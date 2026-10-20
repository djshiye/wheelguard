"""'Start in tray at login': the package ships an XDG autostart entry; a user-level copy with Hidden=true
switches it off, removing that copy switches it back on. No Qt."""
import os
from pathlib import Path
from typing import Optional

APP_ID = "io.github.djshiye.Wheelguard"
FILENAME = APP_ID + ".desktop"
HIDDEN_ENTRY = "[Desktop Entry]\nType=Application\nName=Wheelguard\nHidden=true\n"


def user_file(config_home: Optional[Path] = None) -> Path:
    home = config_home or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return Path(home) / "autostart" / FILENAME


def system_file(config_dirs: Optional[str] = None) -> Optional[Path]:
    """The packaged entry, if installed (not when running from a source checkout)."""
    dirs = (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg") if config_dirs is None else config_dirs
    for d in filter(None, dirs.split(":")):
        p = Path(d) / "autostart" / FILENAME
        if p.is_file():
            return p
    return None


def _hidden(path: Path) -> bool:
    try:
        return any(line.strip().lower() == "hidden=true" for line in path.read_text().splitlines())
    except OSError:
        return False


def available(config_dirs: Optional[str] = None) -> bool:
    return system_file(config_dirs) is not None


def is_enabled(config_home: Optional[Path] = None, config_dirs: Optional[str] = None) -> bool:
    if not available(config_dirs):
        return False
    u = user_file(config_home)
    return not (u.is_file() and _hidden(u))


def set_enabled(on: bool, config_home: Optional[Path] = None) -> None:
    u = user_file(config_home)
    if on:
        if u.is_file() and _hidden(u):      # only remove the override this module writes
            u.unlink()
    else:
        u.parent.mkdir(parents=True, exist_ok=True)
        u.write_text(HIDDEN_ENTRY)
