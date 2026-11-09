"""The installed desktop apps a profile can be made for: id (the desktop file's name), display name, icon.
No Qt. Uses Gio.AppInfo when PyGObject is there, else reads the applications/*.desktop files by hand."""
import configparser
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import profiles

MAX_APPS = 1500
MAX_FILE = 64 * 1024
STEAM_RUN_RE = re.compile(r"steam://rungameid/(\d{1,12})\b")


def steam_app_id(exec_line: str) -> str:
    """"steam_app_<n>" for a Steam game shortcut (Exec has steam://rungameid/<n>): the WM class its window has."""
    m = STEAM_RUN_RE.search(exec_line or "")
    return f"steam_app_{int(m.group(1))}" if m else ""


def data_dirs(env: Optional[Dict[str, str]] = None) -> List[Path]:
    env = os.environ if env is None else env
    home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    system = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [Path(home)] + [Path(p) for p in system.split(":") if p]
    return [d / "applications" for d in dirs]


def parse_desktop(path: Path, app_id: str) -> Optional[Dict[str, str]]:
    """{"id", "name", "icon"} for a launchable application, else None (hidden, not an app, unreadable)."""
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        cp.optionxform = str                    # keys are case-sensitive
        cp.read_string(path.read_text(encoding="utf-8", errors="replace"))
        entry = cp["Desktop Entry"]
    except (OSError, configparser.Error, KeyError):
        return None
    if entry.get("Type", "Application") != "Application":
        return None
    if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
        return None
    name = " ".join(entry.get("Name", "").split())[:80]
    app_id = steam_app_id(entry.get("Exec", "")) or app_id
    if not name or not profiles.clean_app_id(app_id):
        return None
    return {"id": app_id, "name": name, "icon": entry.get("Icon", "").strip()[:200]}


def scan(dirs: Iterable[Path]) -> List[Dict[str, str]]:
    """Every shown app under the given applications folders (first folder wins for a repeated id)."""
    seen: Dict[str, Dict[str, str]] = {}
    for base in dirs:
        try:
            files = sorted(base.rglob("*.desktop"))
        except OSError:
            continue
        for f in files:
            if len(seen) >= MAX_APPS:
                break
            app_id = "-".join(f.relative_to(base).parts)      # subfolders become "-" in the id
            if app_id in seen:
                continue
            info = parse_desktop(f, app_id)
            if info is not None and info["id"] not in seen:
                seen[info["id"]] = info
    return sorted(seen.values(), key=lambda a: (a["name"].casefold(), a["id"]))


def _gio_apps() -> Optional[List[Dict[str, str]]]:
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return None
    out: Dict[str, Dict[str, str]] = {}
    try:
        for info in Gio.AppInfo.get_all():
            app_id = steam_app_id(info.get_commandline() or "") or info.get_id() or ""
            if not info.should_show() or not profiles.clean_app_id(app_id) or app_id in out:
                continue
            name = " ".join((info.get_display_name() or "").split())[:80]
            icon = info.get_icon()
            icon_name = ""
            if icon is not None:
                names = getattr(icon, "get_names", lambda: [])()
                icon_name = names[0] if names else (icon.to_string() or "")
            if name:
                out[app_id] = {"id": app_id, "name": name, "icon": icon_name[:200]}
            if len(out) >= MAX_APPS:
                break
    except Exception:           # PyGObject misbehaving must not take the page down
        return None
    return sorted(out.values(), key=lambda a: (a["name"].casefold(), a["id"]))


def list_apps() -> List[Dict[str, str]]:
    apps = _gio_apps()
    return apps if apps else scan(data_dirs())


def matches(app: Dict[str, str], query: str) -> bool:
    q = " ".join(query.casefold().split())
    return not q or q in app["name"].casefold() or q in app["id"].casefold()


def display_name(app_id: str, apps: Iterable[Dict[str, str]]) -> str:
    """The app's name, or a tidied id when it isn't installed (any more)."""
    for a in apps:
        if a["id"] == app_id:
            return a["name"]
    return app_id[:-len(".desktop")] if app_id.endswith(".desktop") else app_id
