"""hidraw discovery and the two ioctls we need (HIDIOCSFEATURE / HIDIOCGFEATURE).

Discovery reads sysfs only. Nothing is opened until open_device() / HidrawDevice() is called, and
hidraw numbers are never hardcoded: they change on every replug.
"""
import errno
import fcntl
import os
from dataclasses import dataclass
from typing import List, Optional

VENDOR = 0x3367
PID_DONGLE = 0x1970
PID_WIRED = 0x1982

SYSFS_HIDRAW = "/sys/class/hidraw"
DEV_DIR = "/dev"

# Report descriptor fragments: usage page 0xFF01 + usage 2 collection, and Feature reports A1 / A0.
VENDOR_COLLECTION = bytes.fromhex("0601ff0902")
REPORT_A1 = bytes.fromhex("85a1")
REPORT_A0 = bytes.fromhex("85a0")


class AccessDenied(Exception):
    """The hidraw node exists but this user may not open it (udev rule missing or not yet applied)."""


# --- ioctl numbers, derived like <linux/hidraw.h> does with the generic _IOC() macro.
_IOC_NRSHIFT, _IOC_TYPESHIFT, _IOC_SIZESHIFT, _IOC_DIRSHIFT = 0, 8, 16, 30
_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction: int, type_: str, nr: int, size: int) -> int:
    return (direction << _IOC_DIRSHIFT) | (size << _IOC_SIZESHIFT) | (ord(type_) << _IOC_TYPESHIFT) | (nr << _IOC_NRSHIFT)


def HIDIOCSFEATURE(length: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, "H", 0x06, length)


def HIDIOCGFEATURE(length: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, "H", 0x07, length)


@dataclass(frozen=True)
class Candidate:
    path: str          # /dev/hidrawN
    vid: int
    pid: int
    sysname: str       # hidrawN

    @property
    def wired(self) -> bool:
        return self.pid == PID_WIRED


def descriptor_is_config_channel(desc: bytes) -> bool:
    return VENDOR_COLLECTION in desc and REPORT_A1 in desc and REPORT_A0 in desc


def _hid_id(uevent: str) -> Optional[tuple]:
    for line in uevent.splitlines():
        if line.startswith("HID_ID="):
            try:
                _bus, vid, pid = line[7:].split(":")
                return int(vid, 16), int(pid, 16)
            except ValueError:
                return None
    return None


def discover(sysfs: str = SYSFS_HIDRAW, dev_dir: str = DEV_DIR) -> List[Candidate]:
    """hidraw nodes that are the vendor config channel of the mouse (wired first) or its dongle.

    Pure sysfs reads: the device nodes are not opened.
    """
    found: List[Candidate] = []
    try:
        names = sorted(os.listdir(sysfs), key=lambda n: (len(n), n))
    except OSError:
        return found
    for name in names:
        base = os.path.join(sysfs, name, "device")
        try:
            with open(os.path.join(base, "uevent"), "r", errors="replace") as f:
                ident = _hid_id(f.read())
            if ident is None or ident[0] != VENDOR or ident[1] not in (PID_DONGLE, PID_WIRED):
                continue
            with open(os.path.join(base, "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if descriptor_is_config_channel(desc):
            found.append(Candidate(os.path.join(dev_dir, name), ident[0], ident[1], name))
    found.sort(key=lambda c: 0 if c.wired else 1)   # the tool prefers the wired mouse when both exist
    return found


class HidrawDevice:
    """A hidraw node opened for Feature report I/O. Only endgame.EndgameDevice should call this."""

    def __init__(self, path: str):
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except PermissionError as e:
            raise AccessDenied(path) from e
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EPERM):
                raise AccessDenied(path) from e
            raise

    def set_feature(self, data: bytes) -> None:
        buf = bytearray(data)
        fcntl.ioctl(self._fd, HIDIOCSFEATURE(len(buf)), buf, True)

    def get_feature(self, report_id: int, length: int) -> bytes:
        buf = bytearray(length)
        buf[0] = report_id
        n = fcntl.ioctl(self._fd, HIDIOCGFEATURE(length), buf, True)
        return bytes(buf[:n]) if isinstance(n, int) and 0 < n <= length else bytes(buf)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
