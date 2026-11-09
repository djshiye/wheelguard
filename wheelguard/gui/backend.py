"""QObject backends exposed to QML: daemon client, GNOME pointer settings, appearance, Liquid Glass."""
import json
import os
from typing import Optional

from PySide6.QtCore import Property, QObject, QProcess, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

from . import autostart, gsettings, presets, protocol

SERVICE = "wheelguard"
DEBOUNCE_MS = 250
BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 5.0)


def default_socket_path() -> str:
    override = os.environ.get("WHEELGUARD_SOCKET")
    if override:
        return override
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "wheelguard.sock")


class DaemonClient(QObject):
    """Non-blocking client for the wheelguard socket (QLocalSocket, auto-reconnect with backoff).

    state: "connecting" | "stopped" | "permission" | "waiting" | "running"
    The displayed config (enabled/preMs/holdMs/lockMs) is what the sliders show; it follows the
    user's edits immediately, is sent to the daemon after a debounce, and snaps back to the
    daemon's config if the daemon answers with an error.
    """

    stateChanged = Signal()
    configChanged = Signal()
    statsChanged = Signal()
    editErrorChanged = Signal()
    serviceChanged = Signal()
    clickLockActiveChanged = Signal()
    miceChanged = Signal()
    tick = Signal(int, str, bool, float)     # sign, kind, held, t

    def __init__(self, path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._path = path or default_socket_path()
        self._state = "connecting"
        self._device = ""
        self._label = ""
        self._detail = ""
        self._version = ""
        self._server_cfg = dict(protocol.CONFIG_DEFAULTS)
        self._clicklock_active = False
        self._mice: list = []
        self._cfg = dict(self._server_cfg)
        self._staged: dict = {}
        self._inflight = 0
        self._ticks = 0
        self._dropped = 0
        self._debounced = 0
        self._last_debounce_ms = protocol.DEBOUNCE_DEFAULT_ON_MS
        self._edit_error = ""
        self._service_busy = False
        self._service_error = ""
        self._attempt = 0

        self._lines = protocol.LineBuffer()
        self._queue = protocol.CommandQueue()
        self._sock = QLocalSocket(self)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_lost)
        self._sock.readyRead.connect(self._on_ready)
        self._sock.errorOccurred.connect(self._on_error)

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._connect)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._send_staged)
        self._err_timer = QTimer(self)
        self._err_timer.setSingleShot(True)
        self._err_timer.setInterval(7000)
        self._err_timer.timeout.connect(self._clear_edit_error)
        self._proc: Optional[QProcess] = None

    # --- connection lifecycle
    @Slot()
    def start(self):
        self._connect()

    @Slot()
    def reconnectNow(self):
        self._retry.stop()
        if self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._connect()

    def _connect(self):
        if self._sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            return
        self._sock.connectToServer(self._path)

    def _on_connected(self):
        self._attempt = 0
        self._lines.clear()
        self._queue.clear()
        self._send({"cmd": "subscribe"})

    def _on_error(self, _err):
        # Connection refused / not found / peer closed: all mean "no usable daemon right now".
        self._lost()

    def _on_lost(self):
        self._lost()

    def _lost(self):
        self._queue.clear()
        self._inflight = 0
        if self._sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self._sock.abort()
        self._set_state("stopped", "", "")
        if self._clicklock_active:
            self._clicklock_active = False
            self.clickLockActiveChanged.emit()
        if not self._retry.isActive():
            delay = BACKOFF_S[min(self._attempt, len(BACKOFF_S) - 1)]
            self._attempt += 1
            self._retry.start(int(delay * 1000))

    def _set_state(self, state, device, detail, label=None):
        label = device if label is None else label
        if (state, device, detail, label) != (self._state, self._device, self._detail, self._label):
            self._state, self._device, self._detail, self._label = state, device, detail, label
            self.stateChanged.emit()

    def _send(self, msg: dict, track: Optional[str] = None):
        if self._sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
            return False
        self._sock.write(protocol.encode(msg))
        if track:
            self._queue.push(track)
        return True

    # --- incoming
    def _on_ready(self):
        for line in self._lines.feed(bytes(self._sock.readAll())):
            msg = protocol.parse(line)
            if msg is not None:
                self._dispatch(msg)

    def _dispatch(self, msg: dict):
        t = msg["type"]
        if t == "tick":
            tk = protocol.as_tick(msg)
            if tk is not None:
                if tk.kind != "hold":
                    self._ticks += 1
                    self._dropped += tk.kind == "drop"
                    self.statsChanged.emit()
                self.tick.emit(tk.sign, tk.kind, tk.held, tk.t)
        elif t == "mice":
            self._set_mice(msg.get("mice"))
        elif t == "status":
            self._on_status(msg)
        elif t in ("ok", "error"):
            self._on_reply(msg)

    def _on_status(self, msg: dict):
        ds = protocol.device_state(msg)
        self._version = str(msg.get("version") or "")
        self._set_state(ds.state, ds.device, ds.error, ds.label)
        cfg = protocol.status_config(msg)
        if cfg is not None:
            self._server_cfg = cfg
            if not self._dirty():
                self._set_cfg(cfg)
        if "mice" in msg:
            self._set_mice(msg.get("mice"))
        active = protocol.clicklock_active(msg)
        if active != self._clicklock_active:
            self._clicklock_active = active
            self.clickLockActiveChanged.emit()
        st = protocol.status_stats(msg)
        if (st["ticks"], st["dropped"], st["debounced"]) != (self._ticks, self._dropped, self._debounced):
            self._ticks, self._dropped, self._debounced = st["ticks"], st["dropped"], st["debounced"]
            self.statsChanged.emit()

    def _set_mice(self, mice):
        mice = mice if isinstance(mice, list) else []       # raw dicts from the daemon; protocol.mouse_options vets them
        if mice != self._mice:
            self._mice = mice
            self.miceChanged.emit()

    def _on_reply(self, msg: dict):
        res = self._queue.resolve(msg)
        if res is None:
            return
        name, err = res
        if name != "set":
            return
        self._inflight = max(0, self._inflight - 1)
        if err is not None:
            self._staged.clear()
            self._debounce.stop()
            self._inflight = 0
            self._set_edit_error(err)
            self._set_cfg(self._server_cfg)   # revert

    def _dirty(self) -> bool:
        return bool(self._staged) or self._inflight > 0 or self._debounce.isActive()

    # --- config editing
    def _set_cfg(self, cfg: dict):
        if cfg != self._cfg:
            self._cfg = dict(cfg)
            self.configChanged.emit()
            self.miceChanged.emit()         # the picker's selection follows the config

    def _stage(self, patch: dict, immediate=False):
        if self._state == "stopped":
            return
        self._staged.update(patch)
        merged = dict(self._cfg)
        merged.update(patch)
        keys = [k for k in patch if k in ("pre_ms", "hold_ms", "lock_ms")]
        if keys:
            norm = presets.normalize(merged, keys[0])
            for k in ("pre_ms", "hold_ms", "lock_ms"):
                if norm[k] != merged[k]:
                    self._staged[k] = norm[k]
            merged = norm
        self._clear_edit_error()
        self._set_cfg(merged)
        if immediate:
            self._debounce.stop()
            self._send_staged()
        else:
            self._debounce.start()

    def _send_staged(self):
        if not self._staged:
            return
        patch, self._staged = self._staged, {}
        if self._send({"cmd": "set", "config": patch}, track="set"):
            self._inflight += 1
        else:
            self._set_cfg(self._server_cfg)

    @Slot(bool)
    def setEnabled(self, on: bool):
        self._stage({"enabled": bool(on)}, immediate=True)

    @Slot(str, int)
    def setMs(self, key: str, value: int):
        if key in ("pre_ms", "hold_ms", "lock_ms"):
            self._stage({key: int(value)})

    @Slot(bool)
    def setClickLock(self, on: bool):
        self._stage({"clicklock": bool(on)}, immediate=True)

    @Slot(int)
    def setClickLockMs(self, value: int):
        lo, hi = protocol.CLICKLOCK_MS_RANGE
        self._stage({"clicklock_ms": max(lo, min(hi, int(value)))})

    @Slot(int)
    def setClickLockRate(self, value: int):
        self._stage({"clicklock_rate": max(1, min(30, int(value)))})

    @Slot(int)
    def setDebounceMs(self, value: int, immediate=False):
        """The Double-click fix window in ms: 0 is off, otherwise clamped to the slider range."""
        v = int(value)
        lo, hi = protocol.DEBOUNCE_MS_RANGE
        v = 0 if v <= 0 else max(lo, min(hi, v))
        if v:
            self._last_debounce_ms = v
        self._stage({"debounce_ms": v}, immediate=immediate or v == 0)

    @Slot(bool)
    def setDebounce(self, on: bool):
        self.setDebounceMs(self._last_debounce_ms if on else 0, immediate=True)

    @Slot()
    def refreshMice(self):
        """Ask the daemon which mice with a scroll wheel are connected (it rescans at most every few seconds)."""
        self._send({"cmd": "mice"})

    @Slot(str)
    def setMouse(self, choice: str):
        """"auto" or the "vvvv:pppp" id of a mouse from the list."""
        if choice == "auto" or protocol.mouse_ids.parse_choice(choice):
            self._stage({"mouse": choice}, immediate=True)

    @Slot(bool)
    def setClickLockIgnoreDrag(self, on: bool):
        self._stage({"clicklock_ignore_drag": bool(on)}, immediate=True)

    @Slot(int)
    def setClickLockMaxS(self, seconds: int):
        self._stage({"clicklock_max_s": 0 if int(seconds) <= 0 else max(10, min(3600, int(seconds)))},
                    immediate=True)

    @Slot()
    def stopClickLock(self):
        """End a running Click-Lock now (screen lock, or the user asking). Harmless when none runs."""
        self._send({"cmd": "clicklock_stop"}, track="clicklock_stop")

    @Slot(str)
    def setClickLockMode(self, mode: str):
        if mode in protocol.CLICKLOCK_MODES:
            self._stage({"clicklock_mode": mode}, immediate=True)

    @Slot(int)
    def applyPreset(self, index: int):
        if 0 <= index < len(presets.PRESETS):
            self._stage(presets.preset_config(index))

    @Slot()
    def refreshStatus(self):
        self._send({"cmd": "status"})
        self.reconnectNow()

    @Slot()
    def resetStats(self):
        self._send({"cmd": "reset_stats"}, track="reset_stats")

    @Slot(result="QVariantList")
    def presetList(self):
        return [{"name": p.name, "pre": p.pre_ms, "hold": p.hold_ms, "lock": p.lock_ms,
                 "summary": p.summary} for p in presets.PRESETS]

    def _set_edit_error(self, text: str):
        self._edit_error = text
        self.editErrorChanged.emit()
        self._err_timer.start()

    def _clear_edit_error(self):
        if self._edit_error:
            self._edit_error = ""
            self.editErrorChanged.emit()

    @Slot()
    def dismissEditError(self):
        self._clear_edit_error()

    # --- service control
    @Slot()
    def startService(self):
        if self._service_busy:
            return
        self._service_busy, self._service_error = True, ""
        self.serviceChanged.emit()
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(lambda code, _status, p=proc: self._service_done(p, code))
        proc.errorOccurred.connect(lambda _e, p=proc: self._service_done(p, -1))
        self._proc = proc
        proc.start("systemctl", ["--user", "start", SERVICE])

    def _service_done(self, proc: QProcess, code: int):
        if proc is not self._proc:
            return
        self._proc = None
        out = bytes(proc.readAll()).decode(errors="replace").strip()
        self._service_busy = False
        self._service_error = "" if code == 0 else (out or "Could not run systemctl")
        self.serviceChanged.emit()
        proc.deleteLater()
        if code == 0:
            QTimer.singleShot(400, self.reconnectNow)

    # --- properties
    state = Property(str, lambda s: s._state, notify=stateChanged)
    device = Property(str, lambda s: s._device, notify=stateChanged)
    deviceLabel = Property(str, lambda s: s._label, notify=stateChanged)   # "Endgame Gear XM2w 4k v2 · dongle"
    detail = Property(str, lambda s: s._detail, notify=stateChanged)
    version = Property(str, lambda s: s._version, notify=stateChanged)
    enabled = Property(bool, lambda s: s._cfg["enabled"], notify=configChanged)
    preMs = Property(int, lambda s: s._cfg["pre_ms"], notify=configChanged)
    holdMs = Property(int, lambda s: s._cfg["hold_ms"], notify=configChanged)
    lockMs = Property(int, lambda s: s._cfg["lock_ms"], notify=configChanged)
    presetIndex = Property(int, lambda s: presets.preset_index(
        s._cfg["pre_ms"], s._cfg["hold_ms"], s._cfg["lock_ms"]), notify=configChanged)
    presetName = Property(str, lambda s: presets.preset_name(
        s._cfg["pre_ms"], s._cfg["hold_ms"], s._cfg["lock_ms"]), notify=configChanged)
    presetSummary = Property(str, lambda s: presets.preset_summary(
        s._cfg["pre_ms"], s._cfg["hold_ms"], s._cfg["lock_ms"]), notify=configChanged)
    clickLock = Property(bool, lambda s: s._cfg["clicklock"], notify=configChanged)
    clickLockMs = Property(int, lambda s: s._cfg["clicklock_ms"], notify=configChanged)
    clickLockMode = Property(str, lambda s: s._cfg["clicklock_mode"], notify=configChanged)
    clickLockRate = Property(int, lambda s: s._cfg["clicklock_rate"], notify=configChanged)
    debounceMs = Property(int, lambda s: s._cfg["debounce_ms"], notify=configChanged)
    debounceOn = Property(bool, lambda s: s._cfg["debounce_ms"] > 0, notify=configChanged)
    debouncedClicks = Property(int, lambda s: s._debounced, notify=statsChanged)
    mouseChoice = Property(str, lambda s: s._cfg["mouse"], notify=configChanged)
    mouseLabels = Property("QStringList",
                           lambda s: [o["label"] for o in protocol.mouse_options(s._mice, s._cfg["mouse"])],
                           notify=miceChanged)
    mouseIds = Property("QStringList",
                        lambda s: [o["id"] for o in protocol.mouse_options(s._mice, s._cfg["mouse"])],
                        notify=miceChanged)
    mouseIndex = Property(int, lambda s: protocol.mouse_index(protocol.mouse_options(s._mice, s._cfg["mouse"]),
                                                              s._cfg["mouse"]), notify=miceChanged)
    clickLockIgnoreDrag = Property(bool, lambda s: s._cfg["clicklock_ignore_drag"], notify=configChanged)
    clickLockMaxS = Property(int, lambda s: s._cfg["clicklock_max_s"], notify=configChanged)
    clickLockActive = Property(bool, lambda s: s._clicklock_active, notify=clickLockActiveChanged)
    ticks = Property(int, lambda s: s._ticks, notify=statsChanged)
    dropped = Property(int, lambda s: s._dropped, notify=statsChanged)
    blockRate = Property(float, lambda s: protocol.block_rate(s._ticks, s._dropped), notify=statsChanged)
    editError = Property(str, lambda s: s._edit_error, notify=editErrorChanged)
    serviceBusy = Property(bool, lambda s: s._service_busy, notify=serviceChanged)
    serviceError = Property(str, lambda s: s._service_error, notify=serviceChanged)


class PointerSettings(QObject):
    """org.gnome.desktop.peripherals.mouse via the gsettings CLI. Reads on start/focus, writes debounced."""

    changed = Signal()
    availableChanged = Signal()

    KEYMAP = {  # qml name -> gsettings key
        "speed": "speed", "accelProfile": "accel-profile", "naturalScroll": "natural-scroll",
        "leftHanded": "left-handed", "middleClickEmulation": "middle-click-emulation",
        "doubleClick": "double-click", "dragThreshold": "drag-threshold",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._v = {k: gsettings.KEYS[k][3] for k in gsettings.KEYS}
        self._available = gsettings.available()
        self._pending: dict = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._flush)

    @Slot()
    def refresh(self):
        vals = gsettings.read_all()
        avail = vals is not None
        if avail != self._available:
            self._available = avail
            self.availableChanged.emit()
        if not vals:
            return
        changed = False
        for k, v in vals.items():
            if k in self._pending:
                continue
            if self._v.get(k) != v:
                self._v[k] = v
                changed = True
        if changed:
            self.changed.emit()

    def _user_set(self, key: str, value):
        value = gsettings.coerce(key, value)
        if self._v[key] == value and key not in self._pending:
            return
        self._v[key] = value
        self._pending[key] = value
        self.changed.emit()
        self._timer.start()

    @Slot()
    def _flush(self):
        pending, self._pending = self._pending, {}
        for k, v in pending.items():
            gsettings.write(k, v)

    def flush(self):
        self._timer.stop()
        self._flush()

    available = Property(bool, lambda s: s._available, notify=availableChanged)
    speed = Property(float, lambda s: s._v["speed"],
                     lambda s, v: s._user_set("speed", v), notify=changed)
    accelProfile = Property(str, lambda s: s._v["accel-profile"],
                            lambda s, v: s._user_set("accel-profile", v), notify=changed)
    naturalScroll = Property(bool, lambda s: s._v["natural-scroll"],
                             lambda s, v: s._user_set("natural-scroll", v), notify=changed)
    leftHanded = Property(bool, lambda s: s._v["left-handed"],
                          lambda s, v: s._user_set("left-handed", v), notify=changed)
    middleClickEmulation = Property(bool, lambda s: s._v["middle-click-emulation"],
                                    lambda s, v: s._user_set("middle-click-emulation", v), notify=changed)
    doubleClick = Property(int, lambda s: s._v["double-click"],
                           lambda s, v: s._user_set("double-click", v), notify=changed)
    dragThreshold = Property(int, lambda s: s._v["drag-threshold"],
                             lambda s, v: s._user_set("drag-threshold", v), notify=changed)


class Autostart(QObject):
    """The 'Start in tray at login' switch on the About page (see autostart.py)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = autostart.is_enabled()

    @Slot(bool)
    def setEnabled(self, on: bool):
        try:
            autostart.set_enabled(bool(on))
        except OSError:
            pass
        self._on = autostart.is_enabled()
        self.changed.emit()

    available = Property(bool, lambda s: autostart.available(), constant=True)
    enabled = Property(bool, lambda s: s._on, notify=changed)


class Appearance(QObject):
    """Follows org.gnome.desktop.interface color-scheme (live, via `gsettings monitor`)."""

    changed = Signal()

    def __init__(self, override: Optional[bool] = None, parent=None):
        super().__init__(parent)
        self._override = override
        self._dark = override if override is not None else (
            gsettings.prefers_dark() if gsettings.available() else True)
        self._proc: Optional[QProcess] = None

    @Slot()
    def watch(self):
        if self._override is not None or not gsettings.available() or self._proc:
            return
        p = QProcess(self)
        p.readyReadStandardOutput.connect(self._on_output)
        p.start("gsettings", ["monitor", gsettings.INTERFACE_SCHEMA, "color-scheme"])
        self._proc = p

    def _on_output(self):
        text = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            if "color-scheme" in line:
                self._set(gsettings.prefers_dark(line))

    def _set(self, dark: bool):
        if dark != self._dark:
            self._dark = dark
            self.changed.emit()

    def shutdown(self):
        if self._proc:
            self._proc.kill()
            self._proc.waitForFinished(500)

    dark = Property(bool, lambda s: s._dark, notify=changed)


class LiquidGlass(QObject):
    """Colours from the Liquid Glass theme (github.com/djshiye/fedora-liquid-glass), when it's in use.

    Liquid Glass writes its design tokens to ~/.config/liquid-glass/tokens-{dark,light}.json on every
    apply. While those files exist the app takes its surfaces, text and accent from them, so it matches
    the rest of the desktop; without them it keeps its own look. The folder is watched, so a change in
    the Liquid Glass app shows up here immediately.
    """

    changed = Signal()

    # Theme.qml role -> Liquid Glass token
    ROLES = {
        "chrome": "chrome", "panel": "window", "card": "card", "cardHi": "active", "inset": "view",
        "track": "edge", "border": "edge", "borderHi": "highlight", "hover": "hover",
        "text": "fg", "textMuted": "fg_dim", "textFaint": "fg_faint",
        "accentA": "accent", "accentB": "accent_text",
        "coral": "destructive", "amber": "warning", "good": "success",
    }

    def __init__(self, directory: Optional[str] = None, parent=None):
        super().__init__(parent)
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        self._dir = directory or os.path.join(base, "liquid-glass")
        self._tokens: dict = {}
        self._watcher = None
        self._debounce = QTimer(self, singleShot=True, interval=150, timeout=self.reload)
        self.reload()

    @Slot()
    def watch(self):
        from PySide6.QtCore import QFileSystemWatcher
        if self._watcher is None:
            self._watcher = QFileSystemWatcher(self)
            self._watcher.directoryChanged.connect(lambda _p: self._debounce.start())
            self._watcher.fileChanged.connect(lambda _p: self._debounce.start())
        parent = os.path.dirname(self._dir)
        for p in (parent, self._dir, *self._files()):
            if os.path.exists(p) and p not in self._watcher.directories() + self._watcher.files():
                self._watcher.addPath(p)

    def _files(self):
        return [os.path.join(self._dir, f"tokens-{s}.json") for s in ("dark", "light")]

    @Slot()
    def reload(self):
        tokens = {}
        for scheme, path in zip(("dark", "light"), self._files()):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("version") == 1:
                tokens[scheme] = data
        if tokens != self._tokens:
            self._tokens = tokens
            self.changed.emit()
        if self._watcher is not None:
            self.watch()            # files that were replaced need re-adding

    @Slot(bool, str, str, result=str)
    def color(self, dark: bool, role: str, fallback: str) -> str:
        """The token colour for a Theme.qml role, or the fallback (an "#RRGGBBAA" token becomes Qt's "#AARRGGBB")."""
        value = self._tokens.get("dark" if dark else "light", {}).get(self.ROLES.get(role, ""))
        if not (isinstance(value, str) and value.startswith("#") and len(value) in (7, 9)):
            return fallback
        return value if len(value) == 7 else "#" + value[7:9] + value[1:7]

    @Slot(bool, str, int, result=int)
    def number(self, dark: bool, name: str, fallback: int) -> int:
        value = self._tokens.get("dark" if dark else "light", {}).get(name)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback

    active = Property(bool, lambda s: bool(s._tokens), notify=changed)
