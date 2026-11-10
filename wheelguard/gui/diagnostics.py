"""The "Copy diagnostics" report: plain text a user can paste into a bug report.

It holds Wheelguard's own state and the basics of the system and mouse, and nothing personal: no user
name, home path, host name or serial number. Everything is passed through scrub() as a last step, so even
an error message that happens to carry a path stays clean. The builder itself is pure (no Qt).
"""
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, Slot

# GNOME Shell's ExtensionState values (org.gnome.Shell.Extensions)
EXTENSION_STATES = {1: "active", 2: "installed but switched off", 3: "failed to load", 4: "out of date",
                    5: "downloading", 6: "installed, starting", 99: "not installed"}
MOUSE_STATES = {"idle": "not read yet", "reading": "reading", "ready": "ready", "applying": "saving",
                "asleep": "asleep or not answering", "denied": "no permission to open the mouse",
                "missing": "no supported mouse found", "unsupported": "unsupported mouse", "error": "error"}


def scrub(text: str, home: str = "", user: str = "", host: str = "") -> str:
    """Remove the home folder, user name and host name (and any other /home/<name> or /run/user/<uid> path)."""
    out = text
    if home and home != "/":
        out = out.replace(home, "~")
    out = re.sub(r"/home/[^/\s'\"]+", "~", out)
    out = re.sub(r"/run/user/\d+", "/run/user/<uid>", out)
    for word, label in ((user, "<user>"), (host, "<host>")):
        if word and len(word) >= 3:
            out = re.sub(r"(?<![\w.-])" + re.escape(word) + r"(?![\w.-])", label, out, flags=re.I)
    return out


def os_name(text: Optional[str] = None) -> str:
    """PRETTY_NAME from os-release ("Fedora Linux 44 (Workstation Edition)")."""
    if text is None:
        for path in ("/etc/os-release", "/usr/lib/os-release"):
            try:
                text = Path(path).read_text(encoding="utf-8")
                break
            except OSError:
                continue
    for line in (text or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"\'')
    return "unknown"


def shell_version(output: Optional[str] = None) -> str:
    """"GNOME Shell 50.1" -> "50.1"; "unknown" when there is no GNOME Shell."""
    if output is None:
        exe = shutil.which("gnome-shell")
        if exe is None:
            return "unknown"
        try:
            output = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return "unknown"
    m = re.search(r"(\d+(?:\.\w+)*)", output or "")
    return m.group(1) if m else "unknown"


def extension_text(info: Dict[str, object]) -> str:
    if not info:
        return "not reported by GNOME Shell"
    state = info.get("state")
    text = EXTENSION_STATES.get(state if isinstance(state, int) else -1, f"state {state}")
    if state == 1 and info.get("enabled") is False:
        text = "installed but switched off"
    version = info.get("version-name")
    return f"{text} (version {version})" if isinstance(version, str) and version else text


def build_report(d: Dict[str, object]) -> str:
    """The report text from collected facts (see collect()). Missing facts read "unknown" or are left out."""
    def g(key, default="unknown"):
        v = d.get(key)
        return default if v in (None, "") else str(v)

    on = lambda key: "on" if d.get(key) else "off"      # noqa: E731
    lines = [
        "Wheelguard diagnostics",
        "",
        f"App version: {g('app_version')}",
        f"Service: version {g('service_version', 'not connected')}, {g('service_state')}",
        f"System: {g('os')}" + (f", {g('session')} session" if d.get("session") else ""),
        f"GNOME Shell: {g('shell_version')}",
        f"Panel extension: {g('extension')}",
        "",
    ]
    if d.get("model"):
        lines += [
            f"Mouse: {g('model')}",
            f"USB ID: {g('usb_id')} (vendor:product)",
            f"Connection: {g('connection')}",
            f"Mouse firmware: {g('mouse_fw')}",
            f"Dongle firmware: {g('dongle_fw', 'none or not reported')}",
            f"Battery: {g('battery')}",
        ]
        if d.get("buttons"):
            lines.append(f"Button actions: {g('buttons')}")
    else:
        lines.append("Mouse: none detected")
    lines += [
        f"Mouse page: {g('mouse_state')}",
        f"Last error: {g('last_error', 'none')}",
        "",
        f"Scroll protection: {on('protection')}, preset {g('preset')}",
        f"Click-Lock: {on('clicklock')}",
        f"Low-battery alerts: {on('battery_alerts')}",
        f"DPI popup: {on('dpi_popup')}",
    ]
    return scrub("\n".join(lines) + "\n", d.get("_home", ""), d.get("_user", ""), d.get("_host", ""))


def _identity() -> Dict[str, str]:
    home = str(Path.home())
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USER", "")
    return {"_home": home, "_user": user or os.environ.get("USER", ""), "_host": socket.gethostname()}


def collect(daemon, mouse, ext_info: Dict[str, object], settings, version: str) -> Dict[str, object]:
    """Gather the facts from the running backends (the objects the QML sees)."""
    state = mouse.state
    error = ""
    if mouse.messageKind == "error" and mouse.message:
        error = mouse.message
    elif state not in ("ready", "applying", "idle", "reading") and (mouse.detail or mouse.message):
        error = mouse.detail or mouse.message
    if not error and mouse.problem:
        error = "read-only: " + mouse.problem
    d: Dict[str, object] = {
        "app_version": version,
        "service_version": daemon.version,
        "service_state": daemon.state + (" (protecting)" if daemon.state == "running" and daemon.enabled else ""),
        "os": os_name(),
        "session": {"wayland": "Wayland", "x11": "X11"}.get(os.environ.get("XDG_SESSION_TYPE", ""), ""),
        "shell_version": shell_version(),
        "extension": extension_text(ext_info),
        "model": mouse.modelName,
        "usb_id": mouse.usbIdText,
        "connection": mouse.connection,
        "mouse_fw": mouse.mouseFw,
        "dongle_fw": mouse.dongleFw,
        "battery": "" if mouse.battery < 0 else f"{mouse.battery}%" + (" (charging)" if mouse.charging else ""),
        "mouse_state": MOUSE_STATES.get(state, state),
        "last_error": error,
        "protection": daemon.enabled,
        "preset": daemon.presetName,
        "clicklock": daemon.clickLock,
        "battery_alerts": settings.batteryAlerts,
        "dpi_popup": getattr(settings, "dpiPopup", False),
        "buttons": ("" if "buttons" not in list(getattr(mouse, "features", []) or [])
                    else "available" if getattr(mouse, "buttonsKnown", True) else "not reported by the mouse"),
    }
    d.update(_identity())
    return d


class Diagnostics(QObject):
    """`diagnostics` in QML: builds the report on demand and puts it on the clipboard."""

    def __init__(self, daemon, mouse, ext, settings, parent=None):
        super().__init__(parent)
        self._daemon, self._mouse, self._ext, self._settings = daemon, mouse, ext, settings

    @Slot(result=str)
    def report(self) -> str:
        from .. import __version__
        ext_info = self._ext.info if self._ext is not None else {}
        return build_report(collect(self._daemon, self._mouse, ext_info, self._settings, __version__))

    @Slot(result=bool)
    def copy(self) -> bool:
        from PySide6.QtGui import QGuiApplication
        clip = QGuiApplication.clipboard()
        if clip is None:
            return False
        clip.setText(self.report())
        return True
