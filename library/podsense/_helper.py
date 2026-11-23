# SPDX-License-Identifier: Apache-2.0
"""The process that owns the Bluetooth channel:  python -m podsense._helper <address>

IOBluetooth delivers its callbacks on the main thread's run loop, so the channel lives in a process
of its own and podsense.Session talks to it over stdin and stdout (see framing.py). Nothing but
frames may be written to stdout. Ctrl-C in the terminal is left to the parent, which sends "stream
off" and then closes stdin.

Opening the channel. The AirPods answer one AAP session, and while macOS holds it a second channel
stays silent. Each attempt therefore disconnects the AirPods and opens the channel at once, before
macOS is back. The result of the open call says little: a channel can open and stay silent, or
answer the handshake with a stale reply while the link is going down. A channel is accepted only
once it has delivered a motion sample in answer to a stream request written on it.

A session can also go silent later with its channel still open. After STALL_SECONDS without a sensor
message the helper ends with an error and the Session opens a new one.
"""
import ctypes
import os
import queue
import select
import signal
import sys
import threading
import time

from . import protocol
from .framing import Closed, data_frame, read_frame, write_frame

ATTEMPTS = 8
CHECK_INTERVAL_US = 20000          # the stream of the liveness check; the Session then asks for its own
STALL_SECONDS = 10


def emit(frame):
    write_frame(sys.stdout.buffer, frame)


def say(text):
    emit(b"L" + text.encode())


def load():
    """PyObjC, imported late so that its absence is reported as a frame."""
    try:
        import objc
        from Foundation import NSDate, NSDefaultRunLoopMode, NSObject, NSRunLoop
        import IOBluetooth
    except ImportError as e:
        raise OSError("%s (pip install pyobjc-framework-IOBluetooth)" % e) from e

    class Delegate(NSObject, protocols=[objc.protocolNamed("IOBluetoothL2CAPChannelDelegate")]):
        def init(self):
            self = objc.super(Delegate, self).init()
            self.packets = []          # (time.monotonic(), bytes)
            self.closed = False
            return self

        def l2capChannelData_data_length_(self, channel, data, length):
            # PyObjC hands over this void* as an integer, not a buffer
            packet = ctypes.string_at(data, length) if isinstance(data, int) else bytes(data[:length])
            self.packets.append((time.monotonic(), packet))

        def l2capChannelClosed_(self, channel):
            self.closed = True

    def pump(seconds):
        """Run the run loop for `seconds`: this is when the callbacks arrive."""
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))

    def pump_once(limit):
        """Run the run loop until something was handled, `limit` seconds at most."""
        NSRunLoop.currentRunLoop().runMode_beforeDate_(NSDefaultRunLoopMode,
                                                      NSDate.dateWithTimeIntervalSinceNow_(limit))

    return IOBluetooth, Delegate, pump, pump_once


def write(channel, packet):
    status = channel.writeSync_length_(packet, len(packet))
    if status:
        raise OSError("Bluetooth write failed (0x%08x)" % (status & 0xFFFFFFFF))


def start_stream(channel, seq, interval, pump):
    """The stream request, with the activity service switched on for a moment before it.

    The motion service alone does not always start; the activity service wakes the sensors.
    """
    write(channel, protocol.stream_request(seq, protocol.SERVICE_ACTIVITY, 40000))
    pump(0.25)
    write(channel, protocol.stream_request(seq + 1, protocol.SERVICE_MOTION, interval))
    write(channel, protocol.stream_request(seq + 2, protocol.SERVICE_ACTIVITY, 0))


def check(channel, delegate, pump, stopping):
    """None when the channel delivers motion samples, else the reason it is rejected."""
    def wait(seconds, found):
        until = time.monotonic() + seconds
        while time.monotonic() < until and not stopping.is_set():
            pump(0.02)
            if found():
                return True
        return False

    seen = 0

    def motion():
        nonlocal seen
        packets = delegate.packets[seen:]
        seen += len(packets)
        return any(protocol.motion_blocks(m) for _, p in packets for m in protocol.split_messages(p))

    try:
        write(channel, protocol.HANDSHAKE)
        if not wait(1.5, lambda: any(p.startswith(protocol.HANDSHAKE_REPLY) for _, p in delegate.packets)):
            return "no handshake reply"
        for packet in (protocol.FEATURES, protocol.NOTIFICATIONS):
            write(channel, packet)
            pump(0.15)
        motion()                         # samples must come after the request below
        start_stream(channel, 0x10, CHECK_INTERVAL_US, pump)
    except OSError as e:
        return str(e)
    return None if wait(2.5, motion) else "no motion data"


def open_session(device, Delegate, pump, stopping, retained):
    for attempt in range(ATTEMPTS):
        if stopping.is_set():
            break
        delegate = Delegate.alloc().init()
        retained.append(delegate)        # late callbacks of failed attempts need live objects
        say("Reconnecting the AirPods, attempt %d of %d" % (attempt + 1, ATTEMPTS))
        device.closeConnection()
        status, channel = device.openL2CAPChannelSync_withPSM_delegate_(None, protocol.PSM, delegate)
        if channel is not None:
            retained.append(channel)
        if status == 0 and channel is not None:
            reason = check(channel, delegate, pump, stopping)
            if reason is None:
                return channel, delegate
            say("That channel is no use (%s), trying again" % reason)
            channel.closeChannel()
        pump(0.5)
    raise OSError("no channel with motion data after %d attempts: are the AirPods out of their case, "
                  "and not in use by another device?" % ATTEMPTS)


def main():
    bluetooth, Delegate, pump, pump_once = load()
    commands = queue.Queue()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def receive():
        try:
            # raw reads and a poll that can be cancelled: a buffered reader blocked in a daemon
            # thread can abort the interpreter at shutdown
            with os.fdopen(os.dup(0), "rb", buffering=0) as source:
                while not stopping.is_set():
                    if select.select([source], [], [], 0.1)[0]:
                        commands.put(read_frame(source))
        except OSError:
            stopping.set()

    def send_pending(channel):
        for _ in range(64):
            try:
                write(channel, commands.get_nowait())
            except queue.Empty:
                return

    receiver = threading.Thread(target=receive, daemon=True)
    receiver.start()
    pump(2)                              # IOBluetooth needs a moment before it knows its devices
    device = bluetooth.IOBluetoothDevice.deviceWithAddressString_(sys.argv[1])
    if device is None:
        raise OSError("no Bluetooth device with this address")
    retained = []
    channel = None
    try:
        channel, delegate = open_session(device, Delegate, pump, stopping, retained)
        emit(b"R")
        last_sensor = time.monotonic()
        while not stopping.is_set():
            send_pending(channel)
            pump_once(0.01)
            packets, delegate.packets = delegate.packets, []
            for at, packet in packets:
                for message in protocol.split_messages(packet):
                    if message[:10] == protocol.SENSOR_DATA:
                        last_sensor = at
                    emit(data_frame(at, message))
            if delegate.closed:
                raise OSError("the AirPods disconnected")
            if time.monotonic() - last_sensor > STALL_SECONDS:
                raise OSError("no motion data for %d s on an open channel" % STALL_SECONDS)
    finally:
        stopping.set()
        if channel is not None:
            try:
                send_pending(channel)    # the parent's last words: stream off
                pump(0.05)
            except OSError:
                pass
            channel.closeChannel()
            pump(0.1)
        receiver.join(timeout=0.5)


if __name__ == "__main__":
    try:
        main()
    except Closed:                       # the parent is gone: nobody to tell
        sys.exit(1)
    except OSError as e:
        try:
            emit(b"E" + str(e).encode())
        except OSError:
            pass
        sys.exit(1)
