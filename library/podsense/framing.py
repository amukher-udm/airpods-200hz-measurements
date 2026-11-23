# SPDX-License-Identifier: Apache-2.0
"""Frames between a Session and its Bluetooth helper process: a u32 little-endian length, then bytes.

To the helper, a frame is one AAP message to send. From the helper, the first byte says what follows:

    R                    the channel is open and delivers motion samples
    D + f64 time + data  one AAP message and the time.monotonic() of its arrival
    L + text             a progress line for the log
    E + text             the helper failed; it exits after this
"""
import struct

LIMIT = 65536


class Closed(OSError):
    """The other end of the pipe is gone."""


def read_frame(stream):
    def exact(n):
        data = bytearray()
        while len(data) < n:
            chunk = stream.read(n - len(data))
            if not chunk:
                raise Closed("pipe closed")
            data.extend(chunk)
        return bytes(data)

    size = struct.unpack("<I", exact(4))[0]
    if not 1 <= size <= LIMIT:
        raise Closed("invalid frame size %d" % size)
    return exact(size)


def write_frame(stream, data):
    pending = memoryview(struct.pack("<I", len(data)) + data)
    while pending:
        n = stream.write(pending)
        if not n:
            raise Closed("pipe closed")
        pending = pending[n:]
    stream.flush()


def data_frame(at, message):
    return b"D" + struct.pack("<d", at) + message


def parse_data(frame):
    """(arrival time, message) of a D frame."""
    return struct.unpack_from("<d", frame, 1)[0], frame[9:]
