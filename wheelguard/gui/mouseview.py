"""What the panel, the tray and the low-battery alerts see of the mouse: the MouseController for the mice
Wheelguard drives itself, else the battery UPower knows for the mouse being protected (wheelguard/gui/upower.py).

A mouse known only through UPower shows its battery and nothing else: no DPI stages, nothing to change.
"""
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .. import mice
from .upower import Reading

POLL_MS = 60000                 # UPower itself refreshes a mouse battery every few minutes at most


class MouseView(QObject):
    trayChanged = Signal()
    infoChanged = Signal()
    stateChanged = Signal()

    def __init__(self, mouse, daemon, battery: Optional[Callable[[int, int], Optional[Reading]]] = None,
                 parent=None, poll_ms: int = POLL_MS):
        super().__init__(parent)
        self._mouse, self._daemon = mouse, daemon
        if battery is None:
            from .upower import UPowerClient
            battery = UPowerClient().battery
        self._battery = battery
        self._reading: Optional[Reading] = None
        mouse.trayChanged.connect(self._on_mouse)
        mouse.infoChanged.connect(self.infoChanged)
        mouse.stateChanged.connect(self._on_mouse_state)
        self._seen = None                       # (daemon state, ident) last polled for
        daemon.stateChanged.connect(self._on_daemon)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.poll()

    # ---- the fallback battery
    def _wanted(self):
        """(vendor, product) to ask UPower about, or None when the mouse's own driver answers or nothing is
        being protected."""
        if self._mouse.online or self._daemon.state != "running" or not self._daemon.ident:
            return None
        return mice.parse_choice(self._daemon.ident)

    @Slot()
    def poll(self):
        ids = self._wanted()
        try:
            reading = self._battery(*ids) if ids else None
        except Exception:           # a misbehaving system service must never take the app down
            reading = None
        if reading != self._reading:
            self._reading = reading
            self.trayChanged.emit()
            self.infoChanged.emit()

    def _on_daemon(self):
        seen = (self._daemon.state, self._daemon.ident)
        if seen != self._seen:                  # status updates arrive often: ask UPower only when it matters
            self._seen = seen
            self.poll()

    def _on_mouse(self):
        if self._mouse.online and self._reading is not None:
            self._reading = None        # the driver answers now
        self.trayChanged.emit()

    def _on_mouse_state(self):
        self.stateChanged.emit()
        if not self._mouse.online:
            QTimer.singleShot(0, self.poll)     # the driver lost the mouse: UPower may still know its battery

    # ---- what traymodel.snapshot() and the alerts read
    @property
    def fallback(self) -> bool:
        return not self._mouse.online and self._reading is not None

    @property
    def online(self) -> bool:
        return bool(self._mouse.online) or self._reading is not None

    @property
    def battery(self) -> int:
        return self._reading.percent if self.fallback else self._mouse.battery

    @property
    def charging(self) -> bool:
        return self._reading.charging if self.fallback else bool(self._mouse.charging)

    @property
    def stageLabels(self):
        return [] if self.fallback else self._mouse.stageLabels

    @property
    def activeStage(self) -> int:
        return -1 if self.fallback else self._mouse.activeStage

    @property
    def stageReason(self) -> str:
        return "" if self.fallback else self._mouse.stageReason

    # ---- commands go to the real controller
    def setActiveStage(self, index: int):
        if not self.fallback:
            self._mouse.setActiveStage(index)

    def refreshBattery(self):
        self._mouse.refreshBattery()
        self.poll()

    def refreshQuietly(self):
        self._mouse.refreshQuietly()
        self.poll()
