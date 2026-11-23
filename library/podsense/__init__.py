# SPDX-License-Identifier: Apache-2.0
"""High-rate motion data from AirPods, on macOS, Linux and Windows.

    import podsense

    with podsense.Session(rate_hz=200) as session:
        for sample in session.samples():
            print(sample.timestamp, sample.gyro, sample.gravity)
"""
from .devices import Device, connected
from .errors import PodsenseError
from .protocol import Placement, Sample, StemPress
from .session import Session

__version__ = "0.1.0"
__all__ = ["Session", "Sample", "StemPress", "Placement", "Device", "PodsenseError", "connected"]
