"""Low-battery alerts: when to speak up (pure logic) and the desktop notification itself.

Two levels, 20% and 5%. Each fires once per discharge: it re-arms when the mouse is charging, or when the
level climbs back above the threshold plus a few points of hysteresis. Never while charging.
"""
import shutil
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QProcess

from .traymodel import LOW_BATTERY_PERCENT, is_low  # noqa: F401

LEVELS = (LOW_BATTERY_PERCENT, 5)
HYSTERESIS = 5
APP_ID = "io.github.djshiye.Wheelguard"
NOTIFY_NAME = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"


def message(percent: int) -> str:
    if percent <= LEVELS[1]:
        return f"Mouse battery very low: {percent}%. Charge it now."
    return f"Mouse battery low: {percent}%. Charge it soon."


class AlertTracker:
    """Feed it each battery reading; it says whether to notify."""

    def __init__(self):
        self._armed: Dict[int, bool] = {lv: True for lv in LEVELS}

    def update(self, online: bool, percent: int, charging: bool) -> Optional[int]:
        """The level to announce (20 or 5) or None. A reading below both levels announces only the lower."""
        if not online or percent < 0:
            return None
        if charging:
            self._armed = {lv: True for lv in LEVELS}
            return None
        for lv in LEVELS:
            if percent > lv + HYSTERESIS:
                self._armed[lv] = True
        due: List[int] = [lv for lv in LEVELS if percent <= lv and self._armed[lv]]
        if not due:
            return None
        for lv in due:                  # the milder level is spent too
            self._armed[lv] = False
        return min(due)


def notification_args(text: str, summary: str = "Wheelguard") -> list:
    """`gdbus call` arguments for org.freedesktop.Notifications.Notify. gdbus fills in the exact wire types
    from the service's introspection (the replaces-id must be unsigned, which QtDBus cannot send from Python)."""
    return ["call", "--session", "--dest", NOTIFY_NAME, "--object-path", NOTIFY_PATH,
            "--method", NOTIFY_NAME + ".Notify", "Wheelguard", "0", APP_ID, summary, text, "[]",
            "{'desktop-entry': <'" + APP_ID + "'>}", "-1"]


def notify(text: str, summary: str = "Wheelguard") -> bool:
    """Fire and forget a plain-text desktop notification. False when it could not be started."""
    if shutil.which("gdbus") is None:
        return False
    started = QProcess.startDetached("gdbus", notification_args(text, summary))
    return bool(started[0] if isinstance(started, tuple) else started)     # PySide6 returns (ok, pid)


class BatteryAlerts(QObject):
    """Watches the mouse controller and notifies when the settings allow it."""

    def __init__(self, mouse, settings, send=notify, parent=None):
        super().__init__(parent)
        self._mouse, self._settings, self._send = mouse, settings, send
        self._tracker = AlertTracker()
        mouse.infoChanged.connect(self.check)
        mouse.stateChanged.connect(self.check)

    def check(self):
        m = self._mouse
        level = self._tracker.update(m.online, m.battery, m.charging)
        if level is not None and self._settings.batteryAlerts:
            self._send(message(m.battery))
