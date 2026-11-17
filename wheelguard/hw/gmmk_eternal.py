"""Glorious GMMK Eternal 96 (ANSI / ISO): frames for its settings protocol. See docs/gmmk-protocol.md,
"GMMK Eternal".

The Eternal takes 64-byte output reports (report ID 9) on usage page 0xFF60 / usage 0x61, each ending in a
checksum. Settings are written one at a time ("write configuration" with a selector), and keys one at a time.
That usage page is also where QMK boards listen, but the Eternal's stock firmware is not QMK: Wheelguard
only matches it by its own USB IDs. Wheelguard only writes; nothing is read back.

Never built here (and so impossible to send): the factory reset (0x11), user data (0x71), lighting, macros
and anything to do with firmware.
"""
from typing import Dict, Optional, Sequence, Tuple

REPORT_ID = 9
FRAME_LEN = 64
CMD_KEY = 0x03
CMD_CONFIG = 0x04
CMD_PROFILE = 0x10
VAL_POLLING = 0x17
VAL_DEBOUNCE = 0x1E
VAL_STANDBY = 0x23
ALLOWED_CONFIG = frozenset((VAL_POLLING, VAL_DEBOUNCE, VAL_STANDBY))
DELAY = 0.04                    # CORE's gap between writes

COMMAND_COLLECTION = bytes.fromhex("0660ff0961")
# CORE also knows 8000, 4000 and 2000 Hz (codes 4 to 6), but only from a vendor document: left out until a
# board shows it takes them.
POLLING_HZ = (1000, 500, 250, 125)
_POLLING_CODE = {1000: 0, 500: 1, 250: 2, 125: 3}
PROFILES = LAYERS = 3

# Matrix position of each key (CORE's table; ISO's key left of Z is 105 and # is 106).
_ANSI = {
    "Escape": 1, **{f"F{n}": 1 + n for n in range(1, 13)},
    "Backquote": 14, **{f"Digit{d}": 15 + i for i, d in enumerate("1234567890")}, "Minus": 25, "Equal": 26,
    "Backspace": 27, "Tab": 28, **{f"Key{c}": 29 + i for i, c in enumerate("QWERTYUIOP")}, "BracketLeft": 39,
    "BracketRight": 40, "Backslash": 41, "CapsLock": 42, **{f"Key{c}": 43 + i for i, c in enumerate("ASDFGHJKL")},
    "Semicolon": 52, "Quote": 53, "Enter": 54, "ShiftLeft": 55, **{f"Key{c}": 56 + i for i, c in enumerate("ZXCVBNM")},
    "Comma": 63, "Period": 64, "Slash": 65, "ShiftRight": 66, "ControlLeft": 67, "MetaLeft": 68, "AltLeft": 69,
    "Space": 70, "AltRight": 71, "Fn": 72, "ControlRight": 73, "ArrowUp": 74, "ArrowDown": 75, "ArrowLeft": 76,
    "ArrowRight": 77, "NumLock": 78, "NumpadDivide": 79, "NumpadMultiply": 80, "NumpadSubtract": 81,
    "NumpadAdd": 82, "NumpadEnter": 83, "NumpadDecimal": 84, "Numpad0": 85, "Numpad1": 86, "Numpad2": 87,
    "Numpad3": 88, "Numpad4": 89, "Numpad5": 90, "Numpad6": 91, "Numpad7": 92, "Numpad8": 93, "Numpad9": 94,
    "PrintScreen": 95, "ScrollLock": 96, "Pause": 97, "Insert": 98, "Delete": 99, "Home": 100, "End": 101,
    "PageUp": 102, "PageDown": 103, "ContextMenu": 104,
}
KEY_INDEX = {"ansi": _ANSI, "iso": dict(_ANSI, Backslash=105, Hashtag=106)}
# CORE has no code to put the modifiers back once changed, so they keep what the board gives them.
LOCKED = frozenset(("ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight",
                    "MetaLeft"))
MODIFIER_USAGES = frozenset(range(0xE0, 0xE8))
MOD_BITS = {"ctrl": 1, "shift": 2, "alt": 4, "super": 8}
# Wheelguard's media codes (gmmk.MEDIA) to consumer usages; its mouse codes (gmmk.MOUSE) to the board's.
MEDIA = {0: 0x183, 1: 0xCD, 2: 0xE2, 3: 0xE9, 4: 0xEA, 5: 0xB7, 6: 0xB6, 7: 0xB5}
MOUSE = {1: 1, 2: 2, 3: 3, 4: 5, 5: 4, 6: 8, 7: 9}
FUNCTIONS = {2: 1, 1: 2}         # next / previous profile; the board has no layer keys


def checksum(f: bytes) -> int:
    return (0xFF - sum(f[:FRAME_LEN - 1])) & 0xFF


def _frame(cmd: int, val: int, payload: Sequence[int]) -> bytes:
    if len(payload) > 56:
        raise ValueError("payload too long")
    f = bytearray(FRAME_LEN)
    f[0], f[1], f[2], f[3], f[4], f[5], f[6] = REPORT_ID, cmd, val, 0, 1, 0, len(payload)
    f[7:7 + len(payload)] = bytes(payload)
    f[FRAME_LEN - 1] = checksum(f)
    return bytes(f)


def polling_frame(hz: int) -> bytes:
    if hz not in _POLLING_CODE:
        raise ValueError(f"polling rate {hz} Hz isn't supported")
    return _frame(CMD_CONFIG, VAL_POLLING, [_POLLING_CODE[hz]])


def debounce_frame(ms: int) -> bytes:
    """Debounce in microseconds, big-endian (CORE builds this but always sends 5 ms instead)."""
    if not 1 <= ms <= 30:
        raise ValueError(f"debounce {ms} ms isn't supported")
    us = ms * 1000
    return _frame(CMD_CONFIG, VAL_DEBOUNCE, [us >> 8, us & 0xFF])


def standby_frame(minutes: int) -> bytes:
    """Lights-off timer in seconds, big-endian; 255 (never) is 0."""
    seconds = 0 if minutes == 255 else minutes * 60
    if not 0 <= seconds <= 0xFFFF:
        raise ValueError(f"standby {minutes} min isn't supported")
    return _frame(CMD_CONFIG, VAL_STANDBY, [seconds >> 8, seconds & 0xFF])


def profile_frame(profile: int) -> bytes:
    if not 0 <= profile < PROFILES:
        raise ValueError("profile is 0 to 2")
    return _frame(CMD_PROFILE, 0, [profile])


def binding_problem(b: dict) -> str:
    """What the Eternal can't do that the GMMK boards can ("" = fine)."""
    kind = b.get("kind")
    if kind == "macro":
        return "Macros aren't available on this keyboard."
    if kind == "function" and b.get("code") not in FUNCTIONS:
        return "This keyboard switches profiles, not layers."
    if kind == "key" and b.get("code") in MODIFIER_USAGES:
        return "This keyboard can't turn a key into a modifier; choose a key with a modifier instead."
    return ""


def keycode(b: Optional[dict], default_usage: int) -> Tuple[int, int, int, int]:
    """The four bytes for a key: None means the key's own usage."""
    if b is None or b.get("kind") == "default":
        return (0, 0, 0, default_usage)
    kind = b["kind"]
    if kind == "disabled":
        return (0, 0, 0, 0)
    if kind == "key":
        mods = 0
        for m in b.get("mods") or []:
            mods |= MOD_BITS[m]
        return (0, mods, 0, b["code"])
    if kind == "media":
        usage = MEDIA[b["code"]]
        return (2, 0, usage >> 8, usage & 0xFF)
    if kind == "mouse":
        return (1, MOUSE[b["code"]], 0, 0)
    if kind == "function":
        return (6, FUNCTIONS[b["code"]], 0, 0)
    raise ValueError(f"unknown binding {kind!r}")


def key_frame(layer: int, position: int, code: Sequence[int]) -> bytes:
    """One key on one layer of the active profile (CORE always writes the Windows key table)."""
    if not 0 <= layer < LAYERS:
        raise ValueError("layer is 0 to 2")
    if not 1 <= position <= 106:
        raise ValueError("unknown key")
    f = bytearray(_frame(CMD_KEY, layer & 3, [position >> 8, position & 0xFF, *code]))
    f[6] = 6                    # CORE's length byte for a key write
    f[FRAME_LEN - 1] = checksum(f)
    return bytes(f)


def check(frame: bytes) -> Optional[str]:
    """Why a frame mustn't go to an Eternal (None = fine)."""
    if len(frame) != FRAME_LEN or frame[0] != REPORT_ID or frame[FRAME_LEN - 1] != checksum(frame):
        return "not a settings frame"
    if frame[1] == CMD_CONFIG and frame[2] in ALLOWED_CONFIG:
        return None
    if frame[1] in (CMD_KEY, CMD_PROFILE):
        return None
    return "not a settings frame"


def keys_job(layout: str, layer: int, old: Dict[str, dict], new: Dict[str, dict], default_usage) -> list:
    """One frame per key whose binding changed (a key back to normal gets its own usage)."""
    index = KEY_INDEX[layout]
    out = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        b = new.get(key)
        if b is not None:
            problem = binding_problem(b)
            if problem:
                raise ValueError(problem)
        out.append((key_frame(layer, index[key], keycode(b, default_usage(key))), DELAY))
    return out
