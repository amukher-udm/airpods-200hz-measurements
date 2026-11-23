# SPDX-License-Identifier: Apache-2.0
"""A motion session with the AirPods.

    with podsense.Session(rate_hz=200) as session:
        for sample in session.samples():
            print(sample.timestamp, sample.gyro)

The same on macOS, Linux and Windows. What differs is how the AirPods' channel is reached: each
system has a transport (_macos.py, _linux.py, _windows.py) with open, read, write and close; this
class asks for the stream and turns what comes back into Sample, StemPress and Placement objects.
"""
from __future__ import annotations

import logging
import time

from . import devices, protocol
from .errors import PodsenseError

log = logging.getLogger("podsense")

STEM_REFRESH = 5.0           # s: on macOS the AirPods forget the request for stem events, so it is repeated
RESTART_AFTER = 4.0          # s without a sample before the stream is asked for again


class Session:
    """Motion samples from the connected AirPods at `rate_hz` (about 200 at most).

    address      Bluetooth address when several AirPods are connected; the first one otherwise
    stem_events  also report squeezes of the stem. While this is on, the AirPods send presses here.
    reconnect    open a new session when this one is lost, instead of raising PodsenseError

    On macOS opening takes 10 to 25 seconds and interrupts sound from the AirPods for about that long.
    """

    def __init__(self, rate_hz=200.0, address=None, stem_events=False, reconnect=True,
                 _transport=None, _device=None):
        self.interval_us = protocol.interval_us(rate_hz)
        self.address = address
        self.stem_events = stem_events
        self.reconnect = reconnect
        self.device = _device
        self.placement = None            # the last Placement received
        self._transport = _transport     # tests pass a stand-in, with a device
        self._link = None
        self._seq = 0x40
        self._closed = False
        self._pending = []               # events that arrived while opening
        self._last_sample = self._last_request = self._next_stem = 0.0

    # ---- opening and closing
    def open(self):
        """Connect. Called by `with` and by the first iteration; raises PodsenseError on failure."""
        if self._link is not None:
            return self
        self._closed = False
        if self._transport is None:
            self.device = devices.find(self.address)
            log.info("Opening a session with %s (%s)", self.device.name, self.device.model)
        link = self._transport or devices.transport()
        try:
            link.open(self.device)
            self._link = link
            self._request_stream()
            if self.stem_events:
                self._link.write(protocol.STEM_EVENTS_ON)
            settled = time.monotonic() + link.settle
            while time.monotonic() < settled:    # keep what is not a sample: the earbuds' placement
                self._pending += [e for e in self._read(settled - time.monotonic())
                                  if not isinstance(e, protocol.Sample)]
            self._next_stem = time.monotonic() + STEM_REFRESH
            self._last_sample = time.monotonic()
        except BaseException:
            self._link = None
            link.close()
            raise
        log.info("Session open, asking for %.0f Hz", 1e6 / self.interval_us)
        return self

    def close(self):
        """Stop the stream and give the AirPods back. Safe to call twice."""
        self._closed = True
        link, self._link = self._link, None
        if link is None:
            return
        try:
            link.write(protocol.stream_request(self._next_seq(), protocol.SERVICE_MOTION, 0))
            if self.stem_events:
                link.write(protocol.STEM_EVENTS_OFF)
            time.sleep(0.05)             # let them leave before the channel closes
        except PodsenseError:
            pass
        link.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ---- reading
    def events(self):
        """Every Sample, StemPress and Placement, in order of arrival, until close()."""
        backoff = 2.0
        while not self._closed:
            try:
                self.open()
                backoff = 2.0
                found = self._read(0.5)
                self._housekeeping()
            except PodsenseError as e:
                if self._closed:
                    return
                link, self._link = self._link, None
                if link is not None:
                    link.close()
                if not self.reconnect:
                    raise
                log.warning("Session lost (%s); opening a new one in %.0f s", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 20.0)
                continue
            while self._pending:
                yield self._pending.pop(0)
            yield from found

    __iter__ = events

    def samples(self):
        """Only the motion samples."""
        for event in self.events():
            if isinstance(event, protocol.Sample):
                yield event

    def set_rate(self, rate_hz):
        """Change the sample rate of the running stream."""
        self.interval_us = protocol.interval_us(rate_hz)
        if self._link is not None:
            self._request_stream()

    # ---- internals
    def _read(self, timeout):
        """The events of the next message, [] when none came within `timeout` seconds."""
        item = self._link.read(max(0.0, timeout))
        if item is None:
            return []
        found = protocol.events(item[1], item[0])
        for event in found:
            if isinstance(event, protocol.Sample):
                self._last_sample = time.monotonic()
            elif isinstance(event, protocol.Placement):
                self.placement = event
        return found

    def _next_seq(self):
        self._seq = (self._seq + 1) & 0x7F
        return self._seq

    def _request_stream(self):
        """The stream request, with the activity service switched on for a moment before it.

        The motion service alone does not always start; the activity service wakes the sensors.
        """
        self._link.write(protocol.stream_request(self._next_seq(), protocol.SERVICE_ACTIVITY, 40000))
        time.sleep(0.25)
        self._link.write(protocol.stream_request(self._next_seq(), protocol.SERVICE_MOTION, self.interval_us))
        self._link.write(protocol.stream_request(self._next_seq(), protocol.SERVICE_ACTIVITY, 0))
        self._last_request = time.monotonic()

    def _housekeeping(self):
        now = time.monotonic()
        if self.stem_events and now >= self._next_stem:
            self._next_stem = now + STEM_REFRESH
            self._link.write(protocol.STEM_EVENTS_ON)
        if now - self._last_sample > RESTART_AFTER and now - self._last_request > RESTART_AFTER:
            log.info("No samples for %.0f s, asking for the stream again", now - self._last_sample)
            self._request_stream()
