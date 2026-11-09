"""Logitech HID++ 2.0 transport, limited to what Wheelguard needs. See docs/logitech-protocol.md.

Requests are long reports (0x11, 20 bytes) written to the mouse's HID++ hidraw node; replies are
matched by device index, feature index, function and software id, and everything else on the node
(pointer reports, notifications) is skipped. Device index 0xFF: on the receiver's per-mouse node made
by hid-logitech-dj and on the wired mouse the kernel routes it to the right device, the way libratbag
talks to this mouse. Replies are not matched on the device index: hid-logitech-dj rewrites it to the
mouse's receiver slot (1), and libratbag doesn't check it either.

Every request goes through HidppDevice.request(), the single choke point. It enforces an allow-list
of (feature, function) pairs, the argument rules of each, and the onboard-memory write protocol
(address -> 16-byte chunks -> end, only to sectors the driver unlocked for this write, only while the
write gate is open). Features that could pair, reset, update firmware or change anything else are
not on the list.

Not verified on hardware.
"""
import errno
import time
from typing import Callable, Dict, Optional, Set

from .endgame import LinkError, Rejected, StatusError, WriteBlocked

REPORT_SHORT, REPORT_LONG = 0x10, 0x11
LONG_LEN = 20
SW_ID = 0x0A
DEVICE_INDEX = 0xFF
ERR_20 = 0xFF          # HID++ 2.0 error reply marker (byte 2)
ERR_10 = 0x8F          # HID++ 1.0 error reply (from the receiver: mouse unreachable)
TIMEOUT_S = 1.5

ROOT = 0x0000
FW_VERSION = 0x0003
BATTERY_STATUS = 0x1000
BATTERY_VOLTAGE = 0x1001
UNIFIED_BATTERY = 0x1004
ONBOARD_PROFILES = 0x8100

# Onboard profile functions (libratbag / Solaar names).
OP_GET_INFO, OP_SET_MODE, OP_GET_MODE, OP_SET_PROFILE, OP_GET_PROFILE = 0x0, 0x1, 0x2, 0x3, 0x4
OP_MEM_READ, OP_MEM_ADDR_WRITE, OP_MEM_WRITE, OP_MEM_WRITE_END = 0x5, 0x6, 0x7, 0x8
OP_GET_DPI_INDEX, OP_SET_DPI_INDEX = 0xB, 0xC
MODE_ONBOARD = 0x01

ALLOWED: Dict[int, Set[int]] = {
    ROOT: {0x0, 0x1},                       # getFeature, ping
    FW_VERSION: {0x0, 0x1},                 # entity count, entity info
    BATTERY_STATUS: {0x0},
    BATTERY_VOLTAGE: {0x0},
    UNIFIED_BATTERY: {0x1},                 # getStatus
    ONBOARD_PROFILES: {OP_GET_INFO, OP_SET_MODE, OP_GET_MODE, OP_SET_PROFILE, OP_GET_PROFILE, OP_MEM_READ,
                       OP_MEM_ADDR_WRITE, OP_MEM_WRITE, OP_MEM_WRITE_END, OP_GET_DPI_INDEX, OP_SET_DPI_INDEX},
}
WRITE_FNS = {OP_SET_MODE, OP_SET_PROFILE, OP_MEM_ADDR_WRITE, OP_MEM_WRITE, OP_MEM_WRITE_END, OP_SET_DPI_INDEX}


class HidppDevice:
    """One open HID++ hidraw node. `dev` provides write(bytes) and read(timeout) -> bytes | None."""

    def __init__(self, dev, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = TIMEOUT_S):
        self._dev = dev
        self._sleep = sleep
        self._clock = clock
        self._timeout = timeout
        self._index: Dict[int, int] = {ROOT: 0}     # feature id -> feature index on this device
        self._ids: Dict[int, int] = {0: ROOT}
        self.write_enabled = False                  # opened by the driver once the mouse is identified
        self.sector_size = 0
        self.writable_sectors: Set[int] = set()      # unlocked by the driver for one apply
        self._writing: Optional[int] = None          # chunks still expected after an address write

    # ---- feature discovery
    def feature(self, fid: int) -> int:
        """Feature index of `fid`, 0 when the mouse doesn't have it."""
        if fid in self._index:
            return self._index[fid]
        if fid not in ALLOWED:
            raise Rejected(f"feature {fid:#06x} is not on the allow-list")
        r = self.request(ROOT, 0x0, bytes((fid >> 8, fid & 0xFF)))
        idx = r[0]
        self._index[fid] = idx
        if idx:
            self._ids[idx] = fid
        return idx

    def has(self, fid: int) -> bool:
        return self.feature(fid) != 0

    # ---- the choke point
    def _check(self, fid: int, fn: int, params: bytes) -> None:
        if fid not in ALLOWED or fn not in ALLOWED[fid]:
            raise Rejected(f"HID++ feature {fid:#06x} function {fn} is not on the allow-list")
        if len(params) > 16:
            raise Rejected("HID++ parameters are at most 16 bytes")
        p = params.ljust(16, b"\x00")
        if fid == ROOT and fn == 0x0:
            if ((p[0] << 8) | p[1]) not in ALLOWED or any(p[2:]):
                raise Rejected("getFeature only for features on the allow-list")
        elif fid == ROOT and fn == 0x1:
            if p[0] or p[1] or any(p[3:]):
                raise Rejected("ping takes only the ping byte")
        if fid != ONBOARD_PROFILES or fn not in WRITE_FNS:
            if self._writing is not None:
                raise Rejected("a memory write is in progress")
            return
        if not self.write_enabled:
            raise WriteBlocked("writes are disabled: the mouse has not been identified as a G Pro X Superlight")
        if fn == OP_SET_MODE:
            if p[0] != MODE_ONBOARD or any(p[1:]):
                raise Rejected("only switching to onboard mode is allowed")
        elif fn == OP_SET_PROFILE:
            sector = (p[0] << 8) | p[1]
            if sector not in self.writable_sectors or sector == 0 or any(p[2:]):
                raise Rejected(f"selecting profile sector {sector:#06x} is not allowed")
        elif fn == OP_SET_DPI_INDEX:
            if p[0] > 4 or any(p[1:]):
                raise Rejected("DPI index must be 0-4")
        elif fn == OP_MEM_ADDR_WRITE:
            sector, offset, count = (p[0] << 8) | p[1], (p[2] << 8) | p[3], (p[4] << 8) | p[5]
            if self._writing is not None:
                raise Rejected("a memory write is already in progress")
            if sector not in self.writable_sectors:
                raise Rejected(f"writing sector {sector:#06x} is not allowed")
            if offset or count != self.sector_size or not self.sector_size or any(p[6:]):
                raise Rejected("only whole-sector writes are allowed")
        elif fn == OP_MEM_WRITE:
            if not self._writing:
                raise Rejected("memory data without an address write")
        elif fn == OP_MEM_WRITE_END:
            if self._writing != 0 or any(p):
                raise Rejected("memory write ended early")

    def request(self, fid: int, fn: int, params: bytes = b"") -> bytes:
        """Send one request and return the 16 parameter bytes of its reply. Raises Rejected (nothing
        sent), StatusError (the mouse answered with an error) or LinkError (no answer)."""
        self._check(fid, fn, params)
        idx = self._index.get(fid)
        if idx is None or (idx == 0 and fid != ROOT):
            raise Rejected(f"the mouse has no feature {fid:#06x}")
        fsw = (fn << 4) | SW_ID
        msg = bytes((REPORT_LONG, DEVICE_INDEX, idx, fsw)) + bytes(params).ljust(16, b"\x00")
        try:
            self._dev.write(msg)
        except OSError as e:
            raise LinkError(e.errno or 0, "HID++ write") from e
        self._track(fid, fn, params)
        deadline = self._clock() + self._timeout
        while True:
            left = deadline - self._clock()
            if left <= 0:
                raise LinkError(errno.ETIMEDOUT, "HID++ reply")
            try:
                r = self._dev.read(left)
            except OSError as e:
                raise LinkError(e.errno or 0, "HID++ read") from e
            if r is None:
                raise LinkError(errno.ETIMEDOUT, "HID++ reply")
            r = bytes(r)
            if len(r) < 7 or r[0] not in (REPORT_SHORT, REPORT_LONG):
                continue
            if r[2] == ERR_20 and r[3] == idx and r[4] == fsw:
                self._writing = None
                raise StatusError((fid << 4) | fn, r[5])
            if r[2] == ERR_10 and r[3] == idx and r[4] == fsw:
                self._writing = None
                raise LinkError(errno.EHOSTUNREACH, f"receiver error {r[5]:#04x}")
            if r[2] == idx and r[3] == fsw:
                return r[4:].ljust(16, b"\x00")[:16]

    def _track(self, fid: int, fn: int, params: bytes) -> None:
        if fid != ONBOARD_PROFILES:
            return
        if fn == OP_MEM_ADDR_WRITE:
            self._writing = (self.sector_size + 15) // 16
        elif fn == OP_MEM_WRITE and self._writing:
            self._writing -= 1
        elif fn == OP_MEM_WRITE_END:
            self._writing = None

    # ---- reads
    def ping(self) -> None:
        r = self.request(ROOT, 0x1, bytes((0, 0, 0x5A)))
        if r[2] != 0x5A:
            raise LinkError(errno.EPROTO, "HID++ ping")

    def firmware(self) -> str:
        if not self.has(FW_VERSION):
            return ""
        count = self.request(FW_VERSION, 0x0)[0]
        for i in range(min(count, 8)):
            r = self.request(FW_VERSION, 0x1, bytes((i,)))
            if r[0] & 0x0F == 0:          # main application firmware
                prefix = bytes(r[1:4]).decode("ascii", "replace").strip("\x00 ")
                return f"{prefix} {r[4]:02X}.{r[5]:02X}.B{(r[6] << 8) | r[7]:04X}".strip()
        return ""

    def battery(self):
        """(percent, charging or None), or None when the mouse reports no battery feature."""
        if self.has(UNIFIED_BATTERY):
            r = self.request(UNIFIED_BATTERY, 0x1)
            return min(100, r[0]), r[2] in (1, 2, 4)
        if self.has(BATTERY_STATUS):
            r = self.request(BATTERY_STATUS, 0x0)
            return min(100, r[0]), r[2] in (1, 2, 4)
        if self.has(BATTERY_VOLTAGE):
            r = self.request(BATTERY_VOLTAGE, 0x0)
            return voltage_percent((r[0] << 8) | r[1]), bool(r[2] & 0x80)
        return None

    # ---- onboard profiles
    def onboard_info(self) -> bytes:
        return self.request(ONBOARD_PROFILES, OP_GET_INFO)

    def onboard_mode(self) -> int:
        return self.request(ONBOARD_PROFILES, OP_GET_MODE)[0]

    def current_profile(self) -> int:
        r = self.request(ONBOARD_PROFILES, OP_GET_PROFILE)
        return (r[0] << 8) | r[1]

    def dpi_index(self) -> int:
        return self.request(ONBOARD_PROFILES, OP_GET_DPI_INDEX)[0]

    def read_sector(self, sector: int, size: int) -> bytes:
        """Read a whole sector, 16 bytes at a time (the last read is aligned to the end, since the
        firmware refuses reads that run past it)."""
        out = bytearray(size)
        off = 0
        while off < size:
            at = min(off, size - 16)
            r = self.request(ONBOARD_PROFILES, OP_MEM_READ, bytes((sector >> 8, sector & 0xFF, at >> 8, at & 0xFF)))
            out[at:at + 16] = r[:16]
            off = at + 16
        return bytes(out)

    def write_sector(self, sector: int, data: bytes) -> None:
        if len(data) != self.sector_size:
            raise Rejected("sector data has the wrong length")
        n = len(data)
        self.request(ONBOARD_PROFILES, OP_MEM_ADDR_WRITE, bytes((sector >> 8, sector & 0xFF, 0, 0, n >> 8, n & 0xFF)))
        padded = data + b"\xff" * (-n % 16)
        for o in range(0, len(padded), 16):
            self.request(ONBOARD_PROFILES, OP_MEM_WRITE, padded[o:o + 16])
        self.request(ONBOARD_PROFILES, OP_MEM_WRITE_END)

    def set_onboard_mode(self) -> None:
        self.request(ONBOARD_PROFILES, OP_SET_MODE, bytes((MODE_ONBOARD,)))

    def set_current_profile(self, sector: int) -> None:
        self.request(ONBOARD_PROFILES, OP_SET_PROFILE, bytes((sector >> 8, sector & 0xFF)))

    def set_dpi_index(self, index: int) -> None:
        self.request(ONBOARD_PROFILES, OP_SET_DPI_INDEX, bytes((index,)))


# Battery voltage (mV) -> percent, the curve Solaar uses for Logitech's single-cell batteries.
_VOLTAGE_CURVE = ((4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60), (3811, 50), (3778, 40),
                  (3751, 30), (3717, 20), (3671, 10), (3646, 5), (3579, 2), (3500, 0))


def voltage_percent(mv: int) -> int:
    if mv >= _VOLTAGE_CURVE[0][0]:
        return 100
    if mv <= _VOLTAGE_CURVE[-1][0]:
        return 0
    for (v_hi, p_hi), (v_lo, p_lo) in zip(_VOLTAGE_CURVE, _VOLTAGE_CURVE[1:]):
        if v_lo <= mv <= v_hi:
            return round(p_lo + (p_hi - p_lo) * (mv - v_lo) / (v_hi - v_lo))
    return 0


def crc_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), the checksum of onboard profile sectors."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc
