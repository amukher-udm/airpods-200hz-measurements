# SPDX-License-Identifier: Apache-2.0
"""Which AirPods this computer can reach, and the transport to reach them with."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from .errors import PodsenseError

MODELS = {"2013": "AirPods (3rd generation)", "2014": "AirPods Pro (2nd generation)",
          "2027": "AirPods Pro (3rd generation)"}


@dataclass(frozen=True, slots=True)
class Device:
    name: str
    address: str                 # AA:BB:CC:DD:EE:FF
    product_id: str              # four hex digits, e.g. "2014"
    handle: str = ""             # Windows: the driver's device interface path

    @property
    def model(self):
        return MODELS.get(self.product_id, "Apple audio device 0x" + self.product_id)


def _platform(platform=None):
    """The module for this system: connected() and Transport."""
    platform = platform or sys.platform
    if platform == "darwin":
        from . import _macos as module
    elif platform.startswith("linux"):
        from . import _linux as module
    elif platform == "win32":
        from . import _windows as module
    else:
        raise PodsenseError("podsense runs on macOS, Linux and Windows, not on %s" % platform)
    return module


def connected():
    """The AirPods connected to this computer."""
    return [Device(*fields) for fields in _platform().connected()]


def transport():
    return _platform().Transport()


def find(address=None, devices=None):
    """The device with this address, or the first connected one."""
    devices = connected() if devices is None else devices
    if not devices:
        raise PodsenseError("no AirPods connected to this computer")
    if address is None:
        return devices[0]
    for device in devices:
        if device.address == address.upper().replace("-", ":"):
            return device
    raise PodsenseError("AirPods %s are not connected to this computer" % address)
