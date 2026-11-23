# SPDX-License-Identifier: Apache-2.0
"""The part of the AirPods accessory protocol (AAP) needed for motion data.

AAP runs on Bluetooth L2CAP channel PSM 0x1001. Every message after the handshake starts with
`04 00 04 00` and a 16-bit opcode. Motion samples travel in opcode 0x17 messages, which carry a
length and a protobuf-encoded body; the other messages used here have fixed layouts.

Everything in this module is plain Python: no Bluetooth, testable anywhere.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

PSM = 0x1001
HEADER = bytes.fromhex("04000400")
HANDSHAKE = bytes.fromhex("00000400010002000000000000000000")
HANDSHAKE_REPLY = bytes.fromhex("01000400")
FEATURES = bytes.fromhex("040004004d00d700000000000000")
NOTIFICATIONS = bytes.fromhex("040004000f00ffffffff")
SENSOR = HEADER + bytes.fromhex("1700")                    # opcode 0x17: the messages with a length
SENSOR_DATA = bytes.fromhex("04000400170000001000")        # ... of the kind that carries samples

SERVICE_ACTIVITY, SERVICE_MOTION = 0x0E, 0x10
OP_PLACEMENT, OP_STEM = 0x06, 0x19
STEM_KINDS = {0x05: "single", 0x06: "double", 0x07: "triple", 0x08: "long"}
PLACES = {0: "in_ear", 1: "out", 2: "case"}

GYRO_DPS_PER_COUNT = 0.122       # 16-bit, +-4000 deg/s full scale
COUNTS_PER_G = 1000.0


def control(ident, value=0):
    """A settings command (opcode 0x09): one identifier and its value."""
    return HEADER + bytes([0x09, 0x00, ident, value, 0, 0, 0])


STEM_EVENTS_ON = control(0x39, 0x0F)
STEM_EVENTS_OFF = control(0x39, 0x00)


def stream_request(seq, service, interval_us):
    """Ask for reports of `service` every `interval_us` microseconds; 0 stops them."""
    body = bytes([0x08, seq & 0x7F, 0x42, 0x0B, 0x08, service, 0x10, 0x02, 0x1A, 0x05, 0x01]) + \
        struct.pack("<I", interval_us)
    return SENSOR_DATA + struct.pack("<H", len(body)) + body


def interval_us(rate_hz):
    if not 1 <= rate_hz <= 1000:
        raise ValueError("rate must be between 1 and 1000 Hz")
    return int(round(1e6 / rate_hz))


# --------------------------------------------------------------------------- messages in a data callback
def _next_header(data, start):
    """Index of the next `04 00 04 00 xx 00` from `start`, or -1. Every opcode seen is below 0x100."""
    i = data.find(HEADER, start)
    while i != -1 and data[i + 5:i + 6] != b"\x00":
        i = data.find(HEADER, i + 1)
    return i


def split_messages(data):
    """The AAP messages of one Bluetooth data callback.

    macOS delivers messages that arrive together in one piece: several sample messages at high
    rates, or a state change behind another notification. Opcode 0x17 messages are cut by their
    length field. Other messages have none and are cut at the next header; a tail that does not
    start with a header is kept whole.
    """
    start, end = 0, len(data)
    while start < end:
        stop = end
        if data[start:start + 6] == SENSOR and end - start >= 12:
            size = 12 + struct.unpack_from("<H", data, start + 10)[0]
            if start + size <= end:
                stop = start + size
        elif data[start:start + 4] == HEADER or start == 0:
            i = _next_header(data, start + 4)
            if i != -1:
                stop = i
        yield data[start:stop]
        start = stop


# --------------------------------------------------------------------------- samples
def _varint(buf, i):
    value = shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
    raise ValueError("truncated varint")


def _fields(buf):
    """(field number, value) pairs of a protobuf message; length-delimited values as bytes."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            value, i = buf[i:i + n], i + n
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        else:
            return
        yield number, value


def motion_blocks(message):
    """The raw motion reports of one message: usually one, several after a radio hiccup.

    Field 7 of the body repeats {1: service, 3: report}. Only report 1 of the motion service is a
    sample; the activity service, switched on for a moment at stream start, sends 114-byte reports
    of its own.
    """
    if len(message) < 12 or message[:10] != SENSOR_DATA:
        return []
    size = struct.unpack_from("<H", message, 10)[0]
    blocks = []
    try:
        for number, value in _fields(message[12:12 + size]):
            if number != 7 or not isinstance(value, bytes):
                continue
            service = block = None
            for inner, inner_value in _fields(value):
                if inner == 1:
                    service = inner_value
                elif inner == 3:
                    block = inner_value
            if service == SERVICE_MOTION and isinstance(block, bytes) and len(block) >= 50 and block[0] == 1:
                blocks.append(block)
    except ValueError:
        pass
    return blocks


@dataclass(frozen=True, slots=True)
class Sample:
    """One motion sample, in the earbud's own axes.

    timestamp     seconds on the AirPods' clock (arbitrary origin); use it for intervals and rates
    host_time     time.monotonic() when the Mac received it
    gyro          rotation rate, degrees per second
    acceleration  acceleration with gravity removed, in g
    gravity       direction of gravity, in g (length about 1)
    quaternion    orientation (w, x, y, z), body to a gravity-aligned world frame; None until the
                  AirPods have one, which takes a moment after the stream starts
    """
    timestamp: float
    host_time: float
    gyro: tuple
    acceleration: tuple
    gravity: tuple
    quaternion: tuple | None


def decode(block, host_time=0.0):
    """A raw motion report (56 bytes on AirPods Pro 2) -> Sample."""
    nanoseconds = struct.unpack_from("<Q", block, 1)[0]
    qx, qy, qz = (c / 32768.0 for c in struct.unpack_from("<hhh", block, 20))
    rate = struct.unpack_from("<hhh", block, 26)
    accel = struct.unpack_from("<hhh", block, 38)
    grav = struct.unpack_from("<hhh", block, 44)
    n2 = qx * qx + qy * qy + qz * qz             # the report carries x, y, z; w is the positive root
    quaternion = (math.sqrt(max(0.0, 1.0 - n2)), qx, qy, qz) if 0.01 < n2 < 1.01 else None
    return Sample(nanoseconds / 1e9, host_time,
                  tuple(c * GYRO_DPS_PER_COUNT for c in rate),
                  tuple(c / COUNTS_PER_G for c in accel),
                  tuple(c / COUNTS_PER_G for c in grav),
                  quaternion)


# --------------------------------------------------------------------------- events
@dataclass(frozen=True, slots=True)
class StemPress:
    """A squeeze of the stem: kind is 'single', 'double', 'triple' or 'long'."""
    kind: str
    host_time: float


@dataclass(frozen=True, slots=True)
class Placement:
    """Where the two earbuds are: each 'in_ear', 'out' or 'case'."""
    buds: tuple
    host_time: float

    @property
    def in_ear(self):
        return "in_ear" in self.buds


def events(message, host_time=0.0):
    """The Samples, StemPress or Placement carried by one message (none for anything else)."""
    if message[:10] == SENSOR_DATA:
        return [decode(block, host_time) for block in motion_blocks(message)]
    if message[:4] == HEADER and len(message) >= 8 and message[5] == 0:
        if message[4] == OP_PLACEMENT:
            return [Placement(tuple(PLACES.get(b, "unknown") for b in message[6:8]), host_time)]
        if message[4] == OP_STEM:
            return [StemPress(STEM_KINDS.get(message[6], "0x%02x" % message[6]), host_time)]
    return []
