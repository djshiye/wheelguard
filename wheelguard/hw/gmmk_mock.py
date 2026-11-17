"""A pretend Glorious keyboard for development and screenshots, chosen with WHEELGUARD_KB_MOCK:

    gmmk-pro | gmmk-pro-iso    a stock board (ANSI / ISO)
    gmmk2-65[-iso] | gmmk2-96[-iso]
    gmmk3-{65,75,100}[-iso] | gmmk3-pro-{65,75,100}[-iso] | gmmk3-pro-wireless-{65,75,100}[-iso]
    gmmk3-he-{65,75,100}[-iso] | gmmk3-pro-he[-wireless]-{65,75,100}[-iso]
    gmmk-numpad
    gmbk-75[-iso] | gmmk-eternal-96[-iso]
    qmk                        a board flashed with QMK/VIA
    denied                     the board is there but its settings channel isn't accessible
    odd                        the descriptor reports an unexpected settings report size

FakeBoardDevice sits under gmmk.Transport, so mock runs go through the real frame checks and padding.
Every frame it receives is kept in `frames` (tests read it).
"""
import os
from typing import List, Optional

from . import gmmk
from .hidraw import AccessDenied

MOCK_ENV = "WHEELGUARD_KB_MOCK"
_PIDS = {"gmmk-pro-iso": 0x5046, "gmmk2-65": 0x5045, "gmmk2-65-iso": 0x504A, "gmmk2-96": 0x504B,
         "gmmk2-96-iso": 0x505A}
for _pid, _m in gmmk.MODELS.items():
    if _m.key.startswith("gmmk3") and _m.vendor == gmmk.VENDOR_GMMK3:
        _PIDS.setdefault(_m.key.replace("-ansi", ""), _pid)
_PIDS["gmmk-numpad"] = 0x5088
_PIDS.update({"gmbk-75": 0xB000, "gmbk-75-iso": 0xB001, "gmmk-eternal-96": 0xB010, "gmmk-eternal-96-iso": 0xB011})
MODES = ("gmmk-pro", *_PIDS, "qmk", "denied", "odd")
MOCK_PATH = "/dev/hidraw-gmmk-mock"


def mock_mode() -> Optional[str]:
    m = os.environ.get(MOCK_ENV, "").strip().lower()
    return m if m in MODES else None


def boards(mode: str) -> List[gmmk.Board]:
    pid = _PIDS.get(mode, 0x5044)
    model = gmmk.MODELS[pid]
    if mode == "qmk":
        return [gmmk.Board("", pid, model, "0045", 0, qmk=True)]
    size = 33 if mode == "odd" else {"he": 65, "eternal": 64}.get(model.protocol, 265)
    return [gmmk.Board(MOCK_PATH, pid, model, "0045", size, event_path="")]


class FakeBoardDevice:
    frames: List[bytes] = []        # shared, so a test can look after the transport closed it

    def __init__(self, path: str):
        if mock_mode() == "denied":
            raise AccessDenied(path)
        self.closed = False

    def set_feature(self, data: bytes) -> None:
        FakeBoardDevice.frames.append(bytes(data))

    def write(self, data: bytes) -> None:       # the Eternal's output reports
        FakeBoardDevice.frames.append(bytes(data))

    def close(self) -> None:
        self.closed = True
