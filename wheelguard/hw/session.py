"""High-level operations on the mouse: read everything, apply edits, restore the original backup.

Pure Python (no Qt); the GUI runs these on a worker thread. Each operation opens the hidraw node,
does its work and closes it, so nothing stays open between operations and nothing is sent unless
the user asked for it. The same flow serves every supported mouse; the model specifics live in its
driver (drivers.py, razer.py, logitech.py) and codec.

Apply = fresh read -> identity gate -> sanity check -> backup before the first write -> apply the
edits to the *fresh* settings -> write only what changed -> read again and compare. Starting from a
fresh read means anything the user didn't touch (including the active DPI stage, which the mouse's
own DPI button changes) is left exactly as the mouse has it.
"""
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import hidraw, mock
from .drivers import Driver, EndgameDriver, fw_key
from .endgame import LinkError, Rejected, StatusError
from .errors import (ApplyResult, Battery, HwError, MouseAsleep, NoAccess, NoDevice, Snapshot,  # noqa: F401
                     UnsupportedMouse, VerifyMismatch, WriteFailed)
from .hidraw import AccessDenied
from .logitech import LogitechDriver
from .models import supported_names
from .razer import RazerDriver

Progress = Callable[[float, str], None]
_fw_key = fw_key           # older name, kept for callers


def _noprogress(_f: float, _t: str) -> None:
    pass


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def default_backup_dir() -> Path:
    return config_home() / "wheelguard" / "backups"


DRIVERS = {"endgame": EndgameDriver, "razer": RazerDriver, "logitech": LogitechDriver}
CODECS = {d.model.key: d.codec for d in DRIVERS.values()}


def codec_for(model_key: str):
    """The codec of a model (Snapshot.model); Endgame Gear's for unknown keys."""
    return CODECS.get(model_key, EndgameDriver.codec)


def default_opener():
    """What to talk to: (device, wired) for an Endgame Gear mouse, or (device, wired, kind, pid) for
    the others; the fake devices when WHEELGUARD_HW_MOCK is set. None when no supported mouse is
    connected. Raises AccessDenied when the node can't be opened."""
    mode = mock.mock_mode()
    if mode:
        return mock.open_mock(mode)
    cands = hidraw.discover_all()
    if not cands:
        return None
    c = cands[0]
    dev = hidraw.HidrawDevice(c.path)
    return (dev, c.wired) if c.kind == "endgame" else (dev, c.wired, c.kind, c.pid)


def make_driver(opened, sleep: Callable[[float], None]) -> Driver:
    if len(opened) == 2:
        dev, wired = opened
        return EndgameDriver(dev, wired, sleep)
    dev, wired, kind, pid = opened
    return DRIVERS[kind](dev, wired, sleep, pid)


def missing_text() -> str:
    names = supported_names()
    return "No supported mouse found (" + ", ".join(names[:-1]) + " or " + names[-1] + ")."


class MouseSession:
    def __init__(self, opener=None, backup_dir: Optional[Path] = None,
                 sleep: Optional[Callable[[float], None]] = None):
        self._opener = opener or default_opener
        self._backup_dir = Path(backup_dir) if backup_dir else None
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()      # one operation at a time, like the vendor tools
        self._waiting = 0                  # operations queued for the lock: a stage watch gives way to them
        self._count_lock = threading.Lock()

    @contextmanager
    def _operation(self):
        """Hold the device for one user-visible operation. A stage watch in progress notices the waiting
        operation within a fraction of a second and hands the device over."""
        with self._count_lock:
            self._waiting += 1
        with self._lock:
            with self._count_lock:
                self._waiting -= 1
            yield

    def _others_waiting(self) -> bool:
        return self._waiting > 0

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir or default_backup_dir()

    # ---- backups
    def backups(self, fw: str, drv: Optional[Driver] = None) -> List[Path]:
        """Valid backups for this mouse and firmware, oldest first (the oldest is the original)."""
        drv = drv or EndgameDriver(None, True, self._sleep)
        d, prefix = self.backup_dir, drv.backup_prefix(fw)
        out = []
        try:
            for p in sorted(d.glob(f"{prefix}-*.bin")):
                if p.stat().st_size <= 65536 and drv.backup_ok(p.read_bytes()):
                    out.append(p)
        except OSError:
            return out
        return out

    def original_backup(self, fw: str, drv: Optional[Driver] = None) -> Optional[Path]:
        b = self.backups(fw, drv)
        return b[0] if b else None

    def _save_backup(self, blob: bytes, fw: str, drv: Optional[Driver] = None) -> Path:
        drv = drv or EndgameDriver(None, True, self._sleep)
        d, prefix = self.backup_dir, drv.backup_prefix(fw)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.bin"
        n = 0
        while path.exists():
            n += 1
            path = d / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{n}.bin"
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
    def _open(self) -> Driver:
        try:
            opened = self._opener()
        except AccessDenied as e:
            raise NoAccess("No permission to open the mouse's hidraw device.") from e
        except OSError as e:
            raise HwError(f"Could not open the device: {e.strerror or e}") from e
        if opened is None:
            raise NoDevice(missing_text())
        return make_driver(opened, self._sleep)

    def _snapshot(self, drv: Driver, snap: Snapshot, blob: bytes, battery: Optional[Battery]) -> Snapshot:
        problems = drv.blob_problems(blob)
        state = drv.state_problem()
        if state:
            problems = problems + [state]
        backup = self.original_backup(snap.mouse_fw, drv)
        return replace(snap, model=drv.model.key, battery=battery.percent if battery else None,
                       charging=battery.charging if battery else None, blob=blob,
                       writable=not problems and drv.write_enabled, problem="; ".join(problems),
                       backup=str(backup) if backup else None)

    def _read_fresh(self, drv: Driver, snap: Snapshot, progress: Progress, f: float) -> bytes:
        try:
            progress(f, "Reading current settings…")
            return drv.read_blob()
        except drv.link_errors as e:
            raise drv.unreachable(snap, e) from e

    # ---- operations
    def read(self, progress: Progress = _noprogress) -> Snapshot:
        with self._operation():
            drv = self._open()
            try:
                snap = drv.identify(progress, 0.0, 0.5)
                battery = None
                try:
                    progress(0.55, "Reading battery…")
                    battery = drv.battery()
                except drv.link_errors:
                    pass
                try:
                    progress(0.7, "Reading settings…")
                    blob = drv.read_blob()
                except drv.link_errors as e:
                    raise drv.unreachable(snap, e) from e
                progress(1.0, "Done")
                return self._snapshot(drv, snap, blob, battery)
            finally:
                drv.close()

    def read_battery(self) -> Battery:
        """Battery only (link check + battery read): the cheap read the tray refreshes in the background."""
        with self._operation():
            drv = self._open()
            try:
                try:
                    drv.link_check()
                    return drv.battery()
                except drv.link_errors as e:
                    raise drv.unreachable(None, e) from e
            finally:
                drv.close()

    def _write_and_verify(self, drv: Driver, snap: Snapshot, fresh: bytes, new: bytes,
                          check_keys: List[str], progress: Progress, f0: float) -> ApplyResult:
        codec = drv.codec
        plan = drv.plan_writes(fresh, new)
        written: List[str] = []
        n = max(1, len(plan))
        for i, op in enumerate(plan):
            progress(f0 + (0.92 - f0) * i / n, drv.op_text(op))
            try:
                drv.write(op)
            except drv.link_errors as e:
                raise WriteFailed(f"The mouse didn't accept the {op.label} update ({e}). "
                                  "Some earlier parts may already be saved; refresh to see the real state.",
                                  written) from e
            except Rejected as e:
                raise WriteFailed(f"Refused to send the {op.label} update: {e}", written) from e
            written.append(drv.op_done(op))
        progress(0.94, "Checking what the mouse stored…")
        try:
            drv.link_check()
            after = drv.read_blob()
            battery = drv.battery()
        except drv.link_errors:
            battery = None
            try:
                after = drv.read_blob()
            except drv.link_errors as e:
                raise WriteFailed("Written, but the read-back failed. Refresh to check the result.", written) from e
        out = self._snapshot(drv, snap, after, battery)
        want, got = codec.flat(codec.decode(new)), codec.flat(codec.decode(after))
        bad = [k for k in check_keys if want.get(k) != got.get(k)]
        if bad:
            raise VerifyMismatch("The mouse reports different values than were written. "
                                 "The page now shows what it actually stored.", out, bad)
        progress(1.0, "Saved")
        return ApplyResult(out, [op.label for op in plan], None)

    def _writable_fresh(self, drv: Driver, snap: Snapshot, progress: Progress, f: float) -> bytes:
        fresh = self._read_fresh(drv, snap, progress, f)
        probs = drv.blob_problems(fresh)
        if drv.state_problem():
            probs = probs + [drv.state_problem()]
        if probs:
            raise HwError("Refusing to write: " + "; ".join(probs) + ".", snap)
        return fresh

    def apply(self, edits: Dict[str, object], progress: Progress = _noprogress) -> ApplyResult:
        """Write the edited settings. Raises HwError subclasses; nothing is sent if validation fails."""
        with self._operation():
            drv = self._open()
            try:
                snap = drv.identify(progress, 0.0, 0.25)
                fresh = self._writable_fresh(drv, snap, progress, 0.3)
                codec = drv.codec
                try:
                    new = codec.encode(fresh, edits)
                    codec.validate_settings(codec.decode(new))
                except (ValueError, TypeError, OverflowError) as e:     # e.g. a DPI of None
                    raise HwError(str(e), snap) from e
                if not drv.plan_writes(fresh, new):
                    return ApplyResult(self._snapshot(drv, snap, fresh, None), [], None, unchanged=True)
                created = None
                if self.original_backup(snap.mouse_fw, drv) is None:
                    progress(0.4, "Saving a backup of the mouse's settings…")
                    try:
                        created = str(self._save_backup(fresh, snap.mouse_fw, drv))
                    except OSError as e:
                        raise HwError(f"Couldn't save the backup first ({e}); nothing was written.", snap) from e
                res = self._write_and_verify(drv, snap, fresh, new, list(edits), progress, 0.5)
                return replace(res, backup_created=created)
            finally:
                drv.close()

    def restore(self, progress: Progress = _noprogress) -> ApplyResult:
        """Write the original backup back, through the same diff-only path."""
        with self._operation():
            drv = self._open()
            try:
                snap = drv.identify(progress, 0.0, 0.25)
                fresh = self._writable_fresh(drv, snap, progress, 0.3)     # first: the backup name can depend on it
                path = self.original_backup(snap.mouse_fw, drv)
                if path is None:
                    raise HwError("There is no backup to restore for this firmware.", snap)
                backup = path.read_bytes()
                if drv.codec.blob_problems(backup):
                    raise HwError("The backup file doesn't look like a settings block.", snap)
                codec = drv.codec
                try:
                    new = codec.merge_backup(fresh, backup)
                except ValueError as e:
                    raise HwError(f"The backup doesn't fit this mouse ({e}).", snap) from e
                if not drv.plan_writes(fresh, new):
                    return ApplyResult(self._snapshot(drv, snap, fresh, None), [], None, unchanged=True)
                want = codec.flat(codec.decode(new))
                keys = codec.changed_keys(codec.flat(codec.decode(fresh)), want)
                return self._write_and_verify(drv, snap, fresh, new, keys, progress, 0.4)
            finally:
                drv.close()


    def watch_stage(self, should_stop: Callable[[], bool], seconds: float) -> Optional[int]:
        """The active DPI stage as the mouse reports it, listening up to `seconds` on models that send a
        report when their DPI button is pressed (see Model.stage_watch). Read-only. Returns early, with
        None, when another operation wants the mouse or should_stop() says so. Raises HwError when the
        mouse can't be reached."""
        if self._others_waiting():
            return None
        with self._lock:
            drv = self._open()
            try:
                return drv.watch_stage(lambda: should_stop() or self._others_waiting(), seconds)
            except drv.link_errors as e:
                raise drv.unreachable(None, e) from e
            finally:
                drv.close()


__all__ = ["MouseSession", "Snapshot", "ApplyResult", "Battery", "HwError", "NoDevice", "NoAccess",
           "MouseAsleep", "UnsupportedMouse", "WriteFailed", "VerifyMismatch", "default_opener",
           "make_driver", "LinkError", "StatusError"]
