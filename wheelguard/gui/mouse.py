"""QObject backend for the Mouse page: reads and writes the mouse's own settings over its vendor HID
channel (wheelguard.hw). All HID I/O runs on one worker thread; the UI thread only stages edits.

Edits are never sent live. `staged` holds only the keys that differ from what the mouse reported;
Apply sends those (the session re-reads the mouse first and writes only the groups that changed).
The tray is the one exception: setActiveStage() applies just the active DPI stage, and only when
nothing else is staged. It also gets quiet background refreshes (battery, or a full read-only poll)
that never touch the page state.
"""
import os
import tempfile
import time
from typing import Dict, Optional

from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot

from pathlib import Path

from ..hw import codec, mock, models, razer
from . import setups as setupstore, traymodel
from ..hw.session import (ApplyResult, Battery, HwError, MouseSession, Snapshot, UnsupportedMouse, VerifyMismatch,
                          WriteFailed, CODECS, codec_for, config_home, default_backup_dir)

BUSY_STATES = ("reading", "applying")
REFRESH_MIN_S = 20          # at most one tray-triggered refresh per this many seconds
BATTERY_EVERY_MS = 5 * 60 * 1000

# Every key any model uses, with a harmless value: the page binds to keys of cards it hides, and this
# keeps those bindings defined whatever mouse is connected.
NEUTRAL: Dict[str, object] = {}
for _c in CODECS.values():
    for _k, _v in _c.empty().items():
        NEUTRAL.setdefault(_k, _v)


class _Worker(QObject):
    progress = Signal(float, str)
    finished = Signal(str, object)      # kind, Snapshot | ApplyResult | HwError | Exception

    def __init__(self, session: MouseSession):
        super().__init__()
        self._session = session

    @Slot(str, object)
    def run(self, kind: str, arg):
        emit = self.progress.emit
        try:
            if kind in ("read", "poll"):
                res = self._session.read(emit)
            elif kind in ("apply", "stage"):
                res = self._session.apply(arg, emit)
            elif kind == "restore":
                res = self._session.restore(emit)
            elif kind == "battery":
                res = self._session.read_battery()
            else:
                raise ValueError(f"unknown job {kind!r}")
        except Exception as e:      # noqa: BLE001 - everything is reported to the UI, never raised into Qt
            res = e
        self.finished.emit(kind, res)


class MouseController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    valuesChanged = Signal()
    progressChanged = Signal()
    messageChanged = Signal()
    trayChanged = Signal()          # anything the tray icon or popup shows
    setupsChanged = Signal()
    _request = Signal(str, object)

    def __init__(self, session: Optional[MouseSession] = None, parent=None, clock=time.monotonic,
                 setups: Optional[setupstore.SetupStore] = None):
        super().__init__(parent)
        if session is None:
            if mock.mock_mode():
                # Mock runs: fast waits, and backups go to a throwaway directory, never ~/.config.
                bdir = os.environ.get("WHEELGUARD_BACKUP_DIR") or tempfile.mkdtemp(prefix="wheelguard-mock-")
                session = MouseSession(backup_dir=bdir, sleep=lambda s: time.sleep(s * 0.12))
            else:
                session = MouseSession()
        self._session = session
        if setups is None:
            # Next to the user's other Wheelguard files; mock runs and tests (own backup folder) stay in that folder.
            path = (setupstore.default_path(config_home()) if session.backup_dir == default_backup_dir()
                    else Path(session.backup_dir) / setupstore.FILE)
            setups = setupstore.SetupStore(path, {k: tuple(c.KEYS) for k, c in CODECS.items()})
        self._setups = setups
        self._setup_list = []           # [Setup] of the connected model
        self._applying_setup = ""
        self._state = "idle"
        self._message = ""
        self._message_kind = ""         # "error" | "ok" | ""
        self._progress = 0.0
        self._progress_text = ""
        self._wired: Optional[bool] = None
        self._model = models.DEFAULT
        self._codec = codec
        self._known = False             # a mouse has been identified at least once
        self._charging: Optional[bool] = None
        self._dongle_fw = ""
        self._mouse_fw = ""
        self._pid = 0
        self._battery = -1
        self._writable = False
        self._problem = ""
        self._backup = ""
        self._baseline: Dict[str, object] = codec.empty()
        self._blob: Optional[bytes] = None
        self._staged: Dict[str, object] = {}
        self._edit_problem = ""
        self._detail = ""
        self._keep_message = False
        self._pending_stage: Optional[int] = None       # stage being switched to by setActiveStage
        self._bg_busy = False                           # a poll/battery job is queued or running
        self._clock = clock
        self._last_refresh = -REFRESH_MIN_S
        self._timer: Optional[QTimer] = None
        for sig in (self.stateChanged, self.infoChanged, self.valuesChanged):
            sig.connect(self.trayChanged)
        for sig in (self.infoChanged, self.valuesChanged):
            sig.connect(self.setupsChanged)
        self.setupsChanged.connect(self.trayChanged)

        self._thread: Optional[QThread] = None
        self._worker = _Worker(session)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._request.connect(self._worker.run)

    # ---- thread management
    def _submit(self, kind: str, arg=None):
        if self._thread is None:
            self._thread = QThread(self)
            self._worker.moveToThread(self._thread)
            self._thread.start()
        self._request.emit(kind, arg)

    def shutdown(self):
        """Let an operation in flight finish (a half-applied settings update is worse than a short wait)."""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(40000)

    # ---- state helpers
    def _set_state(self, s: str):
        if s != self._state:
            self._state = s
            self.stateChanged.emit()

    def _set_message(self, text: str, kind: str = "error"):
        self._message, self._message_kind = text, kind if text else ""
        self.messageChanged.emit()

    def _set_progress(self, f: float, text: str):
        self._progress, self._progress_text = f, text
        self.progressChanged.emit()

    def _take_snapshot(self, snap: Snapshot):
        if snap.model != self._model.key or not self._known:
            if snap.model != self._model.key:
                self._staged = {}       # another mouse: edits made for the old one don't apply
                self._blob = None
            self._model, self._codec = models.by_key(snap.model), codec_for(snap.model)
            self._baseline = self._codec.empty()
            self._known = True
        self._reload_setups()
        self._wired = snap.wired
        if snap.battery is not None:
            self._charging = snap.charging
        self._dongle_fw, self._mouse_fw, self._pid = snap.dongle_fw, snap.mouse_fw, snap.mouse_pid
        if snap.battery is not None:
            self._battery = snap.battery
        self._writable, self._problem = snap.writable, snap.problem
        self._backup = snap.backup or ""
        if snap.blob is not None:
            self._blob = snap.blob
            self._baseline = self._codec.flat(self._codec.decode(snap.blob))
        self.infoChanged.emit()

    def _retain_edits(self):
        """Keep staged edits that still differ from the (re-)read baseline."""
        self._staged = {k: v for k, v in self._staged.items() if self._baseline.get(k) != v}
        self._revalidate()
        self.valuesChanged.emit()

    def _revalidate(self):
        self._edit_problem = ""
        if not self._staged or self._blob is None:
            return
        c = self._codec
        try:
            c.validate_settings(c.decode(c.encode(self._blob, self._staged)))
        except ValueError as e:
            self._edit_problem = str(e)

    # ---- results
    @Slot(float, str)
    def _on_progress(self, f: float, text: str):
        self._set_progress(f, text)

    @Slot(str, object)
    def _on_finished(self, kind: str, res):
        if kind in ("poll", "battery"):
            self._on_background(kind, res)
            return
        self._pending_stage = None
        setup_name, self._applying_setup = self._applying_setup, ""
        self._set_progress(1.0, "")
        if isinstance(res, Snapshot):
            self._take_snapshot(res)
            if not self._keep_message:
                self._set_message("")
            self._keep_message = False
            self._retain_edits()
            self._set_state("ready")
            return
        if isinstance(res, ApplyResult):
            self._take_snapshot(res.snapshot)
            self._staged = {}
            self._revalidate()
            self.valuesChanged.emit()
            if kind == "stage":
                self._set_message("")
            elif res.unchanged:
                self._set_message("The mouse already has these settings.", "ok")
            elif kind == "restore":
                self._set_message("Original settings restored.", "ok")
            elif setup_name:
                self._set_message(f"Setup \u201c{setup_name}\u201d applied to the mouse."
                                  + (" A backup of the previous settings was kept." if res.backup_created else ""), "ok")
            else:
                self._set_message("Saved to the mouse." + (" A backup of the previous settings was kept."
                                  if res.backup_created else ""), "ok")
            self._set_state("ready")
            return
        if isinstance(res, VerifyMismatch):
            self._take_snapshot(res.snapshot)
            self._staged = {}
            self._revalidate()
            self.valuesChanged.emit()
            self._set_message(str(res))
            self._set_state("ready")
            return
        if isinstance(res, WriteFailed):
            self._set_message(str(res))
            self._keep_message = True
            self._submit("read")           # show what the mouse really holds now (edits are kept)
            self._set_state("reading")
            self._set_progress(0.0, "Checking the mouse…")
            return
        if isinstance(res, HwError):
            self._keep_message = False
            if kind != "read" and res.state == "error":
                # A refusal before anything was written (backup failed, invalid edit...): stay on the
                # page, keep the staged edits and say why.
                self._set_message(str(res))
                self._set_state("ready")
                return
            if res.snapshot is not None:
                self._take_snapshot(res.snapshot)
            if isinstance(res, UnsupportedMouse):
                self._writable = False
            self._set_message(str(res) if res.state == "error" else "")
            self._detail = str(res)
            self.infoChanged.emit()
            self._set_state(res.state)
            return
        self._set_message(f"Unexpected error: {res}")
        self._set_state("error")

    def _on_background(self, kind: str, res):
        """Result of a quiet refresh: update what was learned, never the page's progress or message."""
        self._bg_busy = False
        if isinstance(res, Battery):
            if (res.percent, res.charging) != (self._battery, self._charging):
                self._battery, self._charging = res.percent, res.charging
                self.infoChanged.emit()
            return
        if self._state in BUSY_STATES:
            return                          # the user started something meanwhile; its result wins
        if isinstance(res, Snapshot):
            self._take_snapshot(res)
            self._retain_edits()
            self._set_state("ready")
        elif isinstance(res, HwError) and kind == "poll":
            if res.snapshot is not None:
                self._take_snapshot(res.snapshot)
            if isinstance(res, UnsupportedMouse):
                self._writable = False
            self._detail = str(res)
            self.infoChanged.emit()
            self._set_state(res.state)

    def _background(self, kind: str):
        self._bg_busy = True
        self._last_refresh = self._clock()
        self._submit(kind)

    # ---- slots for QML
    @Slot()
    def ensureLoaded(self):
        if self._state == "idle":
            self.refresh()

    @Slot()
    def refresh(self):
        if self._state in BUSY_STATES:
            return
        self._set_message("")
        self._set_state("reading")
        self._set_progress(0.0, "Starting…")
        self._submit("read")

    @Slot()
    def startBackground(self):
        """Keep the battery fresh every few minutes (the tray needs it while the window is hidden)."""
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(BATTERY_EVERY_MS)
            self._timer.timeout.connect(self.refreshBattery)
            self._timer.start()

    def _may_refresh(self) -> bool:
        return not (self._state in BUSY_STATES or self._bg_busy or self._clock() - self._last_refresh < REFRESH_MIN_S)

    @Slot()
    def refreshBattery(self):
        """Timer or tray menu opened: battery only (probe + A1 B4), once the mouse has been read. Rate-limited."""
        if self._state == "ready" and self._may_refresh():
            self._background("battery")

    @Slot()
    def refreshQuietly(self):
        """Quick-view popup opened: refresh without disturbing the Mouse page. With nothing staged this
        is a full read-only poll (the mouse's own DPI button changes the stage, and a mouse that was away
        may be back); with staged edits only the battery is read. Rate-limited."""
        if not self._may_refresh():
            return
        if self._state == "idle":
            self.refresh()
            self._last_refresh = self._clock()
        elif not self._staged:
            self._background("poll")
        elif self._state == "ready":
            self._background("battery")

    @Slot(int)
    def setActiveStage(self, index: int):
        """Switch the active DPI stage now (tray/popup). Goes through the normal apply path, so only the
        0x14 group is written, and only when nothing else is staged: pending edits are never sent."""
        if self._stage_reason() or self._state != "ready":
            return
        if not 0 <= index < self._baseline["stage_count"] or index == self._baseline["active_stage"]:
            return
        self._set_message("")
        self._pending_stage = index
        self._set_state("applying")
        self._set_progress(0.0, "Switching DPI stage…")
        self._submit("stage", {"active_stage": index})

    @Slot()
    def apply(self):
        if self._state != "ready" or not self._staged or not self._writable or self._edit_problem:
            return
        self._set_message("")
        self._set_state("applying")
        self._set_progress(0.0, "Starting…")
        self._submit("apply", dict(self._staged))

    @Slot()
    def restoreOriginal(self):
        if self._state != "ready" or not self._writable or not self._backup:
            return
        self._set_message("")
        self._set_state("applying")
        self._set_progress(0.0, "Starting…")
        self._submit("restore")

    @Slot()
    def discard(self):
        if self._staged:
            self._staged = {}
            self._revalidate()
            self.valuesChanged.emit()

    @Slot()
    def dismissMessage(self):
        self._set_message("")

    def _stage_one(self, key: str, value):
        c = self._codec
        if key not in c.KEYS:
            return
        if key.startswith("dpi_"):
            value = c.snap_dpi(int(value))
        elif isinstance(self._baseline.get(key), bool):
            value = bool(value)
        else:
            value = int(value)
        try:
            c.check_key_value(key, int(value))
        except ValueError as e:
            self._set_message(str(e))
            return
        if self._current().get(key) == value:
            return
        if self._baseline.get(key) == value:
            self._staged.pop(key, None)
        else:
            self._staged[key] = value

    def _current(self) -> Dict[str, object]:
        cur = dict(self._baseline)
        cur.update(self._staged)
        return cur

    @Slot(str, "QVariant")
    def setValue(self, key: str, value):
        self.setValues({key: value})

    @Slot("QVariantMap")
    def setValues(self, patch):
        if self._state != "ready" or not self._writable:
            return
        patch = dict(patch)
        for key, value in list(patch.items()):
            self._stage_one(key, value)
            cur = self._current()
            if key == "glass" and "glass" in self._codec.KEYS:
                lod = cur["lod"]
                if cur["glass"] and lod not in codec.GLASS_LOD:
                    self._stage_one("lod", 1)
                elif not cur["glass"] and lod in codec.GLASS_LOD:
                    self._stage_one("lod", 3 if lod == 1 else 10)
            elif key == "stage_count" and cur["active_stage"] >= cur["stage_count"]:
                self._stage_one("active_stage", cur["stage_count"] - 1)
        if self._message_kind == "ok":
            self._set_message("")
        self._revalidate()
        self.valuesChanged.emit()

    # ---- saved setups
    def _reload_setups(self):
        self._setup_list = self._setups.list(self._model.key) if self._known else []

    def _setup_summary(self, values: Dict[str, object]) -> str:
        labels = traymodel.stage_labels(values) if "stage_count" in values else []
        parts = [", ".join(labels) + " DPI"] if labels else []
        rate = next((p.label for p in self._model.polling if p.code == values.get("polling")), "")
        if rate:
            parts.append(rate)
        return " \u00b7 ".join(parts)

    def _setup_rows(self):
        base = self._baseline
        return [{"name": st.name, "summary": self._setup_summary(st.values),
                 "active": all(base.get(k) == v for k, v in st.values.items())}
                for st in self._setup_list]

    @Slot(str, result=str)
    def saveSetup(self, name: str) -> str:
        """Keep the settings on screen (including unsaved edits) under a name. Returns "" or the problem."""
        if not self._known or self._blob is None:
            return "Read the mouse first."
        cur = self._current()
        err = self._setups.save(self._model.key, name, {k: cur[k] for k in self._codec.KEYS if k in cur})
        self._reload_setups()
        self.setupsChanged.emit()
        return err

    @Slot(str, result=str)
    def deleteSetup(self, name: str) -> str:
        err = self._setups.delete(self._model.key, name)
        self._reload_setups()
        self.setupsChanged.emit()
        return err

    @Slot(str)
    def applySetup(self, name: str):
        """Write a saved setup to the mouse now, through the normal apply path (backup first, then verify)."""
        if self._state != "ready" or not self._writable or self._blob is None:
            return
        if self._staged:
            self._set_message("You have unsaved changes. Apply or discard them first.")
            return
        st = self._setups.get(self._model.key, name)
        if st is None:
            self._set_message("That setup isn't saved any more.")
            self._reload_setups()
            self.setupsChanged.emit()
            return
        c = self._codec
        patch: Dict[str, object] = {}
        try:
            for key, value in st.values.items():
                if key not in c.KEYS:
                    continue
                if isinstance(self._baseline.get(key), bool):
                    value = bool(value)
                else:
                    value = int(value)
                c.check_key_value(key, int(value))          # out of range is refused, never clamped
                if key.startswith("dpi_"):
                    value = c.snap_dpi(value)
                if self._baseline.get(key) != value:
                    patch[key] = value
            if patch:
                c.validate_settings(c.decode(c.encode(self._blob, patch)))
        except ValueError as e:
            self._set_message(f"The setup \u201c{st.name}\u201d doesn't fit this mouse: {e}")
            return
        if not patch:
            self._set_message(f"The mouse already has the setup \u201c{st.name}\u201d.", "ok")
            return
        self._set_message("")
        self._applying_setup = st.name
        self._set_state("applying")
        self._set_progress(0.0, "Starting…")
        self._submit("apply", patch)

    @Slot()
    def stageDemoEdits(self):
        """Screenshot harness only: stage two edits as if the user had made them."""
        codes = self._model.polling_codes
        self.setValues({"dpi_x1": 1200, "dpi_y1": 1200, "polling": 0x04 if 0x04 in codes else codes[-1]})

    # ---- tray view
    def _stage_reason(self) -> str:
        return traymodel.stage_reason(self._state, self._blob is not None, self._writable, len(self._staged))

    def _online(self) -> bool:
        return traymodel.is_online(self._state, self._blob is not None)

    def _stage_count(self) -> int:
        return int(self._baseline["stage_count"]) if self._online() else 0

    def _active_stage(self) -> int:
        if not self._online():
            return -1
        return self._baseline["active_stage"] if self._pending_stage is None else self._pending_stage

    def _charging_now(self) -> bool:
        if self._charging is not None:
            return self._charging
        return bool(self._wired) and 0 <= self._battery < 100      # Endgame: no charging flag, assume it

    # ---- properties
    # What the connected model offers (models.Model); the page shows only those controls.
    modelKey = Property(str, lambda s: s._model.key if s._known else "", notify=infoChanged)
    modelName = Property(str, lambda s: s._model.name if s._known else "", notify=infoChanged)
    brand = Property(str, lambda s: s._model.brand if s._known else "", notify=infoChanged)
    features = Property("QVariantList", lambda s: sorted(s._model.features), notify=infoChanged)
    maxStages = Property(int, lambda s: s._model.max_stages, notify=infoChanged)
    xyDpi = Property(bool, lambda s: "xy" in s._model.features, notify=infoChanged)
    pollingCodes = Property("QVariantList", lambda s: list(s._model.polling_codes), notify=infoChanged)
    pollingLabels = Property("QVariantList", lambda s: [p.label for p in s._model.polling], notify=infoChanged)
    pollingSubs = Property("QVariantList", lambda s: [p.sub for p in s._model.polling], notify=infoChanged)
    dpiMin = Property(int, lambda s: s._model.dpi_min, notify=infoChanged)
    dpiMax = Property(int, lambda s: s._model.dpi_max, notify=infoChanged)
    dpiStep = Property(int, lambda s: s._model.dpi_step, notify=infoChanged)
    wirelessText = Property(str, lambda s: s._model.wireless_text, notify=infoChanged)
    supportedNames = Property("QVariantList", lambda s: list(models.supported_names()), constant=True)
    # Razer power settings
    idleMin = Property(int, lambda s: razer.IDLE_MIN, constant=True)
    idleMax = Property(int, lambda s: razer.IDLE_MAX, constant=True)
    lowBatteryCodes = Property("QVariantList", lambda s: [c for c, _l in razer.LOW_BATTERY], constant=True)
    lowBatteryLabels = Property("QVariantList", lambda s: [l for _c, l in razer.LOW_BATTERY], constant=True)
    # Endgame Gear constants shared with its codec so the QML never duplicates an encoding.
    actionCodes = Property("QVariantList", lambda s: [(t << 8) | c for (t, c), _n in codec.ACTIONS], constant=True)
    actionNames = Property("QVariantList", lambda s: [n for _k, n in codec.ACTIONS], constant=True)
    buttonNames = Property("QVariantList", lambda s: list(codec.BUTTON_NAMES), constant=True)
    filterMax = Property(int, lambda s: codec.FILTER_MAX, constant=True)
    filterDefault = Property(int, lambda s: codec.FILTER_DEFAULT, constant=True)
    gxSpeed = Property(int, lambda s: codec.GX_SPEED, constant=True)
    gxSafe = Property(int, lambda s: codec.GX_SAFE, constant=True)
    lodNormalMm = Property("QVariantList", lambda s: list(codec.LOD_MM), constant=True)
    lodGlassCodes = Property("QVariantList", lambda s: list(codec.GLASS_LOD), constant=True)
    lodGlassMm = Property("QVariantList", lambda s: list(codec.GLASS_LOD.values()), constant=True)

    state = Property(str, lambda s: s._state, notify=stateChanged)
    busy = Property(bool, lambda s: s._state in BUSY_STATES, notify=stateChanged)
    progress = Property(float, lambda s: s._progress, notify=progressChanged)
    progressText = Property(str, lambda s: s._progress_text, notify=progressChanged)
    message = Property(str, lambda s: s._message, notify=messageChanged)
    messageKind = Property(str, lambda s: s._message_kind, notify=messageChanged)
    connection = Property(str, lambda s: "" if s._wired is None else ("wired" if s._wired else "wireless"),
                          notify=infoChanged)
    dongleFw = Property(str, lambda s: s._dongle_fw, notify=infoChanged)
    mouseFw = Property(str, lambda s: s._mouse_fw, notify=infoChanged)
    pidText = Property(str, lambda s: f"0x{s._pid:04X}" if s._pid else "", notify=infoChanged)
    usbIdText = Property(str, lambda s: f"{s._model.usb_vendor:04X}:{s._pid:04X}" if s._known and s._pid else "",
                         notify=infoChanged)
    battery = Property(int, lambda s: s._battery, notify=infoChanged)
    charging = Property(bool, lambda s: s._charging_now(), notify=infoChanged)
    writable = Property(bool, lambda s: s._writable, notify=infoChanged)
    detail = Property(str, lambda s: s._detail, notify=infoChanged)
    problem = Property(str, lambda s: s._problem, notify=infoChanged)
    hasBackup = Property(bool, lambda s: bool(s._backup), notify=infoChanged)
    backupPath = Property(str, lambda s: s._backup, notify=infoChanged)
    values = Property("QVariantMap", lambda s: {**NEUTRAL, **s._current()}, notify=valuesChanged)
    baseline = Property("QVariantMap", lambda s: dict(s._baseline), notify=infoChanged)
    editedKeys = Property("QVariantList", lambda s: list(s._staged), notify=valuesChanged)
    changeCount = Property(int, lambda s: len(s._codec.describe_changes(s._baseline, s._current())),
                           notify=valuesChanged)
    changeSummary = Property(str, lambda s: ", ".join(s._codec.describe_changes(s._baseline, s._current())),
                             notify=valuesChanged)
    editProblem = Property(str, lambda s: s._edit_problem, notify=valuesChanged)
    setups = Property("QVariantList", lambda s: s._setup_rows(), notify=setupsChanged)
    setupNames = Property("QVariantList", lambda s: [st.name for st in s._setup_list] if s._online() else [],
                          notify=setupsChanged)
    activeSetup = Property(str, lambda s: next((r["name"] for r in s._setup_rows() if r["active"]), ""),
                           notify=setupsChanged)
    online = Property(bool, lambda s: s._online(), notify=trayChanged)
    stageCount = Property(int, lambda s: s._stage_count(), notify=trayChanged)
    activeStage = Property(int, lambda s: s._active_stage(), notify=trayChanged)
    stageLabels = Property("QVariantList", lambda s: traymodel.stage_labels(s._baseline) if s._online() else [],
                           notify=trayChanged)
    stageReason = Property(str, lambda s: s._stage_reason(), notify=trayChanged)
    stagesEnabled = Property(bool, lambda s: not s._stage_reason() and s._state == "ready", notify=trayChanged)
