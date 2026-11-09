"""What a mouse operation returns or raises, shared by every model's driver and the session."""
from dataclasses import dataclass
from typing import List, Optional

from .models import DEFAULT


class HwError(Exception):
    """Base class. `state` is what the UI should show; `snapshot` carries whatever was learned."""
    state = "error"

    def __init__(self, message: str, snapshot: Optional["Snapshot"] = None):
        super().__init__(message)
        self.snapshot = snapshot


class NoDevice(HwError):
    state = "missing"


class NoAccess(HwError):
    state = "denied"


class MouseAsleep(HwError):
    state = "asleep"


class UnsupportedMouse(HwError):
    state = "unsupported"

    def __init__(self, message: str, pid: int, snapshot: Optional["Snapshot"] = None):
        super().__init__(message, snapshot)
        self.pid = pid


class WriteFailed(HwError):
    """A write failed part-way. `written` lists the parts that did go through."""
    def __init__(self, message: str, written: List[str]):
        super().__init__(message)
        self.written = written


class VerifyMismatch(HwError):
    """The mouse accepted the writes but a read-back shows different values."""
    def __init__(self, message: str, snapshot: "Snapshot", keys: List[str]):
        super().__init__(message, snapshot)
        self.keys = keys


@dataclass(frozen=True)
class Snapshot:
    wired: bool
    dongle_fw: str          # "" when wired or unknown
    mouse_fw: str
    mouse_pid: int
    battery: Optional[int] = None
    blob: Optional[bytes] = None
    writable: bool = False
    problem: str = ""       # why writes are disabled although the mouse is supported
    backup: Optional[str] = None     # path of the original backup, if one exists
    model: str = DEFAULT.key         # models.Model.key
    charging: Optional[bool] = None  # None: the mouse doesn't say (Endgame: assume "charging while wired")


@dataclass(frozen=True)
class ApplyResult:
    snapshot: Snapshot
    written: List[str]
    backup_created: Optional[str] = None
    unchanged: bool = False


@dataclass(frozen=True)
class Battery:
    percent: int
    charging: Optional[bool] = None
