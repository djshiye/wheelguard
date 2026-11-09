"""The session-bus face of the app: owning the name is the single-instance lock, and the GNOME Shell
panel extension talks to the running app through it.

    name       io.github.djshiye.Wheelguard
    object     /io/github/djshiye/Wheelguard
    interface  io.github.djshiye.Wheelguard1
        GetState() -> a{sv}              everything the popup shows (see traymodel.state_dict)
        StateChanged(a{sv})              the same dict, emitted when something in it changed
        SetActiveStage(i)                switch the DPI stage (the guarded MouseController path)
        SetProtection(b)                 scroll protection on/off
        SetClickLock(b)                  Click-Lock on/off
        SetClickLockMs(i)                Click-Lock hold time in ms (clamped to the slider range)
        SetClickLockMode(s)              "repeat" (keep clicking) or "hold" (keep the button pressed)
        StopClickLock()                  end a running Click-Lock now (the shell extension calls this when the screen locks)
        ToggleClickLock() -> s           flip Click-Lock; "ok:Click-Lock on" or "error:<why>" (commands.py)
        NextDpiStage() -> s              switch to the next DPI stage, wrapping round; same reply format
        FocusChanged(s)                  the shell's focused app id ("" = none); per-game profiles (profilectl.py)
        ShowWindow()                     bring the settings window forward
        Refresh()                        re-read the mouse quietly (rate-limited)
        read-only properties             the same keys, with org.freedesktop.DBus.Properties.PropertiesChanged
"""
from typing import Dict, Optional

from PySide6.QtCore import ClassInfo, Property, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusAbstractAdaptor, QDBusConnection, QDBusMessage

from . import commands, profiles, protocol, traymodel

BUS_NAME = "io.github.djshiye.Wheelguard"
OBJECT_PATH = "/io/github/djshiye/Wheelguard"
INTERFACE = "io.github.djshiye.Wheelguard1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

PROPERTIES = ("Online", "Battery", "Charging", "StageLabels", "ActiveStage", "StageReason",
              "ProtectionEnabled", "DaemonState", "Preset", "Dropped",
              "ClickLockEnabled", "ClickLockMs", "ClickLockActive", "ClickLockMode", "BatteryLow",
              "ProfilesEnabled", "ActiveProfile")
IDLE_STATE = {**traymodel.state_dict(traymodel.TrayState()), "ProfilesEnabled": False, "ActiveProfile": ""}


def claim(bus: Optional[QDBusConnection] = None) -> bool:
    """Try to own the bus name. False means another instance has it (the request never queues)."""
    return (bus or QDBusConnection.sessionBus()).registerService(BUS_NAME)


def call_remote(method: str, bus: Optional[QDBusConnection] = None, timeout_ms: int = 1500) -> bool:
    """Call a no-argument method on the running instance. False when it didn't answer."""
    msg = QDBusMessage.createMethodCall(BUS_NAME, OBJECT_PATH, INTERFACE, method)
    reply = (bus or QDBusConnection.sessionBus()).call(msg, QDBus.CallMode.Block, timeout_ms)
    return reply.type() == QDBusMessage.MessageType.ReplyMessage


def call_remote_text(method: str, bus: Optional[QDBusConnection] = None, timeout_ms: int = 4000) -> Optional[str]:
    """Call a no-argument method that returns a string on the running instance; None when nobody answered."""
    msg = QDBusMessage.createMethodCall(BUS_NAME, OBJECT_PATH, INTERFACE, method)
    reply = (bus or QDBusConnection.sessionBus()).call(msg, QDBus.CallMode.Block, timeout_ms)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
        return None
    return str(reply.arguments()[0])


class Service(QObject):
    """State publisher and command router. The D-Bus adaptor below is a thin shell around the methods here."""

    stateChanged = Signal("QVariantMap")
    showRequested = Signal()
    focusChanged = Signal(str)          # a validated app id, or "" for no app

    def __init__(self, parent=None, bus: Optional[QDBusConnection] = None):
        super().__init__(parent)
        self._bus = bus
        self._daemon = self._mouse = self._profiles = None
        self.pending_show = False           # ShowWindow arrived before the window existed
        self._attached = False
        self._registered = False            # only an exported object may emit PropertiesChanged
        self._state: Dict[str, object] = dict(IDLE_STATE)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)          # coalesce a burst of property changes into one signal
        self._timer.timeout.connect(self.publish)
        self.adaptor = Adaptor(self)

    def register(self) -> bool:
        bus = self._bus or QDBusConnection.sessionBus()
        self._registered = bus.registerObject(OBJECT_PATH, self)
        return self._registered

    def attach(self, daemon, mouse):
        """The backends exist now: start mirroring them."""
        self._daemon, self._mouse = daemon, mouse
        self._attached = True
        for sig in (daemon.stateChanged, daemon.configChanged, daemon.statsChanged,
                    daemon.clickLockActiveChanged, mouse.trayChanged):
            sig.connect(self._timer.start)
        self.publish()

    def attach_profiles(self, ctrl):
        """Per-game profiles: the popup shows when they are on and which one is engaged."""
        self._profiles = ctrl
        ctrl.changed.connect(self._timer.start)
        self.publish()

    # ---- state
    def current(self) -> Dict[str, object]:
        return dict(self._state)

    @Slot()
    def publish(self):
        if self._daemon is None:
            return
        new = traymodel.state_dict(traymodel.snapshot(self._daemon, self._mouse))
        p = self._profiles
        new["ProfilesEnabled"] = bool(p is not None and p.enabled)
        new["ActiveProfile"] = str(p.activeName) if p is not None and p.enabled else ""
        changed = {k: v for k, v in new.items() if self._state.get(k) != v}
        if not changed:
            return
        self._state = new
        self.stateChanged.emit(new)
        if self._registered:
            self.adaptor.propertiesChanged({k: v for k, v in changed.items() if k in PROPERTIES})

    # ---- commands (what the D-Bus methods do)
    def set_active_stage(self, index: int):
        if self._mouse is not None:
            self._mouse.setActiveStage(int(index))      # refuses staged edits, read-only mice, busy states

    def set_protection(self, on: bool):
        d = self._daemon
        if d is not None and d.state in ("running", "waiting"):
            d.setEnabled(bool(on))

    def set_clicklock(self, on: bool):
        d = self._daemon
        if d is not None and d.state in ("running", "waiting"):
            d.setClickLock(bool(on))

    def set_clicklock_ms(self, ms: int):
        d = self._daemon
        if d is not None and d.state in ("running", "waiting"):
            d.setClickLockMs(int(ms))

    def set_clicklock_mode(self, mode: str):
        d = self._daemon
        if d is not None and d.state in ("running", "waiting") and mode in protocol.CLICKLOCK_MODES:
            d.setClickLockMode(mode)

    def stop_clicklock(self):
        d = self._daemon
        if d is not None and d.state in ("running", "waiting"):
            d.stopClickLock()

    def focus_changed(self, app_id: str):
        """The shell reports the focused app's id (nothing else). Anything that isn't a plausible id is "none"."""
        self.focusChanged.emit(profiles.clean_app_id(app_id))

    def toggle_clicklock(self) -> str:
        d = self._daemon
        if d is None or d.state not in ("running", "waiting"):
            return commands.encode_result(False, commands.NO_SERVICE)
        on = not d.clickLock
        d.setClickLock(on)
        return commands.encode_result(True, "Click-Lock on" if on else "Click-Lock off")

    def next_dpi_stage(self) -> str:
        m = self._mouse
        if m is None:
            return commands.encode_result(False, "Wheelguard is still starting. Try again in a moment.")
        labels = list(m.stageLabels)
        if m.stageReason or not labels:
            return commands.encode_result(False, m.stageReason or "There are no DPI stages to switch.")
        if not getattr(m, "stagesEnabled", True):
            return commands.encode_result(False, "The mouse is busy. Try again in a moment.")
        nxt = (m.activeStage + 1) % len(labels)
        m.setActiveStage(nxt)
        return commands.encode_result(True, f"DPI {labels[nxt]}")

    def show_window(self):
        if self._attached:
            self.showRequested.emit()
        else:
            self.pending_show = True        # app.py shows the window once it exists

    def refresh(self):
        if self._mouse is not None:
            self._mouse.refreshQuietly()


def _prop(key, typ):
    return Property(typ, lambda self: self.parent().current()[key], constant=False)


@ClassInfo({"D-Bus Interface": INTERFACE})
class Adaptor(QDBusAbstractAdaptor):
    StateChanged = Signal("QVariantMap")

    def __init__(self, service: Service):
        super().__init__(service)
        self.setAutoRelaySignals(False)
        service.stateChanged.connect(self.StateChanged)

    @Slot(result="QVariantMap")
    def GetState(self):
        return self.parent().current()

    @Slot(int)
    def SetActiveStage(self, index):
        self.parent().set_active_stage(index)

    @Slot(bool)
    def SetProtection(self, on):
        self.parent().set_protection(on)

    @Slot(bool)
    def SetClickLock(self, on):
        self.parent().set_clicklock(on)

    @Slot(int)
    def SetClickLockMs(self, ms):
        self.parent().set_clicklock_ms(ms)

    @Slot(str)
    def SetClickLockMode(self, mode):
        self.parent().set_clicklock_mode(mode)

    @Slot(result=str)
    def ToggleClickLock(self):
        return self.parent().toggle_clicklock()

    @Slot(result=str)
    def NextDpiStage(self):
        return self.parent().next_dpi_stage()

    @Slot(str)
    def FocusChanged(self, app_id):
        self.parent().focus_changed(app_id)

    @Slot()
    def StopClickLock(self):
        self.parent().stop_clicklock()

    @Slot()
    def ShowWindow(self):
        self.parent().show_window()

    @Slot()
    def Refresh(self):
        self.parent().refresh()

    def propertiesChanged(self, changed: Dict[str, object]):
        msg = QDBusMessage.createSignal(OBJECT_PATH, PROPS_IFACE, "PropertiesChanged")
        msg.setArguments([INTERFACE, changed, []])
        (self.parent()._bus or QDBusConnection.sessionBus()).send(msg)

    Online = _prop("Online", bool)
    Battery = _prop("Battery", int)
    Charging = _prop("Charging", bool)
    StageLabels = _prop("StageLabels", "QStringList")
    ActiveStage = _prop("ActiveStage", int)
    StageReason = _prop("StageReason", str)
    ProtectionEnabled = _prop("ProtectionEnabled", bool)
    DaemonState = _prop("DaemonState", str)
    Preset = _prop("Preset", str)
    Dropped = _prop("Dropped", int)
    ClickLockEnabled = _prop("ClickLockEnabled", bool)
    ClickLockMs = _prop("ClickLockMs", int)
    ClickLockActive = _prop("ClickLockActive", bool)
    ClickLockMode = _prop("ClickLockMode", str)
    BatteryLow = _prop("BatteryLow", bool)
    ProfilesEnabled = _prop("ProfilesEnabled", bool)
    ActiveProfile = _prop("ActiveProfile", str)
