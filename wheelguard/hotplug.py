"""Notice new input devices at once instead of only on the next periodic search.

While no mouse is connected the daemon waits `retry_s` seconds between searches. With this watcher it
also wakes up as soon as something changes in /dev/input: a node is created (mouse plugged in, woken
up or reconnected through its dongle) or its permissions change (udev grants the logged-in user access
a moment after creating the node). So protection resumes right away, and the periodic search remains
only as a fallback.

Uses the kernel's inotify through ctypes: no extra dependency, nothing opened but the directory watch.
If inotify isn't available, `DeviceWatcher.open()` returns None and the daemon simply keeps polling.
"""
import ctypes
import ctypes.util
import logging
import os
import struct

log = logging.getLogger("wheelguard")

INPUT_DIR = "/dev/input"
IN_ATTRIB = 0x00000004
IN_CREATE = 0x00000100
IN_MOVED_TO = 0x00000080
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
_EVENT = struct.Struct("iIII")      # wd, mask, cookie, len (then `len` bytes of name)


def _libc():
    name = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(name, use_errno=True)


class DeviceWatcher:
    def __init__(self, fd):
        self.fd = fd

    @classmethod
    def open(cls, path=INPUT_DIR, libc=None):
        """A watcher on `path`, or None if inotify can't be used here."""
        try:
            libc = libc or _libc()
            fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
            if fd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1 failed")
            wd = libc.inotify_add_watch(fd, os.fsencode(path), IN_CREATE | IN_ATTRIB | IN_MOVED_TO)
            if wd < 0:
                err = ctypes.get_errno()
                os.close(fd)
                raise OSError(err, f"cannot watch {path}")
        except (OSError, AttributeError) as ex:
            log.info("no instant device detection (%s); searching every few seconds instead", ex)
            return None
        return cls(fd)

    def fileno(self):
        return self.fd

    def drain(self):
        """Read pending notifications; True if any concerns an event node (eventN)."""
        hit = False
        while True:
            try:
                data = os.read(self.fd, 4096)
            except (BlockingIOError, InterruptedError):
                return hit
            except OSError:
                return hit
            if not data:
                return hit
            hit = hit or relevant(data)

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def relevant(data):
    """True if the raw inotify records in `data` name an event* node."""
    off = 0
    while off + _EVENT.size <= len(data):
        _wd, _mask, _cookie, length = _EVENT.unpack_from(data, off)
        off += _EVENT.size
        name = data[off:off + length].split(b"\0", 1)[0]
        off += length
        if name.startswith(b"event"):
            return True
    return False
