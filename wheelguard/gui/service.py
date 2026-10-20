"""The session-bus face of the app: owning the name is the single-instance lock, and the GNOME Shell
panel extension talks to the running app through it.

    name       io.github.djshiye.Wheelguard
    object     /io/github/djshiye/Wheelguard
    interface  io.github.djshiye.Wheelguard1
        GetState() -> a{sv}              everything the popup shows (see traymodel.state_dict)
        StateChanged(a{sv})              the same dict, emitted when something in it changed
        SetActiveStage(i)                switch the DPI stage (the guarded MouseController path)
        SetProtection(b)                 scroll protection on/off
        ShowWindow()                     bring the settings window forward
        Refresh()                        re-read the mouse quietly (rate-limited)
        read-only properties             the same keys, with org.freedesktop.DBus.Properties.PropertiesChanged
"""
from typing import Dict, Optional

from PySide6.QtCore import ClassInfo, Property, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusAbstractAdaptor, QDBusConnection, QDBusMessage

from . import traymodel

BUS_NAME = "io.github.djshiye.Wheelguard"
OBJECT_PATH = "/io/github/djshiye/Wheelguard"
INTERFACE = "io.github.djshiye.Wheelguard1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

PROPERTIES = ("Online", "Battery", "Charging", "StageLabels", "ActiveStage", "StageReason",
              "ProtectionEnabled", "DaemonState", "Preset", "Dropped")
IDLE_STATE = traymodel.state_dict(traymodel.TrayState())


def claim(bus: Optional[QDBusConnection] = None) -> bool:
    """Try to own the bus name. False means another instance has it (the request never queues)."""
    return (bus or QDBusConnection.sessionBus()).registerService(BUS_NAME)


def call_remote(method: str, bus: Optional[QDBusConnection] = None, timeout_ms: int = 1500) -> bool:
    """Call a no-argument method on the running instance. False when it didn't answer."""
    msg = QDBusMessage.createMethodCall(BUS_NAME, OBJECT_PATH, INTERFACE, method)
    reply = (bus or QDBusConnection.sessionBus()).call(msg, QDBus.CallMode.Block, timeout_ms)
    return reply.type() == QDBusMessage.MessageType.ReplyMessage


class Service(QObject):
    """State publisher and command router. The D-Bus adaptor below is a thin shell around the methods here."""

    stateChanged = Signal("QVariantMap")
    showRequested = Signal()

    def __init__(self, parent=None, bus: Optional[QDBusConnection] = None):
        super().__init__(parent)
        self._bus = bus
        self._daemon = self._mouse = None
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
        for sig in (daemon.stateChanged, daemon.configChanged, daemon.statsChanged, mouse.trayChanged):
            sig.connect(self._timer.start)
        self.publish()

    # ---- state
    def current(self) -> Dict[str, object]:
        return dict(self._state)

    @Slot()
    def publish(self):
        if self._daemon is None:
            return
        new = traymodel.state_dict(traymodel.snapshot(self._daemon, self._mouse))
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
