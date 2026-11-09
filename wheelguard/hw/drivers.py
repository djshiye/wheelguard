"""One driver per mouse model: the glue between the session's generic read/apply/restore flow and a
model's transport (endgame.py, razer.py, hidpp.py) and codec.

Every driver presents the mouse's settings as one opaque byte string, the "blob": the real settings
block for Endgame Gear, the onboard profile sector for Logitech, a fixed little record for Razer (see
each codec). The session reads it, lets the codec patch a copy, asks the driver for the minimal list
of writes, sends them, reads the blob back and compares. Backups are blobs too.
"""
import re
import time
from typing import Callable, List, Optional

from . import codec as endgame_codec
from .endgame import EndgameDevice, LinkError, StatusError
from .errors import Battery, HwError, MouseAsleep, NoDevice, Snapshot, UnsupportedMouse
from .hidraw import PID_WIRED
from .models import ENDGAME_XM2W, Model

Progress = Callable[[float, str], None]
StopCheck = Callable[[], bool]
WATCH_SLICE_S = 0.25        # how often a listener checks whether it should give the mouse back


def fw_key(fw: str) -> str:
    return re.sub(r"[^0-9A-Za-z.]+", "_", fw) or "unknown"


class Driver:
    """Interface. Subclasses fill in the model specifics."""
    model: Model = ENDGAME_XM2W
    codec = endgame_codec
    link_errors = (StatusError, LinkError)    # "the mouse didn't answer / the link failed"

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None]):
        self.dev = dev
        self.wired = wired
        self._sleep = sleep

    # ---- identity and gate
    @property
    def write_enabled(self) -> bool:
        raise NotImplementedError

    def identify(self, progress: Progress, f0: float, f1: float) -> Snapshot:
        """Contact the mouse and say what it is. Raises HwError subclasses."""
        raise NotImplementedError

    # ---- reads
    def link_check(self) -> None:
        """Cheap "are you there" before a battery read or a read-back."""

    def battery(self) -> Battery:
        raise NotImplementedError

    def read_blob(self) -> bytes:
        raise NotImplementedError

    def blob_problems(self, blob: bytes) -> List[str]:
        return self.codec.blob_problems(blob)

    def state_problem(self) -> str:
        """Why writes are refused although the blob looks fine ("" = no reason)."""
        return ""

    def watch_stage(self, should_stop: StopCheck, seconds: float) -> Optional[int]:
        """The active DPI stage (0-based) as the mouse reports it, waiting up to `seconds` for a report
        when the model sends one. None when nothing arrived. Reads only; sends nothing that changes
        anything."""
        return None

    # ---- writes
    def plan_writes(self, old: bytes, new: bytes) -> list:
        return self.codec.plan_writes(old, new)

    def write(self, op) -> None:
        raise NotImplementedError

    def op_text(self, op) -> str:
        return f"Writing {op.label} settings…"

    def op_done(self, op) -> str:
        return op.label

    # ---- backups
    def backup_prefix(self, fw: str) -> str:
        return f"{self.model.key}-{fw_key(fw)}"

    def backup_ok(self, data: bytes) -> bool:
        return not self.blob_problems(data)

    def backup_needed(self, saved: List[bytes], fresh: bytes) -> bool:
        """Save a backup before this write? `saved` = the valid backups, oldest (the original) first."""
        return not saved

    def restore_blob(self, saved: List[bytes]) -> bytes:
        """What "Restore original" goes back to, from the valid backups, oldest first."""
        return saved[0]

    # ---- errors
    def unreachable(self, snap: Optional[Snapshot], e: Exception) -> HwError:
        if isinstance(e, LinkError) and e.errno == 19:       # ENODEV: unplugged mid-operation
            return NoDevice("The mouse was unplugged.")
        if self.wired:
            return HwError("The mouse isn't responding.", snap)
        return MouseAsleep("The receiver can't reach the mouse.", snap)

    def close(self) -> None:
        self.dev.close()


class EndgameDriver(Driver):
    """Endgame Gear XM2w 4k v2 over its vendor Feature reports (endgame.py, codec.py)."""
    model = ENDGAME_XM2W
    codec = endgame_codec

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None]):
        super().__init__(dev, wired, sleep)
        self.eg = EndgameDevice(dev, wired, sleep)

    @property
    def write_enabled(self) -> bool:
        return self.eg.write_enabled

    def identify(self, progress: Progress, f0: float, f1: float) -> Snapshot:
        """probe, dongle info, mouse info."""
        eg, span = self.eg, f1 - f0
        dongle_fw = ""
        try:
            progress(f0, "Contacting the dongle…" if not eg.wired else "Contacting the mouse…")
            eg.probe()
            if not eg.wired:
                progress(f0 + span * 0.25, "Reading dongle firmware…")
                dongle_fw = endgame_codec.fw_text(eg.dongle_info())
            progress(f0 + span * 0.5, "Identifying the mouse…")
            info = eg.mouse_info()
        except self.link_errors as e:
            raise self.unreachable(Snapshot(eg.wired, dongle_fw, "", 0), e) from e
        snap = Snapshot(eg.wired, dongle_fw, endgame_codec.fw_text(info.fw_word), info.pid)
        if info.link_down:
            raise MouseAsleep("The dongle reports no mouse.", snap)
        if info.pid != PID_WIRED:
            raise UnsupportedMouse(f"Unsupported mouse (PID {info.pid:#06x})", info.pid, snap)
        return snap

    def link_check(self) -> None:
        self.eg.probe()

    def watch_stage(self, should_stop: StopCheck, seconds: float) -> Optional[int]:
        """Listen on the config node for the mouse's "DPI stage changed" input report (report 3, event
        0x02, endgame-protocol.md 3.4). Nothing is sent to the mouse."""
        end = time.monotonic() + seconds
        while not should_stop():
            left = end - time.monotonic()
            if left <= 0:
                return None
            r = self.dev.read(min(left, WATCH_SLICE_S), 64)
            if r and len(r) >= 3 and r[0] == 0x03 and r[1] == 0x02 and r[2] <= 3:
                return r[2]
        return None

    def battery(self) -> Battery:
        return Battery(self.eg.battery())

    def read_blob(self) -> bytes:
        return self.eg.read_blob()

    def write(self, op) -> None:
        self.eg.write_group(op)

    def op_text(self, op) -> str:
        if op.cmd == endgame_codec.CMD_BUTTONS:
            return f"Writing button map ({op.page}/2)…"
        return f"Writing {op.label} settings…"

    def op_done(self, op) -> str:
        return op.label if op.cmd != endgame_codec.CMD_BUTTONS else f"buttons {op.page}"

    def backup_prefix(self, fw: str) -> str:
        return fw_key(fw)        # the original naming, so existing backups keep working

    def backup_ok(self, data: bytes) -> bool:
        return len(data) == endgame_codec.BLOB_LEN

    def unreachable(self, snap, e):
        if isinstance(e, LinkError) and e.errno == 19:
            return NoDevice("The device was unplugged.")
        if self.wired:
            return HwError("The mouse isn't responding.", snap)
        return MouseAsleep("The dongle can't reach the mouse.", snap)
