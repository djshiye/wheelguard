"""A fake config channel for developing and testing without hardware.

FakeHidraw implements the same two-method device layer as hidraw.HidrawDevice, but it sits *under*
endgame.EndgameDevice, so every mock run goes through the real allow-list, gate and codec. It is only
ever created when WHEELGUARD_HW_MOCK is set (see session.default_opener) or by tests.

    WHEELGUARD_HW_MOCK=connected|connected-wired|asleep|unsupported|denied|missing
                       razer|razer-wired|razer-asleep
                       razer-classic (a DeathAdder V3 Pro behind its dongle: the classic polling command)
                       razer-viper (a Viper V3 Pro behind its HyperPolling dongle)
                       logitech|logitech-wired|logitech-factory|logitech-asleep|logitech-g502

FakeRazer and FakeLogitech do the same for the other two mice: they sit under razer.RazerDevice and
hidpp.HidppDevice and answer like the real protocol (as documented by OpenRazer, libratbag and Solaar).
"""
import os
from collections import deque
from typing import Deque, Dict, List, Optional

from . import codec
from .hidraw import (AccessDenied, LOGI_SUPERLIGHT_WIRED, LOGI_SUPERLIGHT_WIRELESS, PID_WIRED,
                     RAZER_DA_V4_PRO_WIRED, RAZER_DA_V4_PRO_WIRELESS)

MOCK_ENV = "WHEELGUARD_HW_MOCK"
MODES = ("connected", "connected-wired", "asleep", "unsupported", "denied", "missing",
         "razer", "razer-wired", "razer-asleep", "razer-classic", "razer-viper",
         "logitech", "logitech-wired", "logitech-factory", "logitech-asleep", "logitech-g502")


def default_blob() -> bytes:
    """A plausible settings block: the vendor tool's factory defaults (doc section 4) plus non-zero
    filler in the bytes nothing decodes, so echo-unchanged bugs can't hide behind zeros."""
    b = bytearray(codec.BLOB_LEN)
    for i in range(codec.BLOB_LEN):
        b[i] = (i * 37 + 11) & 0xFF if i >= 0x70 else 0
    b[0x00] = 0x00
    b[0x01] = 0x00          # sensor angle tuning
    b[0x02] = 0x5A          # unknown, echoed
    b[0x03] = 30            # deep sleep: 30 min
    b[0x04] = 5             # sleep: 5 min
    b[0x05] = 0x02          # 4000 Hz
    b[0x06] = 0x01          # flags (slamclick)
    b[0x07] = 0x00
    b[0x08] = 0x01          # LED stays on at lift-off
    b[0x09] = 0x03          # LOD 1.0 mm
    b[0x0A] = 0x00          # angle snapping off
    b[0x0B] = 0x00          # ripple control off
    b[0x0C] = 0x01          # motion sync on
    b[0x0D] = 0x01          # active stage 2
    b[0x0E] = 0x04          # 4 stages
    for i in range(0x0F, 0x23):
        b[i] = (0xA0 + i) & 0xFF      # the never-touched LED area, echoed
    for k, dpi in enumerate((400, 800, 1600, 3200)):
        o = 0x23 + 5 * k
        b[o:o + 5] = bytes((0, dpi & 0xFF, dpi >> 8, dpi & 0xFF, dpi >> 8))
    records = ((0x00, 0x01), (0x00, 0x02), (0x00, 0x04), (0x00, 0x08), (0x00, 0x10),
               (0x09, 0xF1), (0x01, 0x01), (0x01, 0xFF))
    for r, (t, c) in enumerate(records):
        o = 0x37 + 7 * r
        b[o:o + 7] = bytes((t, c, 0, 0, 0, 0, 8))      # filter byte 8 everywhere
    b[0x6F] = 0x00          # glass mode off
    return bytes(b)


class FakeHidraw:
    def __init__(self, mode: str = "connected", blob: Optional[bytes] = None, battery: int = 82,
                 dongle_fw: int = 0x0123, mouse_fw: int = 0x0105, mouse_pid: Optional[int] = None):
        self.mode = mode
        self.wired = mode == "connected-wired"
        self.blob = bytearray(blob if blob is not None else default_blob())
        self.battery = battery
        self.dongle_fw = dongle_fw
        self.mouse_fw = mouse_fw
        self.mouse_pid = mouse_pid if mouse_pid is not None else (0x1983 if mode == "unsupported" else PID_WIRED)
        self.sent: List[bytes] = []      # every frame that reached the device
        self._reply: Optional[bytes] = None

    def close(self):
        pass

    def set_feature(self, data: bytes) -> None:
        data = bytes(data)
        self.sent.append(data)
        cmd = data[1]
        status = 2 if self.mode == "asleep" else 1
        r = bytearray(64)
        r[0], r[1] = 0xA1, status
        if status == 1:
            if cmd == 0x0D:
                r[0x11], r[0x12] = self.dongle_fw & 0xFF, self.dongle_fw >> 8
            elif cmd == 0x0E:
                r[0x12], r[0x13] = self.mouse_pid & 0xFF, self.mouse_pid >> 8
                r[0x17], r[0x18] = self.mouse_fw & 0xFF, self.mouse_fw >> 8
            elif cmd == 0xB4:
                r[0x10] = self.battery
            elif cmd == 0x12:
                big = bytearray(1041)
                big[0], big[1] = 0xA0, 1
                big[0x10:0x410] = self.blob
                r = big
            elif cmd in (0x14, 0x15, 0x16):
                payload = data[0x10:0x10 + {0x14: 28, 0x15: 11, 0x16: 28}[cmd]]
                src = {0x14: codec.SRC_14, 0x15: codec.SRC_15}.get(cmd) or codec.SRC_16[data[6] - 1]
                for i, off in enumerate(src):
                    self.blob[off] = payload[i]
            elif cmd != 0x0F:
                raise AssertionError(f"the mock device received unexpected command {cmd:#04x}")
        self._reply = bytes(r)

    def get_feature(self, report_id: int, length: int) -> bytes:
        if self._reply is None:
            raise OSError(5, "no pending reply")
        r = self._reply
        if r[0] != report_id:
            raise OSError(5, "reply report mismatch")
        return r


class FakeRazer:
    """A DeathAdder V4 Pro behind razer.RazerDevice: Feature report 0 on interface 0."""

    def __init__(self, mode: str = "razer"):
        self.mode = mode
        self.wired = mode == "razer-wired"
        self.stages = [(400, 400), (800, 800), (1600, 1600), (3200, 3200)]
        self.active = 2                 # 1-based, as the mouse reports it
        self.classic = mode == "razer-classic"      # DeathAdder V3 Pro: 00:85 / 00:05, codes 1 / 2 / 8
        self.pid = 0x00B7 if self.classic else 0x00C1 if mode == "razer-viper" else (
            RAZER_DA_V4_PRO_WIRED if self.wired else RAZER_DA_V4_PRO_WIRELESS)
        self.fw = (2, 1) if self.classic else (1, 3)
        self.polling = 0x01 if self.classic else 0x08      # 1000 Hz
        self.idle = 300                 # seconds
        self.low = 0x26
        self.level = 204                # of 255 -> 80%
        self.sent: List[bytes] = []
        self._reply: Optional[bytes] = None

    def close(self):
        pass

    def set_feature(self, data: bytes) -> None:
        data = bytes(data)
        assert len(data) == 91 and data[0] == 0, "Razer reports are report 0 + 90 bytes"
        r = bytearray(data[1:])
        self.sent.append(bytes(r))
        cls, cid, a = r[6], r[7], bytearray(80)
        status = 0x02
        if self.mode == "razer-asleep" and (cls, cid) != (0x00, 0x81):
            status = 0x04               # the dongle answers, the mouse doesn't
        elif (cls, cid) == (0x00, 0x81):
            a[0], a[1] = self.fw
        elif (cls, cid) == (0x04, 0x86):
            a[0], a[1], a[2] = 0x01, self.active, len(self.stages)
            for i, (x, y) in enumerate(self.stages):
                a[3 + 7 * i:10 + 7 * i] = bytes((i + 1, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0))
        elif (cls, cid) == (0x00, 0xC0) and not self.classic:
            a[1] = self.polling
        elif (cls, cid) == (0x00, 0x85) and self.classic:
            a[0] = self.polling
        elif (cls, cid) == (0x00, 0x05) and self.classic:
            self.polling = r[8]
        elif (cls, cid) == (0x07, 0x83):
            a[0], a[1] = self.idle >> 8, self.idle & 0xFF
        elif (cls, cid) == (0x07, 0x81):
            a[0] = self.low
        elif (cls, cid) == (0x07, 0x80):
            a[1] = self.level
        elif (cls, cid) == (0x07, 0x84):
            a[1] = 1 if self.wired else 0
        elif (cls, cid) == (0x04, 0x06):
            n = r[8 + 2]
            self.active = r[8 + 1]
            self.stages = [((r[8 + 4 + 7 * i] << 8) | r[8 + 5 + 7 * i], (r[8 + 6 + 7 * i] << 8) | r[8 + 7 + 7 * i])
                           for i in range(n)]
        elif (cls, cid) == (0x04, 0x05):
            pass
        elif (cls, cid) == (0x00, 0x40) and not self.classic:
            self.polling = r[8 + 1]
        elif (cls, cid) == (0x07, 0x03):
            self.idle = (r[8] << 8) | r[9]
        elif (cls, cid) == (0x07, 0x01):
            self.low = r[8]
        else:
            raise AssertionError(f"the mock Razer received unexpected command {cls:02x}:{cid:02x}")
        r[0] = status
        if status == 0x02 and cid & 0x80:
            r[8:88] = a
        c = 0
        for b in r[2:88]:
            c ^= b
        r[88] = c
        self._reply = b"\x00" + bytes(r)

    def get_feature(self, report_id: int, length: int) -> bytes:
        if self._reply is None:
            raise OSError(5, "no pending reply")
        return self._reply


def _crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _sealed(sector: bytearray) -> bytes:
    c = _crc(bytes(sector[:-2]))
    sector[-2], sector[-1] = c >> 8, c & 0xFF
    return bytes(sector)


def superlight_profile(size: int = 255, dpis=(400, 800, 1600, 3200, 0), rate_ms: int = 1, default: int = 1) -> bytes:
    """A plausible onboard profile: real header fields, non-zero filler where buttons, name and
    lighting live (so echo-unchanged bugs can't hide behind zeros), and a valid CRC."""
    p = bytearray((i * 29 + 7) & 0xFF for i in range(size))
    p[0], p[1], p[2] = rate_ms, default, 0xFF
    for k, d in enumerate(dpis):
        p[3 + 2 * k], p[4 + 2 * k] = d & 0xFF, d >> 8
    return _sealed(p)


class FakeLogitech:
    """A G Pro X Superlight behind hidpp.HidppDevice: HID++ 2.0 long reports on its hidraw node."""

    FEATURES = {0x0000: 0, 0x0001: 1, 0x0003: 2, 0x0005: 3, 0x1001: 4, 0x2201: 5, 0x8060: 6, 0x8100: 7}

    def __init__(self, mode: str = "logitech", size: int = 255):
        self.mode = mode
        self.wired = mode == "logitech-wired"
        self.size = size
        self.sector_count = 8
        self.onboard = 1
        self.sectors: Dict[int, bytes] = {
            0x0101: superlight_profile(size, (800, 1600, 3200, 0, 0), 1, 0),
        }
        if mode == "logitech-factory":
            self.current = 0x0101        # no directory yet: reading sector 0 fails until one is written
        else:
            d = bytearray(b"\xff" * size)
            d[0:8] = bytes((0x00, 0x01, 0x01, 0x00, 0xFF, 0xFF, 0x00, 0x00))
            self.sectors[0x0000] = _sealed(d)
            self.sectors[0x0001] = superlight_profile(size)
            self.current = 0x0001
        if mode == "logitech-g502":         # a G502 Lightspeed (read-only family model) through its receiver
            self.pid = 0x407F
            self.sectors[0x0001] = superlight_profile(size, (400, 800, 1600, 3200, 25600), 1)
        else:
            self.pid = LOGI_SUPERLIGHT_WIRED if self.wired else LOGI_SUPERLIGHT_WIRELESS
        self.dpi_index = 1
        self.millivolts = 3900
        self.sent: List[bytes] = []
        self.writes: List[int] = []          # sectors written, in order
        self._out: Deque[bytes] = deque()
        self._wr: Optional[list] = None

    def close(self):
        pass

    def _reply(self, req: bytes, params: bytes) -> None:
        self._out.append(bytes((0x02, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)))   # pointer noise
        self._out.append(req[:4] + params.ljust(16, b"\x00")[:16])

    def _error(self, req: bytes, code: int) -> None:
        self._out.append(bytes((0x11, req[1], 0xFF, req[2], req[3], code)) + bytes(14))

    def write(self, data: bytes) -> None:
        req = bytes(data)
        assert len(req) == 20 and req[0] == 0x11 and req[1] == 0xFF, "HID++ long reports to index 0xFF only"
        self.sent.append(req)
        if not self.wired:      # hid-logitech-dj rewrites the index to the mouse's receiver slot
            req = req[:1] + b"\x01" + req[2:]
        if self.mode == "logitech-asleep":
            self._out.append(bytes((0x10, 0xFF, 0x8F, req[2], req[3], 0x09, 0x00)))
            return
        idx, fn, p = req[2], req[3] >> 4, req[4:]
        fid = {v: k for k, v in self.FEATURES.items()}.get(idx)
        if fid == 0x0000 and fn == 0:
            self._reply(req, bytes((self.FEATURES.get((p[0] << 8) | p[1], 0),)))
        elif fid == 0x0000 and fn == 1:
            self._reply(req, bytes((4, 2, p[2])))
        elif fid == 0x0003 and fn == 0:
            self._reply(req, bytes((1,)))
        elif fid == 0x0003 and fn == 1:
            self._reply(req, bytes((0x00,)) + b"MPM" + bytes((0x25, 0x01, 0x00, 0x10)))
        elif fid == 0x1001 and fn == 0:
            mv = self.millivolts
            self._reply(req, bytes((mv >> 8, mv & 0xFF, 0x80 if self.wired else 0x00)))
        elif fid == 0x8100:
            self._onboard(req, fn, p)
        else:
            raise AssertionError(f"the mock Superlight received unexpected request {req.hex()}")

    def _onboard(self, req: bytes, fn: int, p: bytes) -> None:
        if fn == 0x0:
            self._reply(req, bytes((1, 3, 1, 5, 1, 5, self.sector_count, self.size >> 8, self.size & 0xFF, 0x00, 0x02)))
        elif fn == 0x1:
            self.onboard = p[0]
            self._reply(req, b"")
        elif fn == 0x2:
            self._reply(req, bytes((self.onboard,)))
        elif fn == 0x3:
            self.current = (p[0] << 8) | p[1]
            self._reply(req, b"")
        elif fn == 0x4:
            self._reply(req, bytes((self.current >> 8, self.current & 0xFF)))
        elif fn == 0x5:
            sector, off = (p[0] << 8) | p[1], (p[2] << 8) | p[3]
            data = self.sectors.get(sector)
            if data is None or off + 16 > self.size:
                self._error(req, 0x02)           # invalid argument
            else:
                self._reply(req, data[off:off + 16])
        elif fn == 0x6:
            sector, n = (p[0] << 8) | p[1], (p[4] << 8) | p[5]
            if sector & 0x0100 or n != self.size:
                self._error(req, 0x02)
            else:
                self._wr = [sector, bytearray()]
                self._reply(req, b"")
        elif fn == 0x7:
            assert self._wr is not None, "memory data without an address write"
            self._wr[1] += p[:16]
            self._reply(req, b"")
        elif fn == 0x8:
            assert self._wr is not None, "write end without an address write"
            sector, data = self._wr
            self.sectors[sector] = bytes(data[:self.size])
            self.writes.append(sector)
            self._wr = None
            self._reply(req, b"")
        elif fn == 0xB:
            self._reply(req, bytes((self.dpi_index,)))
        elif fn == 0xC:
            self.dpi_index = p[0]
            self._reply(req, b"")
        else:
            raise AssertionError(f"the mock Superlight received onboard-profile function {fn}")

    def read(self, timeout: float, size: int = 64) -> Optional[bytes]:
        return self._out.popleft() if self._out else None


_singletons: dict = {}


def mock_mode() -> Optional[str]:
    m = os.environ.get(MOCK_ENV, "").strip().lower()
    return m if m in MODES else None


def open_mock(mode: str):
    """(device, wired) for the session opener. The fake persists across opens so edits stick."""
    if mode == "denied":
        raise AccessDenied("/dev/hidraw-mock")
    if mode == "missing":
        return None
    dev = _singletons.get(mode)
    if dev is None:
        if mode.startswith("razer"):
            dev = FakeRazer(mode)
        elif mode.startswith("logitech"):
            dev = FakeLogitech(mode)
        else:
            dev = FakeHidraw(mode)
        _singletons[mode] = dev
    if isinstance(dev, FakeRazer):
        return dev, dev.wired, "razer", dev.pid
    if isinstance(dev, FakeLogitech):
        return dev, dev.wired, "logitech", dev.pid
    return dev, dev.wired
