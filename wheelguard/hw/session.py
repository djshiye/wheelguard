"""High-level operations on the mouse: read everything, apply edits, restore the original backup.

Pure Python (no Qt); the GUI runs these on a worker thread. Each operation opens the hidraw node,
does its work and closes it, so nothing stays open between operations and nothing is sent unless
the user asked for it.

Apply = fresh read -> identity/PID gate -> sanity check -> backup before the first write ->
apply the edits to the *fresh* blob -> write only the changed groups (14, 15, 16) -> read again
and compare. Starting from a fresh blob means anything the user didn't touch (including the active
DPI stage, which the mouse's own DPI button changes) is left exactly as the mouse has it.
"""
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import codec, hidraw, mock
from .endgame import EndgameDevice, LinkError, Rejected, StatusError
from .hidraw import AccessDenied, PID_WIRED

Progress = Callable[[float, str], None]


def _noprogress(_f: float, _t: str) -> None:
    pass


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
    """A group write failed part-way. `written` lists the groups that did go through."""
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
    problem: str = ""       # why writes are disabled although the PID is supported
    backup: Optional[str] = None     # path of the original backup, if one exists


@dataclass(frozen=True)
class ApplyResult:
    snapshot: Snapshot
    written: List[str]
    backup_created: Optional[str] = None
    unchanged: bool = False


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def default_backup_dir() -> Path:
    return config_home() / "wheelguard" / "backups"


def default_opener():
    """(device, wired) for the real hardware, or the fake when WHEELGUARD_HW_MOCK is set.
    Returns None when no config channel exists. Raises AccessDenied when the node can't be opened."""
    mode = mock.mock_mode()
    if mode:
        return mock.open_mock(mode)
    cands = hidraw.discover()
    if not cands:
        return None
    c = cands[0]
    return hidraw.HidrawDevice(c.path), c.wired


def _fw_key(fw: str) -> str:
    return re.sub(r"[^0-9A-Za-z.]+", "_", fw) or "unknown"


class MouseSession:
    def __init__(self, opener=None, backup_dir: Optional[Path] = None,
                 sleep: Optional[Callable[[float], None]] = None):
        self._opener = opener or default_opener
        self._backup_dir = Path(backup_dir) if backup_dir else None
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()      # one operation at a time, like the vendor tool

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir or default_backup_dir()

    # ---- backups
    def backups(self, fw: str) -> List[Path]:
        """Valid backups for this firmware, oldest first (the oldest is the original)."""
        d, key = self.backup_dir, _fw_key(fw)
        try:
            files = sorted(p for p in d.glob(f"{key}-*.bin") if p.stat().st_size == codec.BLOB_LEN)
        except OSError:
            return []
        return files

    def original_backup(self, fw: str) -> Optional[Path]:
        b = self.backups(fw)
        return b[0] if b else None

    def _save_backup(self, blob: bytes, fw: str) -> Path:
        d = self.backup_dir
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_fw_key(fw)}-{time.strftime('%Y%m%d-%H%M%S')}.bin"
        n = 0
        while path.exists():
            n += 1
            path = d / f"{_fw_key(fw)}-{time.strftime('%Y%m%d-%H%M%S')}-{n}.bin"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise
        if path.read_bytes() != blob:            # never write to the mouse on the strength of a bad backup
            path.unlink(missing_ok=True)
            raise OSError("backup verification failed")
        return path

    # ---- device plumbing
    def _open(self):
        try:
            opened = self._opener()
        except AccessDenied as e:
            raise NoAccess("No permission to open the dongle's hidraw device.") from e
        except OSError as e:
            raise HwError(f"Could not open the device: {e.strerror or e}") from e
        if opened is None:
            raise NoDevice("No Endgame Gear dongle or mouse found.")
        dev, wired = opened
        return dev, EndgameDevice(dev, wired, self._sleep)

    def _unreachable(self, eg: EndgameDevice, snap: Optional[Snapshot], e: Exception) -> HwError:
        if isinstance(e, LinkError) and e.errno == 19:       # ENODEV: unplugged mid-operation
            return NoDevice("The device was unplugged.")
        if eg.wired:
            return HwError("The mouse isn't responding.", snap)
        return MouseAsleep("The dongle can't reach the mouse.", snap)

    def _identify(self, eg: EndgameDevice, progress: Progress, f0: float, f1: float):
        """probe, dongle info, mouse info -> (dongle_fw, MouseInfo, partial Snapshot)."""
        span = f1 - f0
        dongle_fw = ""
        try:
            progress(f0, "Contacting the dongle…" if not eg.wired else "Contacting the mouse…")
            eg.probe()
            if not eg.wired:
                progress(f0 + span * 0.25, "Reading dongle firmware…")
                w = eg.dongle_info()
                dongle_fw = codec.fw_text(w)
            progress(f0 + span * 0.5, "Identifying the mouse…")
            info = eg.mouse_info()
        except (StatusError, LinkError) as e:
            raise self._unreachable(eg, Snapshot(eg.wired, dongle_fw, "", 0), e) from e
        snap = Snapshot(eg.wired, dongle_fw, codec.fw_text(info.fw_word), info.pid)
        if info.link_down:
            raise MouseAsleep("The dongle reports no mouse.", snap)
        if info.pid != PID_WIRED:
            raise UnsupportedMouse(f"Unsupported mouse (PID {info.pid:#06x})", info.pid, snap)
        return dongle_fw, info, snap

    def _snapshot(self, eg: EndgameDevice, snap: Snapshot, blob: bytes, battery: Optional[int]) -> Snapshot:
        problems = codec.blob_problems(blob)
        backup = self.original_backup(snap.mouse_fw)
        return replace(snap, battery=battery, blob=blob, writable=not problems and eg.write_enabled,
                       problem="; ".join(problems), backup=str(backup) if backup else None)

    # ---- operations
    def read(self, progress: Progress = _noprogress) -> Snapshot:
        with self._lock:
            dev, eg = self._open()
            try:
                _fw, _info, snap = self._identify(eg, progress, 0.0, 0.5)
                battery = None
                try:
                    progress(0.55, "Reading battery…")
                    battery = eg.battery()
                except (StatusError, LinkError):
                    pass
                try:
                    progress(0.7, "Reading settings…")
                    blob = eg.read_blob()
                except (StatusError, LinkError) as e:
                    raise self._unreachable(eg, snap, e) from e
                progress(1.0, "Done")
                return self._snapshot(eg, snap, blob, battery)
            finally:
                dev.close()

    def read_battery(self) -> Optional[int]:
        """Battery percentage only (link check + A1 B4): the cheap read the tray refreshes in the background."""
        with self._lock:
            dev, eg = self._open()
            try:
                try:
                    eg.probe()
                    return eg.battery()
                except (StatusError, LinkError) as e:
                    raise self._unreachable(eg, None, e) from e
            finally:
                dev.close()

    def _write_and_verify(self, eg: EndgameDevice, snap: Snapshot, fresh: bytes, new: bytes,
                          check_keys: List[str], progress: Progress, f0: float) -> ApplyResult:
        plan = codec.plan_writes(fresh, new)
        written: List[str] = []
        n = max(1, len(plan))
        for i, gw in enumerate(plan):
            progress(f0 + (0.92 - f0) * i / n, f"Writing {gw.label} settings…" if gw.cmd != codec.CMD_BUTTONS
                     else f"Writing button map ({gw.page}/2)…")
            try:
                eg.write_group(gw)
            except (StatusError, LinkError) as e:
                raise WriteFailed(f"The mouse didn't accept the {gw.label} update ({e}). "
                                  "Some earlier groups may already be saved; refresh to see the real state.",
                                  written) from e
            except Rejected as e:
                raise WriteFailed(f"Refused to send the {gw.label} update: {e}", written) from e
            written.append(gw.label if gw.cmd != codec.CMD_BUTTONS else f"buttons {gw.page}")
        progress(0.94, "Checking what the mouse stored…")
        try:
            eg.probe()
            after = eg.read_blob()
            battery = eg.battery()
        except (StatusError, LinkError):
            battery = None
            try:
                after = eg.read_blob()
            except (StatusError, LinkError) as e:
                raise WriteFailed("Written, but the read-back failed. Refresh to check the result.", written) from e
        out = self._snapshot(eg, snap, after, battery)
        want, got = codec.flat(codec.decode(new)), codec.flat(codec.decode(after))
        bad = [k for k in check_keys if want[k] != got[k]]
        if bad:
            raise VerifyMismatch("The mouse reports different values than were written. "
                                 "The page now shows what it actually stored.", out, bad)
        progress(1.0, "Saved")
        return ApplyResult(out, [g.label for g in plan], None)

    def apply(self, edits: Dict[str, object], progress: Progress = _noprogress) -> ApplyResult:
        """Write the edited settings. Raises HwError subclasses; nothing is sent if validation fails."""
        with self._lock:
            dev, eg = self._open()
            try:
                _fw, info, snap = self._identify(eg, progress, 0.0, 0.25)
                try:
                    progress(0.3, "Reading current settings…")
                    fresh = eg.read_blob()
                except (StatusError, LinkError) as e:
                    raise self._unreachable(eg, snap, e) from e
                probs = codec.blob_problems(fresh)
                if probs:
                    raise HwError("Refusing to write: " + "; ".join(probs) + ".", snap)
                try:
                    new = codec.encode(fresh, edits)
                    codec.validate_settings(codec.decode(new))
                except ValueError as e:
                    raise HwError(str(e), snap) from e
                if not codec.plan_writes(fresh, new):
                    return ApplyResult(self._snapshot(eg, snap, fresh, None), [], None, unchanged=True)
                created = None
                if self.original_backup(snap.mouse_fw) is None:
                    progress(0.4, "Saving a backup of the mouse's settings…")
                    try:
                        created = str(self._save_backup(fresh, snap.mouse_fw))
                    except OSError as e:
                        raise HwError(f"Couldn't save the backup first ({e}); nothing was written.", snap) from e
                res = self._write_and_verify(eg, snap, fresh, new, list(edits), progress, 0.5)
                return replace(res, backup_created=created)
            finally:
                dev.close()

    def restore(self, progress: Progress = _noprogress) -> ApplyResult:
        """Write the original backup back, through the same diff-only path."""
        with self._lock:
            dev, eg = self._open()
            try:
                _fw, info, snap = self._identify(eg, progress, 0.0, 0.25)
                path = self.original_backup(snap.mouse_fw)
                if path is None:
                    raise HwError("There is no backup to restore for this firmware.", snap)
                backup = path.read_bytes()
                if codec.blob_problems(backup):
                    raise HwError("The backup file doesn't look like a settings block.", snap)
                try:
                    progress(0.3, "Reading current settings…")
                    fresh = eg.read_blob()
                except (StatusError, LinkError) as e:
                    raise self._unreachable(eg, snap, e) from e
                probs = codec.blob_problems(fresh)
                if probs:
                    raise HwError("Refusing to write: " + "; ".join(probs) + ".", snap)
                new = codec.merge_backup(fresh, backup)
                if not codec.plan_writes(fresh, new):
                    return ApplyResult(self._snapshot(eg, snap, fresh, None), [], None, unchanged=True)
                want = codec.flat(codec.decode(new))
                keys = codec.changed_keys(codec.flat(codec.decode(fresh)), want)
                return self._write_and_verify(eg, snap, fresh, new, keys, progress, 0.4)
            finally:
                dev.close()
