# SPDX-License-Identifier: Apache-2.0
"""macOS: the channel lives in a helper process (_helper.py); this end exchanges frames with it."""
from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import sys
import time
from pathlib import Path

from .errors import PodsenseError
from .framing import Closed, parse_data, read_frame, write_frame

log = logging.getLogger("podsense")

APPLE_VENDOR = "0x004c"
OPEN_TIMEOUT = 150           # the helper tries up to 8 reconnections of about 10 s each


def connected(profile=None):
    """[(name, address, product id)] of the connected Apple audio devices.

    `profile` is the parsed output of `system_profiler SPBluetoothDataType -json`, read here when None.
    """
    if profile is None:
        try:
            out = subprocess.run(["system_profiler", "SPBluetoothDataType", "-json"],
                                 capture_output=True, text=True, timeout=30).stdout
            profile = json.loads(out)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            raise PodsenseError("cannot list Bluetooth devices: %s" % e) from e
    found = []
    for section in profile.get("SPBluetoothDataType", []):
        for entry in section.get("device_connected", []):
            for name, info in entry.items():
                product = str(info.get("device_productID", ""))
                if str(info.get("device_vendorID", "")).lower() == APPLE_VENDOR and product.lower().startswith("0x"):
                    found.append((name, str(info.get("device_address", "")).upper(), product[2:].lower()))
    return found


class Transport:
    """Frames to and from `python -m podsense._helper <address>`."""

    settle = 0.5             # s: the helper's own 50 Hz check stream is still running at first

    def __init__(self, command=None):
        self._command = command
        self._proc = None

    def open(self, device):
        command = self._command or [sys.executable, "-m", "podsense._helper", device.address]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]),
                                                          env.get("PYTHONPATH")]))
        self._proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, env=env)
        deadline = time.monotonic() + OPEN_TIMEOUT
        while True:
            frame = self._frame(max(0.0, deadline - time.monotonic()))
            if frame is None:
                raise PodsenseError("no session after %d s" % OPEN_TIMEOUT)
            if frame[:1] == b"R":
                return
            if frame[:1] != b"D":
                raise PodsenseError("unexpected message from the Bluetooth helper")

    def read(self, timeout):
        frame = self._frame(timeout)
        return parse_data(frame) if frame is not None and frame[:1] == b"D" else None

    def write(self, message):
        try:
            write_frame(self._proc.stdin, message)
        except (OSError, ValueError) as e:
            while self._frame(0.5) is not None:      # its last frame says why: _frame raises with it
                pass
            raise PodsenseError("the Bluetooth helper is gone") from e

    def _frame(self, timeout):
        """One D or R frame from the helper, None after `timeout` seconds. Progress lines go to the log."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                if not select.select([self._proc.stdout], [], [], max(0.0, deadline - time.monotonic()))[0]:
                    return None
                frame = read_frame(self._proc.stdout)
            except (Closed, ValueError) as e:
                raise PodsenseError("the Bluetooth helper ended without a reason") from e
            if frame[:1] == b"E":
                raise PodsenseError(frame[1:].decode(errors="replace"))
            if frame[:1] != b"L":
                return frame
            log.info("%s", frame[1:].decode(errors="replace"))

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()           # end of input: the helper sends what is pending and closes the channel
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
