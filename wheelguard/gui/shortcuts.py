"""GNOME custom keyboard shortcuts for Wheelguard's two quick commands.

    Super+Alt+C   toggle Click-Lock        (wheelguard-settings --toggle-clicklock)
    Super+Alt+D   next DPI stage           (wheelguard-settings --next-dpi-stage)

They are added as ordinary GNOME custom keybindings (org.gnome.settings-daemon.plugins.media-keys), so the
user can change or remove them in GNOME Settings > Keyboard > Custom Shortcuts. Nothing is written until
the user presses the button, the user's existing custom shortcuts are never changed, and our two entries
are only ever appended to the list.
"""
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Sequence

from PySide6.QtCore import Property, QObject, Signal, Slot

from . import gsettings

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
LIST_KEY = "custom-keybindings"
ITEM_SCHEMA = MEDIA_KEYS + ".custom-keybinding"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

Runner = Callable[[Sequence[str]], Optional[str]]       # gsettings arguments -> stdout, or None on failure


class Shortcut(NamedTuple):
    slug: str                   # last part of the keybinding path: ours never collide with "customN"
    name: str
    flag: str                   # command-line flag of wheelguard-settings
    keys: Sequence[str]         # suggestion first, then fall-backs if another custom shortcut has it
    label: str                  # the suggested key as people read it

    @property
    def path(self) -> str:
        return f"{BASE}{self.slug}/"


SHORTCUTS = (
    Shortcut("wheelguard-clicklock", "Wheelguard: toggle Click-Lock", "--toggle-clicklock",
             ("<Super><Alt>c", "<Super><Alt>k"), "Super+Alt+C"),
    Shortcut("wheelguard-next-dpi", "Wheelguard: next DPI stage", "--next-dpi-stage",
             ("<Super><Alt>d", "<Super><Alt>i"), "Super+Alt+D"),
)


def parse_str_list(text: Optional[str]) -> List[str]:
    """`gsettings get` output for an array of strings: "['a', 'b']", "[]" or "@as []"."""
    if not text:
        return []
    return [re.sub(r"\\(.)", r"\1", m) for m in re.findall(r"'((?:[^'\\]|\\.)*)'", text)]


def format_str_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(gsettings.format_gvariant(i) for i in items) + "]"


def key_label(binding: str) -> str:
    """"<Super><Alt>c" -> "Super+Alt+C"."""
    parts = re.findall(r"<([^>]+)>", binding)
    rest = re.sub(r"<[^>]+>", "", binding)
    return "+".join(parts + ([rest.upper() if len(rest) == 1 else rest] if rest else []))


def command_for(flag: str, which=shutil.which) -> str:
    """The command GNOME runs: the installed launcher when it is on PATH, else this checkout's script."""
    exe = which("wheelguard-settings")
    if exe is None:
        script = Path(__file__).resolve().parents[2] / "wheelguard-settings"
        exe = f"{sys.executable} {shlex.quote(str(script))}"
    return f"{exe} {flag}"


class Result(NamedTuple):
    ok: bool
    added: List[str]            # labels of the shortcuts created now
    existing: List[str]         # labels of ours that were already there (left untouched)
    error: str = ""


class Shortcuts:
    """The gsettings work, with the runner injected so the tests need no GNOME."""

    def __init__(self, run: Runner = gsettings.run, command: Callable[[str], str] = command_for):
        self._run, self._command = run, command

    def read_list(self) -> Optional[List[str]]:
        out = self._run(["get", MEDIA_KEYS, LIST_KEY])
        return None if out is None else parse_str_list(out)

    def _binding_of(self, path: str) -> str:
        out = self._run(["get", f"{ITEM_SCHEMA}:{path}", "binding"])
        return (parse_str_list(out) or [""])[0]

    def installed(self) -> bool:
        paths = self.read_list() or []
        return all(s.path in paths for s in SHORTCUTS)

    def add(self) -> Result:
        paths = self.read_list()
        if paths is None:
            return Result(False, [], [], "GNOME's keyboard settings aren't available here.")
        used = {self._binding_of(p) for p in paths if not p.startswith(BASE + "wheelguard-")}
        used.discard("")
        todo, existing = [], []
        for sc in SHORTCUTS:
            if sc.path in paths:
                existing.append(sc.label)
                continue
            key = next((k for k in sc.keys if k not in used), "")
            used.add(key)
            todo.append((sc, key))
        # Item details first, the list last: GNOME never sees a half-made entry.
        for sc, key in todo:
            schema = f"{ITEM_SCHEMA}:{sc.path}"
            for field, value in (("name", sc.name), ("command", self._command(sc.flag)), ("binding", key)):
                if self._run(["set", schema, field, gsettings.format_gvariant(value)]) is None:
                    return Result(False, [], existing, "Couldn't write the shortcut settings.")
        if todo:
            fresh = self.read_list()                 # read again right before writing the list
            if fresh is None:
                return Result(False, [], existing, "Couldn't read the shortcut list.")
            merged = fresh + [sc.path for sc, _k in todo if sc.path not in fresh]
            if self._run(["set", MEDIA_KEYS, LIST_KEY, format_str_list(merged)]) is None:
                return Result(False, [], existing, "Couldn't save the shortcut list.")
        return Result(True, [(key_label(k) if k else "no key set") for _sc, k in todo], existing)


class ShortcutsBackend(QObject):
    """`shortcuts` in QML: the "Add keyboard shortcuts" button."""

    changed = Signal()

    def __init__(self, engine: Optional[Shortcuts] = None, parent=None):
        super().__init__(parent)
        self._engine = engine or Shortcuts()
        self._available = gsettings.available()
        self._message = ""
        self._error = False
        self._installed = self._available and self._engine.installed()

    @Slot()
    def add(self):
        res = self._engine.add()
        self._error = not res.ok
        if res.ok:
            self._installed = True
            self._message = ("Added. Change the keys any time in GNOME Settings > Keyboard > Custom Shortcuts."
                             if res.added else "Both shortcuts are already there.")
        else:
            self._message = res.error
        self.changed.emit()

    @Slot()
    def refresh(self):
        self._installed = self._available and self._engine.installed()
        self.changed.emit()

    available = Property(bool, lambda s: s._available, constant=True)
    installed = Property(bool, lambda s: s._installed, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    failed = Property(bool, lambda s: s._error, notify=changed)
    labels = Property("QStringList", lambda s: [sc.label for sc in SHORTCUTS], constant=True)


