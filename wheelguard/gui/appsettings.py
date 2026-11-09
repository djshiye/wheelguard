"""Wheelguard's own app settings (not the daemon's config and not GNOME's): a small JSON file in
~/.config/wheelguard. Holds the low-battery alert switch and the first-run welcome flag (not set again for
someone upgrading, who already has a config.json there)."""
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

FILE = "app.json"
DEFAULTS: Dict[str, bool] = {"battery_alerts": True, "welcome_seen": False}


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "wheelguard"


def load(path: Path) -> Dict[str, bool]:
    """The settings from `path`; anything missing, unreadable or of the wrong type falls back to the default."""
    out = dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        for k in DEFAULTS:
            if isinstance(data.get(k), bool):
                out[k] = data[k]
    return out


def save(path: Path, values: Dict[str, bool]) -> bool:
    """Atomic write (temp file in the same folder, then rename). False when the folder isn't writable."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".app-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: values[k] for k in DEFAULTS}, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        return False
    return True


class AppSettings(QObject):
    changed = Signal()

    def __init__(self, path: Optional[Path] = None, parent=None, welcome_enabled: bool = True):
        super().__init__(parent)
        self._path = Path(path) if path else config_dir() / FILE
        self._v = load(self._path)
        if not self._path.exists() and (self._path.parent / "config.json").exists():
            # first run of this file, but the daemon's settings are already here: an upgrade, not a new user
            self._v["welcome_seen"] = True
            save(self._path, self._v)
        self._welcome_enabled = welcome_enabled        # the screenshot harness switches the overlay off

    def _set(self, key: str, value: bool):
        if self._v[key] != value:
            self._v[key] = value
            save(self._path, self._v)
            self.changed.emit()

    @Slot(bool)
    def setBatteryAlerts(self, on: bool):
        self._set("battery_alerts", bool(on))

    @Slot()
    def finishWelcome(self):
        self._set("welcome_seen", True)

    @Slot()
    def resetWelcome(self):
        self._set("welcome_seen", False)

    batteryAlerts = Property(bool, lambda s: s._v["battery_alerts"], notify=changed)
    welcomeSeen = Property(bool, lambda s: s._v["welcome_seen"] or not s._welcome_enabled, notify=changed)
