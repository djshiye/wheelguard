"""Config file: ~/.config/wheelguard/config.json (command-line flags override it).

Internally vendor/product are ints; on the wire and in the file they are hex strings ("3367").
"""
import json
import math
import os
import tempfile
from pathlib import Path

from . import core

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
}
MS_KEYS = ("lock_ms", "pre_ms", "hold_ms")
MAX_MS = 2000
MAX_RETRY_S = 3600


def _number(key, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
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
        if k == "enabled":
            if not isinstance(v, bool):
                raise ValueError("enabled must be true or false")
            cfg[k] = v
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
    except (ValueError, json.JSONDecodeError) as ex:
        raise ValueError(f"{path}: {ex}") from None


def to_wire(cfg):
    out = dict(cfg)
    out["vendor"] = f"{cfg['vendor']:04x}"
    out["product"] = f"{cfg['product']:04x}"
    return out


def save(cfg, path=None):
    """Write the config atomically (temp file in the same directory, then rename)."""
    path = Path(path or CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(to_wire(cfg), f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
