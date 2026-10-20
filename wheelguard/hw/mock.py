"""A fake config channel for developing and testing without hardware.

FakeHidraw implements the same two-method device layer as hidraw.HidrawDevice, but it sits *under*
endgame.EndgameDevice, so every mock run goes through the real allow-list, gate and codec. It is only
ever created when WHEELGUARD_HW_MOCK is set (see session.default_opener) or by tests.

    WHEELGUARD_HW_MOCK=connected|connected-wired|asleep|unsupported|denied|missing
"""
import os
from typing import List, Optional

from . import codec
from .hidraw import AccessDenied, PID_WIRED

MOCK_ENV = "WHEELGUARD_HW_MOCK"
MODES = ("connected", "connected-wired", "asleep", "unsupported", "denied", "missing")


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
        dev = _singletons[mode] = FakeHidraw(mode)
    return dev, dev.wired
