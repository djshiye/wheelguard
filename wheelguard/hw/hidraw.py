"""hidraw discovery, the two Feature-report ioctls (HIDIOCSFEATURE / HIDIOCGFEATURE) and plain
report read/write (Logitech HID++ uses output and input reports, not Feature reports).

Discovery reads sysfs only. Nothing is opened until HidrawDevice() is called, and hidraw numbers are
never hardcoded: they change on every replug.
"""
import errno
import fcntl
import os
import select
from dataclasses import dataclass
from typing import List, Optional

from .models import LOGI_LINKS, RAZER_LINKS

VENDOR = 0x3367
PID_DONGLE = 0x1970
PID_WIRED = 0x1982

SYSFS_HIDRAW = "/sys/class/hidraw"
DEV_DIR = "/dev"

# Report descriptor fragments: usage page 0xFF01 + usage 2 collection, and Feature reports A1 / A0.
VENDOR_COLLECTION = bytes.fromhex("0601ff0902")
REPORT_A1 = bytes.fromhex("85a1")
REPORT_A0 = bytes.fromhex("85a0")

# Razer DeathAdder V4 Pro: wired (cable) and wireless (HyperSpeed / HyperPolling dongle). The vendor
# Feature report lives on USB interface 0, like OpenRazer sends it.
RAZER_VENDOR = 0x1532
RAZER_DA_V4_PRO_WIRED = 0x00BE
RAZER_DA_V4_PRO_WIRELESS = 0x00BF
# The rest of the Razer family (models.RAZER_LINKS) talks the same way on the same interface.
RAZER_PIDS = (RAZER_DA_V4_PRO_WIRED, RAZER_DA_V4_PRO_WIRELESS) + tuple(
    p for p in sorted(RAZER_LINKS) if p not in (RAZER_DA_V4_PRO_WIRED, RAZER_DA_V4_PRO_WIRELESS))
RAZER_WIRED = frozenset(p for p, link in RAZER_LINKS.items() if link.wired)

# Logitech G Pro X Superlight (first generation): the mouse as seen through its LIGHTSPEED receiver
# (the kernel's hid-logitech-dj driver gives it its own HID device, PID 4093) and over the cable.
LOGI_VENDOR = 0x046D
LOGI_SUPERLIGHT_WIRELESS = 0x4093
LOGI_SUPERLIGHT_WIRED = 0xC094
# The rest of the Logitech family (models.LOGI_LINKS) talks HID++ the same way.
LOGI_PIDS = (LOGI_SUPERLIGHT_WIRELESS, LOGI_SUPERLIGHT_WIRED) + tuple(
    p for p in sorted(LOGI_LINKS) if p not in (LOGI_SUPERLIGHT_WIRELESS, LOGI_SUPERLIGHT_WIRED))
LOGI_WIRED = frozenset(p for p, link in LOGI_LINKS.items() if link.wired)
# HID++: vendor usage page 0xFF00 with the long (20-byte) report 0x11.
HIDPP_PAGE = bytes.fromhex("0600ff")
HIDPP_LONG = bytes.fromhex("8511")


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
    kind: str = "endgame"       # "endgame" | "razer" | "logitech"

    @property
    def wired(self) -> bool:
        if self.kind == "razer":
            return self.pid in RAZER_WIRED
        if self.kind == "logitech":
            return self.pid in LOGI_WIRED
        return self.pid == PID_WIRED


def descriptor_is_config_channel(desc: bytes) -> bool:
    return VENDOR_COLLECTION in desc and REPORT_A1 in desc and REPORT_A0 in desc


def descriptor_is_hidpp(desc: bytes) -> bool:
    return HIDPP_PAGE in desc and HIDPP_LONG in desc


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


def _interface_number(sysfs: str, name: str) -> int:
    """USB interface number of the HID device behind a hidraw node, or -1 (not USB, or unknown)."""
    try:
        hid_dir = os.path.realpath(os.path.join(sysfs, name, "device"))
        with open(os.path.join(os.path.dirname(hid_dir), "bInterfaceNumber")) as f:
            return int(f.read().strip(), 16)
    except (OSError, ValueError):
        return -1


def _nodes(sysfs: str):
    """(name, vid, pid, descriptor) for every hidraw node, in a stable order. sysfs reads only."""
    try:
        names = sorted(os.listdir(sysfs), key=lambda n: (len(n), n))
    except OSError:
        return
    for name in names:
        base = os.path.join(sysfs, name, "device")
        try:
            with open(os.path.join(base, "uevent"), "r", errors="replace") as f:
                ident = _hid_id(f.read())
            if ident is None:
                continue
            with open(os.path.join(base, "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        yield name, ident[0], ident[1], desc


def discover_razer(sysfs: str = SYSFS_HIDRAW, dev_dir: str = DEV_DIR) -> List[Candidate]:
    """Interface-0 hidraw nodes of a Razer mouse Wheelguard knows (models.RAZER_LINKS), wired first."""
    found = [Candidate(os.path.join(dev_dir, name), vid, pid, name, "razer")
             for name, vid, pid, _desc in _nodes(sysfs)
             if vid == RAZER_VENDOR and pid in RAZER_PIDS and _interface_number(sysfs, name) == 0]
    found.sort(key=lambda c: 0 if c.wired else 1)
    return found


def discover_logitech(sysfs: str = SYSFS_HIDRAW, dev_dir: str = DEV_DIR) -> List[Candidate]:
    """HID++ hidraw nodes of a Logitech mouse Wheelguard knows (models.LOGI_LINKS), wired first."""
    found = [Candidate(os.path.join(dev_dir, name), vid, pid, name, "logitech")
             for name, vid, pid, desc in _nodes(sysfs)
             if vid == LOGI_VENDOR and pid in LOGI_PIDS and descriptor_is_hidpp(desc)]
    found.sort(key=lambda c: 0 if c.wired else 1)
    return found


def discover_all(sysfs: str = SYSFS_HIDRAW, dev_dir: str = DEV_DIR) -> List[Candidate]:
    """Every supported mouse's config channel: Endgame Gear first (the original mouse), then Razer,
    then Logitech. The session talks to the first one."""
    return discover(sysfs, dev_dir) + discover_razer(sysfs, dev_dir) + discover_logitech(sysfs, dev_dir)


class HidrawDevice:
    """A hidraw node opened for report I/O. Only the transports (endgame, razer, hidpp) call this."""

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

    def write(self, data: bytes) -> None:
        """Send an output report (first byte is the report ID)."""
        n = os.write(self._fd, bytes(data))
        if n != len(data):
            raise OSError(errno.EIO, "short write")

    def read(self, timeout: float, size: int = 64) -> Optional[bytes]:
        """The next input report, or None when nothing arrives within `timeout` seconds."""
        r, _w, _x = select.select([self._fd], [], [], max(0.0, timeout))
        if not r:
            return None
        try:
            return os.read(self._fd, size)
        except OSError as ex:
            if ex.errno == errno.EIO:       # hidraw's answer once the device is gone: report it as unplugged
                raise OSError(errno.ENODEV, "No such device") from ex
            raise

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
