"""Thin wrapper around the `gsettings` CLI for org.gnome.desktop.peripherals.mouse. No Qt, no GLib."""
import os
import re
import shutil
import subprocess
from typing import Any, Dict, Optional

SCHEMA = "org.gnome.desktop.peripherals.mouse"
INTERFACE_SCHEMA = "org.gnome.desktop.interface"
TIMEOUT_S = 3.0

# key -> (python type, min, max, default)
KEYS = {
    "speed": (float, -1.0, 1.0, 0.0),
    "accel-profile": (str, None, None, "default"),
    "natural-scroll": (bool, None, None, False),
    "left-handed": (bool, None, None, False),
    "middle-click-emulation": (bool, None, None, False),
    "double-click": (int, 100, 1000, 400),
    "drag-threshold": (int, 1, 64, 8),
}
ACCEL_PROFILES = ("default", "flat", "adaptive")

_TYPE_PREFIX = re.compile(
    r"^(?:@\w+\s+)?(?:uint(?:8|16|32|64)|int(?:16|32|64)|byte|double|handle)\s+(.*)$", re.S)
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


def parse_gvariant(text: str) -> Any:
    """Parse the textual GVariant `gsettings` prints for the simple types we use.

    true/false -> bool, 'str' / "str" -> str, 400 / uint32 400 -> int, 0.5 -> float.
    Anything else (arrays, tuples, dictionaries) is returned as the stripped raw string.
    """
    s = text.strip()
    m = _TYPE_PREFIX.match(s)
    if m:
        s = m.group(1).strip()
    if s == "true":
        return True
    if s == "false":
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        body = s[1:-1]
        return re.sub(r"\\(.)", lambda mm: _ESCAPES.get(mm.group(1), mm.group(1)), body)
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def format_gvariant(value: Any) -> str:
    """Inverse of parse_gvariant for what we write."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(round(value, 6))
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def parse_list_recursively(text: str, schema: str = SCHEMA) -> Dict[str, Any]:
    """`gsettings list-recursively SCHEMA` output -> {key: value}."""
    out: Dict[str, Any] = {}
    prefix = schema + " "
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix):]
        key, _, value = rest.partition(" ")
        if key:
            out[key] = parse_gvariant(value)
    return out


def coerce(key: str, value: Any) -> Any:
    """Clamp / convert a value to the type and range of a known key (unknown values -> default)."""
    typ, lo, hi, default = KEYS[key]
    try:
        if typ is bool:
            return bool(value)
        if typ is str:
            v = str(value)
            return v if (key != "accel-profile" or v in ACCEL_PROFILES) else default
        v = typ(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        v = max(typ(lo), min(typ(hi), v))
    return v


def readonly() -> bool:
    return os.environ.get("WHEELGUARD_GSETTINGS_READONLY") == "1"


def available() -> bool:
    return shutil.which("gsettings") is not None


def _run(args, timeout=TIMEOUT_S) -> Optional[str]:
    try:
        r = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def read_all() -> Optional[Dict[str, Any]]:
    """All mouse settings we care about (coerced), or None if gsettings/schema is unavailable."""
    if not available():
        return None
    text = _run(["list-recursively", SCHEMA])
    if text is None:
        return None
    raw = parse_list_recursively(text)
    return {k: coerce(k, raw[k]) if k in raw else KEYS[k][3] for k in KEYS}


def write(key: str, value: Any) -> bool:
    if key not in KEYS:
        raise KeyError(key)
    value = coerce(key, value)
    if readonly():
        print(f"[gsettings readonly] set {SCHEMA} {key} {format_gvariant(value)}", flush=True)
        return True
    return _run(["set", SCHEMA, key, format_gvariant(value)]) is not None


def prefers_dark(text: Optional[str] = None) -> bool:
    """True if org.gnome.desktop.interface color-scheme is prefer-dark. `text` is for tests."""
    if text is None:
        text = _run(["get", INTERFACE_SCHEMA, "color-scheme"]) or ""
    return "prefer-dark" in text
