# SPDX-License-Identifier: Apache-2.0
"""Linux: BlueZ gives programs the L2CAP channel as a socket, one AAP message per recv()."""
from __future__ import annotations

import logging
import re
import select
import socket
import struct
import subprocess
import time

from . import protocol
from .errors import PodsenseError

log = logging.getLogger("podsense")

SOL_BLUETOOTH, BT_SECURITY, BT_SECURITY_MEDIUM = 274, 4, 2
SOL_L2CAP, L2CAP_OPTIONS = 6, 1
RECEIVE_MTU = 2582           # what an iPhone asks for; some AirPods hide their sensors at the default 672
_DEVICE = re.compile(r"Device ([0-9A-F:]{17}) (.*)")
_MODEL = re.compile(r"Modalias: bluetooth:v004Cp([0-9A-F]{4})")


def bluetoothctl(*args, timeout=5):
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        raise PodsenseError("bluetoothctl not found: install BlueZ (sudo apt install bluez)") from None


def paired(run=bluetoothctl):
    """[(name, address, product id, connected)] of the paired Apple audio devices.

    `devices Paired` exists since BlueZ 5.65; 5.64 (Ubuntu 22.04) needs the older `paired-devices`.
    """
    lines = []
    for command in (("devices", "Paired"), ("paired-devices",), ("devices",)):
        lines = [m for m in map(_DEVICE.match, (ln.strip() for ln in run(*command).splitlines())) if m]
        if lines:
            break
    found = []
    for m in lines:
        info = run("info", m.group(1))
        model = _MODEL.search(info)
        if model:
            found.append((m.group(2), m.group(1), model.group(1).lower(), "Connected: yes" in info))
    return found


def connected(run=bluetoothctl):
    """[(name, address, product id)] of the connected AirPods; paired ones are connected first if none is."""
    devices = paired(run)
    if devices and not any(d[3] for d in devices):
        for name, address, _product, _ in devices:
            log.info("Connecting %s", name)
            run("connect", address, timeout=20)
        devices = paired(run)
    return [d[:3] for d in devices if d[3]]


def l2cap_socket(address):
    """The connected L2CAP socket to the AirPods' AAP channel."""
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise PodsenseError("this Python was built without Bluetooth sockets (socket.AF_BLUETOOTH): "
                            "use the system Python")
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    try:
        try:                             # an encrypted link, needed for the motion data, and the receive MTU
            s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
            options = bytearray(s.getsockopt(SOL_L2CAP, L2CAP_OPTIONS, 12))
            struct.pack_into("<H", options, 2, RECEIVE_MTU)
            s.setsockopt(SOL_L2CAP, L2CAP_OPTIONS, bytes(options))
        except OSError as e:
            log.debug("socket options: %s", e)   # older kernels: connect anyway
        s.settimeout(8)
        s.connect((address, protocol.PSM))
        s.settimeout(None)
    except OSError as e:
        s.close()
        raise PodsenseError("cannot open the AirPods' channel: %s" % e) from e
    return s


class SocketTransport:
    """A message-per-recv socket: the Linux L2CAP socket, or a stand-in in the tests."""

    settle = 0.0

    def __init__(self, connect=l2cap_socket):
        self._connect = connect
        self._sock = None

    def open(self, device):
        self._sock = self._connect(device.address)
        for packet, pause in ((protocol.HANDSHAKE, 0.3), (protocol.FEATURES, 0.15), (protocol.NOTIFICATIONS, 0.15)):
            self.write(packet)
            time.sleep(pause)

    def read(self, timeout):
        try:
            if not select.select([self._sock], [], [], timeout)[0]:
                return None
            message = self._sock.recv(8192)
        except (OSError, ValueError) as e:
            raise PodsenseError("the AirPods' channel is gone: %s" % e) from e
        if not message:
            raise PodsenseError("the AirPods disconnected")
        return time.monotonic(), message

    def write(self, message):
        try:
            self._sock.send(message)
        except (OSError, AttributeError) as e:
            raise PodsenseError("cannot send to the AirPods: %s" % e) from e

    def close(self):
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()


Transport = SocketTransport
