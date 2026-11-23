# SPDX-License-Identifier: Apache-2.0
"""Windows: the channel through the kernel driver in windows/aap-driver.

Windows gives programs no L2CAP socket. The driver opens the AirPods' channel (PSM 0x1001, receive
MTU 2582) and publishes one device interface per paired pair; opening it opens the channel, each
ReadFile returns one AAP message and each WriteFile sends one. See docs/windows.md.

Everything except `Channel` and `interface_paths` is plain Python and is tested on any system.
"""
from __future__ import annotations

import collections
import queue
import re
import threading
import time
import uuid

from . import protocol
from .errors import PodsenseError

INTERFACE_GUID = "6d0f5a3e-2b1c-4f7e-9a41-0c3e8b7d5a21"     # windows/aap-driver/common/inc/public.h
READ_SIZE = 4096             # at least the 2582-byte receive MTU
PENDING_READS = 4            # overlapped reads kept posted, so no message waits for Python
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING, FILE_FLAG_OVERLAPPED = 3, 0x40000000
ERROR_IO_PENDING = 997
# \\?\bthenum#{74ec2172-...}_vid&0001004c_pid&2014#8&2b1e3c1f&0&aabbccddeeff_c00000000#{6d0f5a3e-...}
_PRODUCT = re.compile(r"pid&([0-9a-f]{4})", re.I)
_ADDRESS = re.compile(r"&([0-9a-f]{12})_", re.I)


def parse_interface_path(path):
    """(address 'AA:BB:CC:DD:EE:FF' or '', product id 'xxxx' or '0000') of a device interface path."""
    a, p = _ADDRESS.search(path or ""), _PRODUCT.search(path or "")
    address = ":".join(a.group(1)[i:i + 2] for i in range(0, 12, 2)).upper() if a else ""
    return address, (p.group(1).lower() if p else "0000")


def connected(paths=None):
    """[(name, address, product id, interface path)] of the AirPods the driver is attached to."""
    paths = interface_paths() if paths is None else paths
    return [("AirPods", *parse_interface_path(p), p) for p in paths]


def _win32():
    """kernel32 / cfgmgr32 prototypes, built on first use so that the module imports anywhere."""
    if _win32.cache:
        return _win32.cache
    import ctypes
    from ctypes import wintypes as w

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", ctypes.c_ubyte * 8)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    cm = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    lpov = ctypes.POINTER(OVERLAPPED)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
    k.CreateEventW.restype = w.HANDLE
    k.ResetEvent.argtypes = [w.HANDLE]
    k.ResetEvent.restype = w.BOOL
    k.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, lpov]
    k.ReadFile.restype = w.BOOL
    k.WriteFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, lpov]
    k.WriteFile.restype = w.BOOL
    k.GetOverlappedResult.argtypes = [w.HANDLE, lpov, ctypes.POINTER(w.DWORD), w.BOOL]
    k.GetOverlappedResult.restype = w.BOOL
    k.CancelIoEx.argtypes = [w.HANDLE, lpov]
    k.CancelIoEx.restype = w.BOOL
    k.CloseHandle.argtypes = [w.HANDLE]
    k.CloseHandle.restype = w.BOOL
    lpguid = ctypes.POINTER(GUID)
    cm.CM_Get_Device_Interface_List_SizeW.argtypes = [ctypes.POINTER(w.ULONG), lpguid, w.LPCWSTR, w.ULONG]
    cm.CM_Get_Device_Interface_List_SizeW.restype = w.DWORD
    cm.CM_Get_Device_Interface_ListW.argtypes = [lpguid, w.LPCWSTR, ctypes.c_void_p, w.ULONG, w.ULONG]
    cm.CM_Get_Device_Interface_ListW.restype = w.DWORD
    _win32.cache = (ctypes, w, k, cm, OVERLAPPED, GUID)
    return _win32.cache


_win32.cache = None


def interface_paths(guid=INTERFACE_GUID):
    """Device interface paths of the present AirPods (one per pair the driver is attached to)."""
    try:
        ctypes, w, _k, cm, _ov, GUID = _win32()
    except (AttributeError, OSError) as e:
        raise PodsenseError("the Windows transport runs on Windows only: %s" % e) from e
    g = GUID.from_buffer_copy(uuid.UUID(guid).bytes_le)
    size = w.ULONG(0)
    if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(g), None, 0) or size.value <= 1:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cm.CM_Get_Device_Interface_ListW(ctypes.byref(g), None, buf, size.value, 0):
        return []
    return [p for p in buf[:size.value].split("\0") if p]


class Channel:
    """The driver handle: read() blocks for one message, write() sends one. Overlapped I/O, so a
    blocked read never holds a write back. Reads are consumed strictly in the order they were posted."""

    def __init__(self, path):
        ctypes, w, k, _cm, OVERLAPPED, _g = _win32()
        self._ct, self._w, self._k = ctypes, w, k
        h = k.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if h is None or h == w.HANDLE(-1).value:
            err = ctypes.get_last_error()
            raise OSError(err, "cannot open the AirPods' channel (Windows error %d): are they connected, "
                               "and is no other program using them?" % err)
        self.h = h
        self._wov = OVERLAPPED()
        self._wov.hEvent = k.CreateEventW(None, True, False, None)
        self._reads = collections.deque()
        for _ in range(PENDING_READS):
            ov = OVERLAPPED()
            ov.hEvent = k.CreateEventW(None, True, False, None)
            rd = (ov, ctypes.create_string_buffer(READ_SIZE))
            self._post(rd)
            self._reads.append(rd)

    def _post(self, rd):
        ov, buf = rd
        self._k.ResetEvent(ov.hEvent)
        if not self._k.ReadFile(self.h, buf, READ_SIZE, None, self._ct.byref(ov)):
            err = self._ct.get_last_error()
            if err != ERROR_IO_PENDING:
                raise OSError(err, "ReadFile failed (Windows error %d)" % err)

    def read(self):
        rd = self._reads.popleft()                     # oldest posted read = next message
        n = self._w.DWORD(0)
        if not self._k.GetOverlappedResult(self.h, self._ct.byref(rd[0]), self._ct.byref(n), True):
            err = self._ct.get_last_error()
            raise OSError(err, "the AirPods' channel closed (Windows error %d)" % err)
        data = rd[1].raw[:n.value]
        self._post(rd)
        self._reads.append(rd)
        return data

    def write(self, message):
        n = self._w.DWORD(0)
        self._k.ResetEvent(self._wov.hEvent)
        ok = self._k.WriteFile(self.h, bytes(message), len(message), None, self._ct.byref(self._wov))
        if not ok and self._ct.get_last_error() == ERROR_IO_PENDING:
            ok = self._k.GetOverlappedResult(self.h, self._ct.byref(self._wov), self._ct.byref(n), True)
        if not ok:
            err = self._ct.get_last_error()
            raise OSError(err, "WriteFile failed (Windows error %d)" % err)

    def close(self):
        if self.h:
            self._k.CancelIoEx(self.h, None)           # wakes the blocked read
            self._k.CloseHandle(self.h)
            self.h = None


def raise_timer_resolution():
    """Ask for 1 ms scheduling instead of Windows' default 15.6 ms, for the life of the process."""
    try:
        import ctypes
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
    except (AttributeError, OSError):
        pass


class Transport:
    """A reader thread on the driver handle; read() takes its messages from a queue."""

    settle = 0.0

    def __init__(self, channel=Channel):
        self._open_channel = channel
        self._channel = None
        self._inbox = queue.Queue()

    def open(self, device):
        raise_timer_resolution()
        try:
            self._channel = self._open_channel(device.handle)
        except OSError as e:
            raise PodsenseError(str(e)) from e
        self._inbox = queue.Queue()
        threading.Thread(target=self._pump, args=(self._channel, self._inbox), daemon=True,
                         name="podsense-read").start()
        for packet, pause in ((protocol.HANDSHAKE, 0.3), (protocol.FEATURES, 0.15), (protocol.NOTIFICATIONS, 0.15)):
            self.write(packet)
            time.sleep(pause)

    @staticmethod
    def _pump(channel, inbox):
        try:
            while True:
                message = channel.read()
                if message:
                    inbox.put((time.monotonic(), message))
        except OSError as e:
            inbox.put(e)

    def read(self, timeout):
        try:
            item = self._inbox.get(timeout=timeout) if timeout > 0 else self._inbox.get_nowait()
        except queue.Empty:
            return None
        if isinstance(item, OSError):
            self._inbox.put(item)                      # every later read fails the same way
            raise PodsenseError(str(item)) from item
        return item

    def write(self, message):
        try:
            self._channel.write(message)
        except (OSError, AttributeError) as e:
            raise PodsenseError("cannot send to the AirPods: %s" % e) from e

    def close(self):
        channel, self._channel = self._channel, None
        if channel is not None:
            channel.close()
