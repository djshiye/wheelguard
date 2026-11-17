"""Glorious GMBK 75% (ANSI / ISO): frames for its own settings commands. See docs/gmmk-protocol.md, "GMBK".

Same envelope as the GMMK boards (Feature report 7 on usage page 0xFF01 / usage 1), but its own command set:
one settings frame per profile (polling rate, debounce, standby, OS mode, Win-lock, knob sensitivity) and a
key assignment made of (type, code) pairs. Wheelguard only writes; the board can't report most settings.

Never built here (and so impossible to send): lighting (0x02), macros (0x12: CORE numbers them two ways, so
which is right can't be told without the board) and anything to do with firmware.
"""
from typing import Dict, Optional

REPORT_ID = 7
CMD_PROFILE = 0x01
CMD_KEYS = 0x03
CMD_SETTINGS = 0x11
ALLOWED = frozenset((CMD_PROFILE, CMD_KEYS, CMD_SETTINGS))
KEYS_LEN = 256                  # CORE's key frame: header, then 126 (type, code) pairs
SETTINGS_LEN = 9                # CORE's settings frame (padded to the report size on the way out)
PAIRS = 126
DELAY = 0.15                    # CORE pauses no time at all; a little is kinder to the board

# Polling: the frame takes the scan period minus one (1 ms -> 0), not the GMMK's 0 to 3.
POLLING_HZ = (1000, 500, 250, 125)
_POLLING_CODE = {1000: 0, 500: 1, 250: 3, 125: 7}
DEFAULT_DEBOUNCE = 2            # the board's default; its unit isn't certain, so Wheelguard never changes it
STANDBY_MAX = 60                # minutes; 0 = never
PROFILES = LAYERS = 3

# Pair index of each key (byte offset / 2 in CORE's buffer). The knob turns at 17 (down) and 18 (up) and
# presses at 19; CORE's own name table is wrong for the backtick, ISO # and ISO \ and the knob press.
_ANSI = {
    "Escape": 1, **{f"F{n}": 1 + n for n in range(1, 13)}, "PrintScreen": 14, "ScrollLock": 15, "Pause": 16,
    "Knob": 19, "Backquote": 21, **{f"Digit{d}": 22 + i for i, d in enumerate("1234567890")},
    "Minus": 32, "Equal": 33, "Backspace": 34, "Insert": 35, "Home": 36, "PageUp": 37,
    "Tab": 42, **{f"Key{c}": 43 + i for i, c in enumerate("QWERTYUIOP")}, "BracketLeft": 53, "BracketRight": 54,
    "Backslash": 55, "Delete": 56, "End": 57, "PageDown": 58,
    "CapsLock": 63, **{f"Key{c}": 64 + i for i, c in enumerate("ASDFGHJKL")}, "Semicolon": 73, "Quote": 74,
    "Enter": 76, "ShiftLeft": 84, **{f"Key{c}": 86 + i for i, c in enumerate("ZXCVBNM")}, "Comma": 93,
    "Period": 94, "Slash": 95, "ShiftRight": 97, "ArrowUp": 99, "ControlLeft": 105, "MetaLeft": 106,
    "AltLeft": 107, "Space": 110, "AltRight": 115, "Fn": 116, "ContextMenu": 117, "ControlRight": 118,
    "ArrowLeft": 119, "ArrowDown": 120, "ArrowRight": 121,
}
KEY_INDEX = {"ansi": _ANSI, "iso": dict(_ANSI, Hashtag=75, Backslash=85)}
# Right Alt is a layer key on the second layer (type 0x0D), so it stays as the board has it, like Fn.
LOCKED = frozenset(("AltRight",))

# CORE's default buffer for layers 1 and 3, pairs 0..125 as sent (0xFFFF = no key there). Layer 2 differs
# only at right Alt (pair 115), which is a layer key there.
_DEFAULT = bytes.fromhex(
    "ffff0129013a013b013c013d013e013f014001410142014301440145014601470148030403030302ffff0135011e011f"
    "01200121012201230124012501260127012d012e012a0149014a014b0153015401550156012b0114011a010801150117"
    "011c0118010c01120113012f01300131014c014d014e015f01600161015701390104011601070109010a010b010d010e"
    "010f0133013401320128ffffffffffff015c015d015effff02e10164011d011b01060119010501110110013601370138"
    "ffff02e5ffff0152ff5b0159015a015b015802e002e302e2ffffffff012cffffffffffffffff02c30d01016502e40150"
    "0151014f0162ffff0163ffff"
)
_LAYER2_ALT_RIGHT = (0x0D, 0x02)
MOD_INDEX = {"ctrl": 0, "shift": 1, "alt": 2, "super": 3}     # type 0xE0 + index: one modifier per key
T_COMBO = 0xE0


def _check_slot(profile: int, layer: int) -> None:
    if not (0 <= profile < PROFILES and 0 <= layer < LAYERS):
        raise ValueError("profile and layer are 0 to 2")


def default_pairs(layer: int) -> bytes:
    km = bytearray(_DEFAULT)
    if layer == 1:
        km[2 * 115], km[2 * 115 + 1] = _LAYER2_ALT_RIGHT
    return bytes(km)


def settings_frame(profile: int, polling: int, standby: int) -> bytes:
    """One profile's settings. `standby` in minutes (0 or 255 = never); Win-lock off, Windows mode, the
    knob's default sensitivity and the board's default debounce, as CORE sends them."""
    _check_slot(profile, 0)
    if polling not in _POLLING_CODE:
        raise ValueError(f"polling rate {polling} Hz isn't supported")
    minutes = 0 if standby == 255 else standby
    if not 0 <= minutes <= STANDBY_MAX:
        raise ValueError(f"standby {standby} min isn't supported")
    f = bytearray(SETTINGS_LEN)
    f[0], f[1], f[2] = REPORT_ID, CMD_SETTINGS, profile + 1
    f[3], f[4], f[5], f[6], f[7], f[8] = 0, 0, _POLLING_CODE[polling], DEFAULT_DEBOUNCE, minutes, 0
    return bytes(f)


def binding_problem(b: dict) -> str:
    """What the GMBK can't do that the GMMK boards can ("" = fine)."""
    if b.get("kind") == "macro":
        return "Macros aren't available on this keyboard."
    if b.get("kind") == "key" and len(b.get("mods") or []) > 1:
        return "This keyboard takes one modifier with a key."
    return ""


def encode(b: dict, gmmk_encode) -> tuple:
    """(type, code) for a binding; only a key with a modifier differs from the GMMK encoding."""
    if b["kind"] == "key" and b.get("mods"):
        return T_COMBO + MOD_INDEX[b["mods"][0]], b["code"]
    return gmmk_encode(b)


def keys_frame(layout: str, profile: int, layer: int, bindings: Dict[str, dict], gmmk_encode,
               locked: frozenset) -> bytes:
    """One layer's whole key assignment: the board's defaults with `bindings` applied."""
    _check_slot(profile, layer)
    index = KEY_INDEX[layout]
    pairs = bytearray(default_pairs(layer))
    for key, b in bindings.items():
        if key in locked or key in LOCKED:
            raise ValueError(f"{key} can't be remapped")
        if key not in index:
            raise ValueError(f"unknown key {key!r}")
        if b.get("kind") == "default":
            continue
        problem = binding_problem(b)
        if problem:
            raise ValueError(f"{key}: {problem}")
        i = index[key]
        pairs[2 * i], pairs[2 * i + 1] = encode(b, gmmk_encode)
    f = bytearray(KEYS_LEN)
    f[0], f[1], f[2], f[3] = REPORT_ID, CMD_KEYS, profile + 1, layer + 1
    f[4:4 + len(pairs)] = pairs
    return bytes(f)


def check(frame: bytes) -> Optional[str]:
    """Why a frame mustn't go to a GMBK (None = fine)."""
    if len(frame) < 2 or frame[0] != REPORT_ID or frame[1] not in ALLOWED:
        return "not a settings frame"
    return None
