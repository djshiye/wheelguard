"""Per-game profiles: while a chosen app has the focus, Wheelguard switches to the profile's settings and
back to the user's own settings when focus leaves. No Qt.

    ~/.config/wheelguard/profiles.json
    {"version": 1, "profiles": [{"app": "steam_app_123.desktop", "protection": "Strong",
                                 "clicklock": "off", "stage": 1}, ...]}

`protection` is a preset name or "keep", `clicklock` is "on", "off" or "keep", `stage` is a 0-based DPI stage
or null for "keep". Only app ids are stored (never window titles). The file is validated when it is read
(anything odd is dropped, never trusted), holds at most MAX_PROFILES, and is written atomically.

ProfileEngine is the decision logic. It owns no I/O: the controller tells it what the live settings are and
what the focused app is, and it answers with the settings to apply. Settings are keyed "protection" (a
(pre, hold, lock) tuple), "clicklock" (bool) and "stage" (int).
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import presets

FILE = "profiles.json"
JOURNAL_FILE = "profile-active.json"
VERSION = 1
MAX_PROFILES = 30
MAX_APP_ID = 200
MAX_STAGE = 3                   # DPI stages are 0 to 3 (the mice have at most four)
KEEP = "keep"
PROTECTIONS = tuple(p.name for p in presets.PRESETS)
SETTLE_S = 3.0                  # how long a value we sent may take to show up in the live settings
SETTLE_STAGE_S = 10.0           # a DPI switch goes through the mouse and is slower
# Steam shortcuts have spaces in their file names ("Portal 2.desktop"); no leading or trailing space
APP_ID_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9._+ -]*[A-Za-z0-9._+-])?")


class Profile(NamedTuple):
    app_id: str
    protection: Optional[str] = None        # preset name, None = keep
    clicklock: Optional[bool] = None        # None = keep
    stage: Optional[int] = None             # None = keep


def clean_app_id(value) -> str:
    """The id if it looks like a desktop-file id, else "". Used for the file and for D-Bus input."""
    if not isinstance(value, str) or not 0 < len(value) <= MAX_APP_ID or not APP_ID_RE.fullmatch(value):
        return ""
    return value


def is_empty(p: Profile) -> bool:
    return p.protection is None and p.clicklock is None and p.stage is None


def _protection(v) -> Optional[str]:
    if isinstance(v, str):
        for name in PROTECTIONS:
            if v.casefold() == name.casefold():
                return name
    return None


def _clicklock(v) -> Optional[bool]:
    return {"on": True, "off": False}.get(v) if isinstance(v, str) else None


def _stage(v) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_STAGE else None


def make_profile(app_id, protection=None, clicklock=None, stage=None) -> Optional[Profile]:
    """A validated Profile (UI words accepted: "keep", preset names, "on"/"off"), or None when invalid or empty."""
    app = clean_app_id(app_id)
    if not app:
        return None
    p = Profile(app, _protection(protection), clicklock if isinstance(clicklock, bool) else _clicklock(clicklock),
                _stage(stage))
    return None if is_empty(p) else p


def parse(data) -> List[Profile]:
    """The validated profiles of a decoded file."""
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("profiles"), list):
        return []
    out: List[Profile] = []
    seen = set()
    for item in data["profiles"]:
        if not isinstance(item, dict):
            continue
        p = make_profile(item.get("app"), item.get("protection"), item.get("clicklock"), item.get("stage"))
        if p is None or p.app_id in seen:
            continue
        seen.add(p.app_id)
        out.append(p)
        if len(out) >= MAX_PROFILES:
            break
    return out


def to_json(p: Profile) -> Dict[str, object]:
    return {"app": p.app_id,
            "protection": p.protection or KEEP,
            "clicklock": KEEP if p.clicklock is None else ("on" if p.clicklock else "off"),
            "stage": p.stage}


def summary(p: Profile) -> str:
    """"Strong protection · Click-Lock on · DPI stage 2" (only what the profile changes)."""
    parts = []
    if p.protection:
        parts.append(f"{p.protection} protection")
    if p.clicklock is not None:
        parts.append("Click-Lock " + ("on" if p.clicklock else "off"))
    if p.stage is not None:
        parts.append(f"DPI stage {p.stage + 1}")
    return " · ".join(parts)


def engine_values(p: Profile) -> Dict[str, object]:
    """The settings a profile sets, in the engine's form."""
    out: Dict[str, object] = {}
    if p.protection:
        i = PROTECTIONS.index(p.protection)
        pr = presets.PRESETS[i]
        out["protection"] = (pr.pre_ms, pr.hold_ms, pr.lock_ms)
    if p.clicklock is not None:
        out["clicklock"] = p.clicklock
    if p.stage is not None:
        out["stage"] = p.stage
    return out


def atomic_write(path: Path, doc, prefix: str) -> Optional[str]:
    """Write `doc` as JSON next to `path`, then rename. Returns "" or what went wrong."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        return str(e.strerror or e)
    return ""


class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> List[Profile]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return parse(data)

    def _write(self, items: List[Profile]) -> str:
        err = atomic_write(self.path, {"version": VERSION, "profiles": [to_json(p) for p in items]}, ".profiles-")
        return f"Couldn't save the profile ({err})." if err else ""

    def save(self, profile: Profile) -> str:
        """Add the profile, or replace the one for the same app. Returns "" or what went wrong."""
        if profile.app_id != clean_app_id(profile.app_id) or is_empty(profile):
            return "Choose at least one change for this app."
        items = self.load()
        at = next((i for i, p in enumerate(items) if p.app_id == profile.app_id), None)
        if at is None:
            if len(items) >= MAX_PROFILES:
                return f"You can keep up to {MAX_PROFILES} profiles. Delete one first."
            items.append(profile)
        else:
            items[at] = profile
        return self._write(items)

    def delete(self, app_id: str) -> str:
        items = self.load()
        kept = [p for p in items if p.app_id != app_id]
        return "" if len(kept) == len(items) else self._write(kept)


# ---- crash recovery: what to hand back if the app dies while a profile is engaged
def journal_to_json(restore: Dict[str, object]) -> Dict[str, object]:
    return {"version": VERSION, "restore": {k: list(v) if isinstance(v, tuple) else v for k, v in restore.items()}}


def journal_parse(data) -> Dict[str, object]:
    """The validated settings to hand back, in the engine's form ({} when the file says nothing useful)."""
    out: Dict[str, object] = {}
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("restore"), dict):
        return out
    r = data["restore"]
    pr = r.get("protection")
    if isinstance(pr, list) and len(pr) == 3 and all(isinstance(x, int) and not isinstance(x, bool) for x in pr):
        t = tuple(pr)
        limits = (presets.LIMITS["pre_ms"], presets.LIMITS["hold_ms"], presets.LIMITS["lock_ms"])
        if all(lo <= v <= hi for v, (lo, hi) in zip(t, limits, strict=True)):
            out["protection"] = t
    if isinstance(r.get("clicklock"), bool):
        out["clicklock"] = r["clicklock"]
    if _stage(r.get("stage")) is not None:
        out["stage"] = r["stage"]
    return out


def journal_load(path: Path) -> Dict[str, object]:
    try:
        return journal_parse(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def journal_save(path: Path, restore: Dict[str, object]) -> bool:
    if not restore:
        return journal_clear(path)
    return not atomic_write(Path(path), journal_to_json(restore), ".active-")


def journal_clear(path: Path) -> bool:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        return False
    return True


class ProfileEngine:
    """Decides what to apply when the focused app changes, and remembers how to get back.

    The user's own values ("base") are captured when a profile engages and followed while no profile is
    active. A value we send is "pending" until it shows up in the live settings, so a half-applied state is
    never mistaken for the user's choice. If a live value differs from what the profile set (and isn't
    pending), the user changed it by hand: that key is left alone for the rest of the session and is not
    restored on leave; the user's value becomes the new base.
    """

    def __init__(self, profiles: Optional[Dict[str, Dict[str, object]]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._profiles: Dict[str, Dict[str, object]] = dict(profiles or {})
        self.active: Optional[str] = None
        self._base: Dict[str, object] = {}
        self._applied: Dict[str, object] = {}
        self._overridden: set = set()
        self._pending: Dict[str, Tuple[object, float]] = {}
        self._retry: Dict[str, object] = {}
        self._seed: Dict[str, object] = {}      # values a crashed run still owes the user (see journal_load)

    # ---- inputs
    def set_profiles(self, profiles: Dict[str, Dict[str, object]]):
        self._profiles = dict(profiles)

    def _eff(self, current: Dict[str, object]) -> Dict[str, object]:
        """The live settings, with values still on their way counted as already there."""
        now = self._clock()
        eff = {k: v for k, v in current.items() if v is not None}
        for k, (v, deadline) in self._pending.items():
            if deadline > now:
                eff[k] = v
        if self.active is None:
            eff.update(self._seed)
        return eff

    def _mark(self, plan: Dict[str, object]):
        now = self._clock()
        for k, v in plan.items():
            self._pending[k] = (v, now + (SETTLE_STAGE_S if k == "stage" else SETTLE_S))

    def focus(self, app_id: str, current: Dict[str, object]) -> Dict[str, object]:
        """The focused app changed: the settings to apply now. Focus on the active profile's app is a no-op."""
        target = self._profiles.get(app_id) if app_id else None
        if target is None:
            return self._leave(current)
        if app_id == self.active:
            return {}
        return self._engage(app_id, target, current)

    def refresh(self, current: Dict[str, object]) -> Dict[str, object]:
        """The profile for the active app was edited, deleted or switched off: re-evaluate."""
        app = self.active
        if app is None:
            return {}
        if app not in self._profiles:
            return self._leave(current)
        return self._engage(app, self._profiles[app], current)

    def release(self, current: Dict[str, object]) -> Dict[str, object]:
        """Hand everything back now (profiles switched off, the app quitting)."""
        return self._leave(current)

    def _engage(self, app_id, target, current) -> Dict[str, object]:
        eff = self._eff(current)
        plan: Dict[str, object] = {}
        if self.active is None:
            self._base = dict(eff)                  # the user's normal settings, as they are right now
            self._base.update(self._retry)          # ... plus values still owed back, which are the user's
            self._retry = {k: v for k, v in self._retry.items() if k not in target}
        else:
            self._retry = {}
            for k in self._overridden:              # what the user set by hand is their normal now
                if k in eff:
                    self._base[k] = eff[k]
            for k in self._applied:                 # settings the new profile doesn't touch go back
                if k not in target and k not in self._overridden and k in self._base and eff.get(k) != self._base[k]:
                    plan[k] = self._base[k]
        self._overridden = set()
        self._applied = {}
        for k, v in target.items():
            if k not in self._base and k in eff:
                self._base[k] = eff[k]
            self._applied[k] = v
            if eff.get(k) != v:
                plan[k] = v
        self.active = app_id
        self._mark(plan)
        return plan

    def _leave(self, current) -> Dict[str, object]:
        if self.active is None:
            return {}
        eff = self._eff(current)
        plan = {k: self._base[k] for k in self._applied
                if k not in self._overridden and k in self._base and eff.get(k) != self._base[k]}
        self.active = None
        self._retry = {k: v for k, v in self._retry.items() if k not in self._applied}   # base values still owed
        self._applied, self._overridden = {}, set()
        self._mark(plan)
        return plan

    def observe(self, current: Dict[str, object]):
        """The live settings changed (or were polled): follow the user, spot hand changes."""
        now = self._clock()
        for k, (v, deadline) in list(self._pending.items()):
            cur = current.get(k)
            if (cur is not None and cur == v) or now >= deadline:
                del self._pending[k]
        for k, v in list(self._seed.items()):
            if current.get(k) == v:
                del self._seed[k]
        if self.active is None:
            for k, v in current.items():
                if v is not None and k not in self._pending and k not in self._seed and k not in self._retry:
                    self._base[k] = v
            return
        for k, want in self._applied.items():
            if k in self._overridden or k in self._pending or k in self._retry:
                continue
            cur = current.get(k)
            if cur is not None and cur != want:
                self._overridden.add(k)
                self._base[k] = cur
        for k, v in current.items():
            if v is not None and k not in self._applied and k not in self._seed:
                self._base[k] = v

    def seed(self, values: Dict[str, object]):
        """Settings a previous run left changed (it died with a profile engaged): they are the user's normal."""
        self._seed = dict(values)

    def seed_plan(self, current: Dict[str, object]) -> Dict[str, object]:
        """The seeded settings that can be handed back now."""
        plan = {k: v for k, v in self._seed.items()
                if current.get(k) is not None and current[k] != v and k not in self._pending
                and k not in self._applied}
        self._mark(plan)
        return plan

    def skipped(self, keys):
        """These settings couldn't be applied right now (the mouse is busy): try again when it can."""
        for k in keys:
            entry = self._pending.pop(k, None)
            if entry is not None:
                self._retry[k] = entry[0]

    def has_retry(self) -> bool:
        return bool(self._retry)

    def retry_plan(self, current: Dict[str, object]) -> Dict[str, object]:
        """Settings that were skipped and whose live value can be read again."""
        plan: Dict[str, object] = {}
        for k, v in list(self._retry.items()):
            cur = current.get(k)
            if cur is None:
                continue
            del self._retry[k]
            if self.active is not None and k in self._applied and k not in self._base:
                self._base[k] = cur
            if cur != v:
                plan[k] = v
        self._mark(plan)
        return plan

    def restore_values(self) -> Dict[str, object]:
        """What to hand back if the app dies now (written to the journal while a profile is engaged)."""
        out = dict(self._seed)
        out.update({k: v for k, v in self._retry.items() if k not in self._applied})   # base values still owed
        if self.active is not None:
            out.update({k: self._base[k] for k in self._applied if k not in self._overridden and k in self._base})
        return out
