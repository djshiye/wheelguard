"""The GNOME Shell panel extension from the app's side: is it running, and (once) switch it on.

The extension replaces the tray icon, so while it is active the app must not also show an
StatusNotifierItem. The shell reports extension state on org.gnome.Shell.Extensions; this module only
reads it (and calls EnableExtension once, from a `--tray` start, when the extension is installed but off).
"""
import os
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusError, QDBusMessage

UUID = "wheelguard@djshiye.github.io"
SHELL = "org.gnome.Shell.Extensions"
SHELL_PATH = "/org/gnome/Shell/Extensions"
MARKER = "extension-enabled"        # in ~/.config/wheelguard: we switched it on (or saw it on) once

STATE_ACTIVE = 1
STATE_ERROR, STATE_OUT_OF_DATE, STATE_DOWNLOADING, STATE_UNINSTALLED = 3, 4, 5, 99


def is_active(info: Dict[str, object]) -> bool:
    return bool(info) and info.get("state") == STATE_ACTIVE and info.get("enabled", True) is not False


def can_enable(info: Dict[str, object]) -> bool:
    """Installed, known to the shell, off, and not broken."""
    if not info:
        return False
    return info.get("state") not in (STATE_ACTIVE, STATE_ERROR, STATE_OUT_OF_DATE, STATE_DOWNLOADING,
                                     STATE_UNINSTALLED)


def should_enable(info: Dict[str, object], marker_exists: bool, tray_start: bool) -> bool:
    """Switch it on automatically only from a `--tray` start and only once: after that the user's choice stands."""
    return tray_start and not marker_exists and can_enable(info)


def marker_path(config_home: Optional[str] = None) -> Path:
    base = config_home or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "wheelguard" / MARKER


def write_marker(path: Optional[Path] = None):
    p = path or marker_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    except OSError:
        pass


class ExtensionWatcher(QObject):
    """Tracks whether the extension is active; `changed(bool)` fires on every transition."""

    changed = Signal(bool)
    infoReceived = Signal("QVariantMap")        # first (and every) answer from the shell

    def __init__(self, bus: Optional[QDBusConnection] = None, parent=None):
        super().__init__(parent)
        self._bus = bus or QDBusConnection.sessionBus()
        self.active = False
        self.answered = False       # the shell has replied (or the reply failed) at least once
        self.info: Dict[str, object] = {}
        self._bus.connect(SHELL, SHELL_PATH, SHELL, "ExtensionStateChanged", self,
                          "1_stateSignal(QString,QVariantMap)")
        self.query()

    @Slot(str, "QVariantMap")
    def _stateSignal(self, uuid, info):
        if uuid == UUID:
            self._set(dict(info))

    def _set(self, info: Dict[str, object]):
        self.info = info
        self.answered = True
        active = is_active(info)
        flipped = active != self.active
        self.active = active
        if flipped:
            self.changed.emit(active)
        self.infoReceived.emit(info)

    def query(self):
        msg = QDBusMessage.createMethodCall(SHELL, SHELL_PATH, SHELL, "GetExtensionInfo")
        msg.setArguments([UUID])
        self._bus.callWithCallback(msg, self, "1_infoReply(QVariantMap)", "1_infoError(QDBusError,QDBusMessage)", 2000)

    @Slot("QVariantMap")
    def _infoReply(self, info):
        self._set(dict(info))

    @Slot(QDBusError, QDBusMessage)
    def _infoError(self, _err, _msg):
        self._set({})           # no shell, or it doesn't know the extension

    def enable(self):
        msg = QDBusMessage.createMethodCall(SHELL, SHELL_PATH, SHELL, "EnableExtension")
        msg.setArguments([UUID])
        self._bus.asyncCall(msg, 2000)
