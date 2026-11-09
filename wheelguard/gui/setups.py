"""Saved mouse setups: named copies of a mouse's own settings ("Gaming", "Desktop"), kept per model in
~/.config/wheelguard/setups.json. No Qt.

    {"version": 1, "setups": {"<model key>": [{"name": "Gaming", "values": {"dpi_x0": 800, ...}}, ...]}}

Only that model's own setting keys are stored, with plain int/bool values. The file is validated when it
is read (anything odd is dropped, never trusted), and written atomically. Applying a setup is not done
here: the MouseController sends it through MouseSession.apply (backup first, then verify).
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

FILE = "setups.json"
VERSION = 1
NAME_MAX = 30
MAX_PER_MODEL = 12
VALUE_LIMIT = 1_000_000


class Setup(NamedTuple):
    name: str
    values: Dict[str, object]


def default_path(config_home: Path) -> Path:
    return Path(config_home) / "wheelguard" / FILE


def clean_name(name) -> str:
    """The name as stored: surrounding space and control characters removed, one space between words."""
    if not isinstance(name, str):
        return ""
    return " ".join("".join(c for c in name if c.isprintable()).split())[:NAME_MAX].strip()


def clean_values(values, allowed) -> Dict[str, object]:
    """Only known keys with plain int/bool values in a sane range."""
    out: Dict[str, object] = {}
    if not isinstance(values, dict):
        return out
    for key in allowed:
        v = values.get(key)
        if isinstance(v, bool) or (isinstance(v, int) and -VALUE_LIMIT <= v <= VALUE_LIMIT):
            out[key] = v
    return out


def parse(data, allowed_by_model: Dict[str, Tuple[str, ...]]) -> Dict[str, List[Setup]]:
    """The validated setups of a decoded file. Unknown models are ignored."""
    out: Dict[str, List[Setup]] = {}
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("setups"), dict):
        return out
    for model, items in data["setups"].items():
        allowed = allowed_by_model.get(model)
        if allowed is None or not isinstance(items, list):
            continue
        seen = set()
        good: List[Setup] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = clean_name(item.get("name"))
            values = clean_values(item.get("values"), allowed)
            if not name or name.casefold() in seen or not values:
                continue
            seen.add(name.casefold())
            good.append(Setup(name, values))
            if len(good) >= MAX_PER_MODEL:
                break
        if good:
            out[model] = good
    return out


class SetupStore:
    def __init__(self, path: Path, allowed_by_model: Dict[str, Tuple[str, ...]]):
        self.path = Path(path)
        self._allowed = allowed_by_model

    def _read(self):
        """(raw "setups" dict of the file, whether the file is unusable). A missing file is fine, not corrupt."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, False
        except (OSError, ValueError):
            return {}, True
        if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("setups"), dict):
            return {}, True
        return data["setups"], False

    def _load(self) -> Dict[str, List[Setup]]:
        raw, _ = self._read()
        return parse({"version": VERSION, "setups": raw}, self._allowed)

    def _write(self, all_setups: Dict[str, List[Setup]]) -> Optional[str]:
        raw, corrupt = self._read()
        # models this version doesn't know (a newer Wheelguard's) are kept exactly as they were
        setups_doc = {m: items for m, items in raw.items() if m not in self._allowed}
        setups_doc.update({m: [{"name": s.name, "values": s.values} for s in items]
                           for m, items in all_setups.items() if items})
        doc = {"version": VERSION, "setups": setups_doc}
        if corrupt:                         # keep it for inspection instead of silently replacing it
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
            except OSError:
                pass
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".setups-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(doc, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as e:
            return f"Couldn't save the setup ({e.strerror or e})."
        return None

    def list(self, model: str) -> List[Setup]:
        return self._load().get(model, [])

    def get(self, model: str, name: str) -> Optional[Setup]:
        key = clean_name(name).casefold()
        return next((s for s in self.list(model) if s.name.casefold() == key), None)

    def save(self, model: str, name: str, values: Dict[str, object]) -> str:
        """Add the setup, or replace the one with the same name. Returns "" or what went wrong."""
        allowed = self._allowed.get(model)
        if allowed is None:
            return "This mouse can't save setups."
        name = clean_name(name)
        if not name:
            return "Give the setup a name."
        vals = clean_values(values, allowed)
        if not vals:
            return "There are no settings to save yet."
        everything = self._load()
        items = everything.get(model, [])
        at = next((i for i, s in enumerate(items) if s.name.casefold() == name.casefold()), None)
        if at is None:
            if len(items) >= MAX_PER_MODEL:
                return f"You can keep up to {MAX_PER_MODEL} setups. Delete one first."
            items = items + [Setup(name, vals)]
        else:                       # same name: replace it, keeping its place in the list
            items = items[:at] + [Setup(name, vals)] + items[at + 1:]
        everything[model] = items
        return self._write(everything) or ""

    def delete(self, model: str, name: str) -> str:
        everything = self._load()
        key = clean_name(name).casefold()
        items = [s for s in everything.get(model, []) if s.name.casefold() != key]
        if len(items) == len(everything.get(model, [])):
            return ""
        everything[model] = items
        return self._write(everything) or ""
