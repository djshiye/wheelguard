"""Config file: ~/.config/wheelguard/config.json (command-line flags override it).

Internally vendor/product are ints; on the wire and in the file they are hex strings ("3367").
"""
import json
import math
import os
import tempfile
from pathlib import Path

from . import clicklock, core, mice

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wheelguard"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "enabled": True,
    "lock_ms": int(core.DEFAULT_LOCK_MS),
    "pre_ms": int(core.DEFAULT_PRE_MS),
    "hold_ms": int(core.DEFAULT_HOLD_MS),
    "vendor": 0x3367,
    "product": 0x1970,
    "retry_s": 2,
    "clicklock": False,
    "clicklock_ms": clicklock.DEFAULT_HOLD_MS,
    "clicklock_mode": clicklock.REPEAT,
    "clicklock_rate": clicklock.DEFAULT_RATE,
    "clicklock_max_s": clicklock.DEFAULT_MAX_S,
    "clicklock_ignore_drag": True,
    "clicklock_drag_counts": clicklock.DRAG_COUNTS,
    "debounce_ms": 0,
    "mouse": mice.AUTO,
}
MS_KEYS = ("lock_ms", "pre_ms", "hold_ms")
MAX_MS = 2000
MAX_RETRY_S = 3600
CLICKLOCK_MS = (500, 30000)     # how long the button must be held before Click-Lock engages
CLICKLOCK_RATE = (1, 30)        # repeat clicks per second
CLICKLOCK_MAX_S = (10, 3600)    # safety timer: a running lock stops by itself after this long (0 = never)
CLICKLOCK_DRAG_COUNTS = (50, 20000)     # "ignore drags": how far the pointer may wander (the app sets ~0.25 inch for the mouse's DPI)
DEBOUNCE_MS = (10, 150)         # "Double-click fix": ignore a second press this soon after a release (0 = off)
CLICKLOCK_KEYS = ("clicklock", "clicklock_ms", "clicklock_mode", "clicklock_rate", "clicklock_max_s",
                  "clicklock_ignore_drag", "clicklock_drag_counts")
# What a client may change for as long as it stays connected, without touching config.json (the `override`
# command; the app uses it for per-game profiles).
OVERRIDE_KEYS = ("enabled", "pre_ms", "hold_ms", "lock_ms", "clicklock")


def _number(key, v):
    try:
        ok = not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
    except OverflowError:           # an integer too big for a float, e.g. 1 followed by 400 zeros
        ok = False
    if not ok:
        raise ValueError(f"{key} must be a number")
    return int(v) if float(v).is_integer() else float(v)


def _usb_id(key, v):
    if isinstance(v, str):
        try:
            v = int(v, 16)
        except ValueError:
            raise ValueError(f"{key} must be a hex string like \"3367\"") from None
    elif isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{key} must be a hex string like \"3367\"")
    if not 0 <= v <= 0xFFFF:
        raise ValueError(f"{key} out of range")
    return v


def _mouse(v):
    """"auto" or the "vvvv:pppp" hex IDs of the mouse to filter (lower case)."""
    if v == mice.AUTO:
        return v
    ids = mice.parse_choice(v)
    if ids is None:
        raise ValueError('mouse must be "auto" or the hex IDs of a mouse like "3367:1970"')
    return mice.format_choice(*ids)


def normalize(partial, base=None):
    """Merge `partial` over `base` (default: DEFAULTS), validate, return a new full config.

    Raises ValueError with a human-readable message.
    """
    if not isinstance(partial, dict):
        raise ValueError("config must be a JSON object")
    cfg = dict(DEFAULTS if base is None else base)
    for k, v in partial.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown key {k!r}")
        if k in ("enabled", "clicklock", "clicklock_ignore_drag"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
            cfg[k] = v
        elif k == "clicklock_mode":
            if v not in clicklock.MODES:
                raise ValueError("clicklock_mode must be " + " or ".join(f'"{m}"' for m in clicklock.MODES))
            cfg[k] = v
        elif k == "mouse":
            cfg[k] = _mouse(v)
        elif k in ("vendor", "product"):
            cfg[k] = _usb_id(k, v)
        else:
            cfg[k] = _number(k, v)
    for k in MS_KEYS:
        if cfg[k] > MAX_MS:
            raise ValueError(f"{k} must be <= {MAX_MS}")
    if not 0 < cfg["hold_ms"] < cfg["lock_ms"]:
        raise ValueError("need 0 < hold_ms < lock_ms")
    if not 0 <= cfg["pre_ms"] <= cfg["lock_ms"]:
        raise ValueError("need 0 <= pre_ms <= lock_ms")
    lo, hi = CLICKLOCK_MS
    if not lo <= cfg["clicklock_ms"] <= hi:
        raise ValueError(f"clicklock_ms must be between {lo} and {hi}")
    lo, hi = CLICKLOCK_RATE
    if not lo <= cfg["clicklock_rate"] <= hi:
        raise ValueError(f"clicklock_rate must be between {lo} and {hi}")
    lo, hi = CLICKLOCK_MAX_S
    if cfg["clicklock_max_s"] != 0 and not lo <= cfg["clicklock_max_s"] <= hi:
        raise ValueError(f"clicklock_max_s must be 0 (never) or between {lo} and {hi}")
    lo, hi = CLICKLOCK_DRAG_COUNTS
    if not lo <= cfg["clicklock_drag_counts"] <= hi:
        raise ValueError(f"clicklock_drag_counts must be between {lo} and {hi}")
    lo, hi = DEBOUNCE_MS
    if cfg["debounce_ms"] != 0 and not lo <= cfg["debounce_ms"] <= hi:
        raise ValueError(f"debounce_ms must be 0 (off) or between {lo} and {hi}")
    if not 0 < cfg["retry_s"] <= MAX_RETRY_S:
        raise ValueError(f"retry_s must be > 0 and <= {MAX_RETRY_S}")
    return cfg


def load(path=None):
    """The effective config from the file (defaults for missing keys). ValueError/OSError on trouble."""
    path = Path(path or CONFIG_PATH)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return normalize({})
    try:
        return normalize(json.loads(text))
    except (ValueError, RecursionError) as ex:   # RecursionError: absurdly deep nesting
        raise ValueError(f"{path}: {ex}") from None


def to_wire(cfg):
    out = dict(cfg)
    out["vendor"] = f"{cfg['vendor']:04x}"
    out["product"] = f"{cfg['product']:04x}"
    return out


def save(cfg, path=None, fsync=True):
    """Write the config atomically (temp file in the same directory, then rename).

    `fsync=False` skips the wait for the disk. The daemon saves that way, because it saves on the thread
    that also moves the mouse; the rename still means a reader sees the old file or the new one."""
    path = Path(path or CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(to_wire(cfg), f, indent=2)
            f.write("\n")
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
