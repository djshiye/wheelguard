"""Ends a running Click-Lock when the screen locks, so a held or clicking button never carries on behind
the lock screen. GNOME's screensaver announces it on the session bus (org.gnome.ScreenSaver.ActiveChanged);
the app tells the daemon. Since 1.7.0 the daemon also watches for it by itself (wheelguard/screenwatch.py), so
this is the second line of defence and covers desktops that only have org.freedesktop.ScreenSaver.
No-op where the signal is absent."""
import logging

from PySide6.QtCore import SLOT, QObject, Slot
from PySide6.QtDBus import QDBusConnection

log = logging.getLogger("wheelguard")
SERVICES = (("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"))


class ScreenLockWatcher(QObject):
    def __init__(self, daemon, parent=None, bus=None):
        super().__init__(parent)
        self._daemon = daemon
        bus = bus or QDBusConnection.sessionBus()
        # SLOT(): QtDBus wants the slot with Qt's code prefix; a bare string loses its first character
        # and the connect silently fails
        self.connected = [bus.connect(svc, path, iface, "ActiveChanged", self, SLOT("onActiveChanged(bool)"))
                          for svc, path, iface in SERVICES]
        if not any(self.connected):
            log.warning("cannot watch the screen lock; Click-Lock will not end when the screen locks")

    @Slot(bool)
    def onActiveChanged(self, active: bool):
        if active:
            self._daemon.stopClickLock()
