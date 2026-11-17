"""A pretend GMMK Pro for development and screenshots, chosen with WHEELGUARD_KB_MOCK:

    gmmk-pro | gmmk-pro-iso    a stock board (ANSI / ISO)
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
MODES = ("gmmk-pro", "gmmk-pro-iso", "qmk", "denied", "odd")
MOCK_PATH = "/dev/hidraw-gmmk-mock"


def mock_mode() -> Optional[str]:
    m = os.environ.get(MOCK_ENV, "").strip().lower()
    return m if m in MODES else None


def boards(mode: str) -> List[gmmk.Board]:
    pid = 0x5046 if mode == "gmmk-pro-iso" else 0x5044
    model = gmmk.GMMK_PRO[pid]
    if mode == "qmk":
        return [gmmk.Board("", pid, model, "0045", 0, qmk=True)]
    size = 33 if mode == "odd" else 265
    return [gmmk.Board(MOCK_PATH, pid, model, "0045", size, event_path="")]


class FakeBoardDevice:
    frames: List[bytes] = []        # shared, so a test can look after the transport closed it

    def __init__(self, path: str):
        if mock_mode() == "denied":
            raise AccessDenied(path)
        self.closed = False

    def set_feature(self, data: bytes) -> None:
        FakeBoardDevice.frames.append(bytes(data))

    def close(self) -> None:
        self.closed = True
