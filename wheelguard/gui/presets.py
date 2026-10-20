"""Protection-strength presets: plain data and the preset <-> (pre, hold, lock) mapping. No Qt."""
from typing import List, NamedTuple, Optional

CUSTOM = "Custom"


class Preset(NamedTuple):
    name: str
    pre_ms: int
    hold_ms: int
    lock_ms: int
    summary: str      # one-line plain-English trade-off


PRESETS: List[Preset] = [
    Preset("Light", 40, 15, 250,
           "Only the fastest bounces are caught. A deliberate reverse scroll lands ~15 ms later."),
    Preset("Balanced", 60, 25, 300,
           "Catches nearly every stray. A deliberate reverse scroll lands ~25 ms later."),
    Preset("Strong", 90, 35, 400,
           "Also catches slow, stubborn strays. A deliberate reverse scroll lands ~35 ms later."),
    Preset("Maximum", 130, 50, 500,
           "Most aggressive. A deliberate reverse scroll lands ~50 ms later and may feel sluggish."),
]

DEFAULT_PRESET = 1  # Balanced

CUSTOM_SUMMARY = ("Hand-tuned values. Open Advanced to adjust, or pick a preset to go back "
                  "to a tested combination.")

# Limits mirrored from PROTOCOL.md validation (the daemon is the authority).
MAX_MS = 2000
LIMITS = {
    "pre_ms": (0, 300),
    "hold_ms": (5, 150),
    "lock_ms": (100, 1000),
}


def preset_index(pre_ms, hold_ms, lock_ms) -> int:
    """Index of the preset exactly matching these values, or -1 for custom."""
    try:
        key = (round(float(pre_ms)), round(float(hold_ms)), round(float(lock_ms)))
    except (TypeError, ValueError):
        return -1
    for i, p in enumerate(PRESETS):
        if key == (p.pre_ms, p.hold_ms, p.lock_ms):
            return i
    return -1


def preset_name(pre_ms, hold_ms, lock_ms) -> str:
    i = preset_index(pre_ms, hold_ms, lock_ms)
    return PRESETS[i].name if i >= 0 else CUSTOM


def preset_summary(pre_ms, hold_ms, lock_ms) -> str:
    i = preset_index(pre_ms, hold_ms, lock_ms)
    return PRESETS[i].summary if i >= 0 else CUSTOM_SUMMARY


def preset_config(index: int) -> dict:
    p = PRESETS[index]
    return {"pre_ms": p.pre_ms, "hold_ms": p.hold_ms, "lock_ms": p.lock_ms}


def normalize(cfg: dict, changed: Optional[str] = None) -> dict:
    """Make a (pre, hold, lock) triple satisfy the daemon's rules: 0 < hold < lock, 0 <= pre <= lock.

    `changed` is the key the user just edited; the *other* value gives way so the edited slider
    does not fight the user's hand.
    """
    out = dict(cfg)
    pre, hold, lock = out["pre_ms"], out["hold_ms"], out["lock_ms"]
    if hold >= lock:
        if changed == "lock_ms":
            hold = max(1, lock - 5)
        else:
            lock = min(MAX_MS, hold + 5)
    if pre > lock:
        if changed == "lock_ms":
            pre = lock
        else:
            lock = pre
    out.update(pre_ms=int(pre), hold_ms=int(hold), lock_ms=int(lock))
    return out
