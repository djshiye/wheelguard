"""Glorious GMMK Pro, GMMK 2 (65%, 96%) and wired GMMK 3 / GMMK 3 Pro (65%, 75%, 100%) keyboards on their
stock (Glorious CORE) firmware: discovery, the frames, and the transport. Lighting is not here: RGBeast owns it (see docs/gmmk-protocol.md, "Sharing the board").

The board takes vendor Feature reports, report ID 7, on the HID interface with usage page 0xFF01,
usage 0x01. It can't report its settings back, so the caller keeps them (gui/keyboard.py) and sends
whole frames, the way Glorious CORE does. Every frame builder here is a pure function, so the bytes are
unit-tested without hardware.

Never touched: the bootloader IDs (BOOTLOADER_PIDS) and anything to do with firmware updates. The
Fn key and the knob are never remapped, so the board's own shortcuts always stay reachable.
"""
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .hidraw import DEV_DIR, SYSFS_HIDRAW, HidrawDevice, _nodes

VENDOR = 0x320F          # GMMK Pro and GMMK 2
VENDOR_GMMK3 = 0x342D    # GMMK 3 / GMMK 3 Pro, wired
REPORT_ID = 7
FRAME_LEN = 264          # what Glorious CORE builds (report ID included); padded to the descriptor's size
SHORT_LEN = 64           # the profile commit frame

CMD_PROFILE = 0x01       # switch to profile/layer (also "commit" after a change)
CMD_KEYMAP = 0x03
CMD_MACRO_MODE = 0x04
CMD_MACRO_DATA = 0x05
CMD_PROPERTIES = 0x07    # shared with lighting: effect, side lights, brightness, debounce
CMD_POLLING = 0x08

# Delays Glorious CORE leaves after each frame (seconds). The board stores to flash; don't rush it.
DELAY_LONG = 0.55
DELAY_KEYS = 0.15

PROFILES = 3
LAYERS = 3
POLLING_HZ = (1000, 500, 250, 125)          # firmware codes 0, 1, 2, 3
DEBOUNCE_MS = (2, 8, 16)
DEFAULT_POLLING = 1000
DEFAULT_DEBOUNCE = 16
# GMMK 3: minutes without a key press before the lights go out; 255 = never. Sent in byte 11 of the
# properties and polling frames, as CORE does.
SLEEP_MINUTES = (3, 7, 15, 30, 255)
SLEEP_NEVER = 255
DEFAULT_SLEEP = 7
MAX_MACROS = 10
MAX_MACRO_EVENTS = 82                        # 248 data bytes / 3 bytes per event
MACRO_MODES = {"once": 1, "repeat": 0xFFFF, "toggle": 0}

# Usage page 0xFF01 + usage 0x01 (the command interface); 0xFF00 / 0xFF00 (the event interface);
# 0xFF60 is QMK's raw HID page (a board flashed with QMK/VIA, which this protocol must not be sent to).
COMMAND_COLLECTION = bytes.fromhex("0601ff0901")
EVENT_COLLECTION = bytes.fromhex("0600ff0a00ff")
QMK_PAGE = bytes.fromhex("0660ff")

# Lighting fields of the properties frame when RGBeast isn't there to supply them: the board's own
# defaults (Glorious mode, full brightness, side lights on).
DEFAULT_LIGHTING = {"effect": 1, "brightness": 20, "sidelights": True}


@dataclass(frozen=True)
class KeyboardModel:
    key: str
    name: str
    layout: str              # "ansi" | "iso"
    keymap: str              # which index map (the "Alt" ANSI board has / and \ swapped)
    form: str = "pro"        # "pro" (75% with a knob) | "65" | "96" | "g3-65" | "g3-75" | "g3-100": drawing, defaults
    commit_keys: bool = True  # CORE re-selects the profile after a key assignment on the Pro only
    confirmed: bool = False  # nobody has tried Wheelguard on this board yet: controls start locked
    vendor: int = VENDOR
    key_count: int = 120     # positions in the key assignment frame
    sleep: bool = False      # has the idle timer (GMMK 3)


MODELS = {
    0x5044: KeyboardModel("gmmk-pro-ansi", "Glorious GMMK Pro", "ansi", "ansi"),
    0x5046: KeyboardModel("gmmk-pro-iso", "Glorious GMMK Pro (ISO)", "iso", "iso"),
    0x5092: KeyboardModel("gmmk-pro-ansi-alt", "Glorious GMMK Pro", "ansi", "ansi-alt"),
    0x5093: KeyboardModel("gmmk-pro-iso-alt", "Glorious GMMK Pro (ISO)", "iso", "iso"),
    0x5045: KeyboardModel("gmmk2-65-ansi", "Glorious GMMK 2 65%", "ansi", "65-ansi", "65", False),
    0x504A: KeyboardModel("gmmk2-65-iso", "Glorious GMMK 2 65% (ISO)", "iso", "65-iso", "65", False),
    0x504B: KeyboardModel("gmmk2-96-ansi", "Glorious GMMK 2 96%", "ansi", "96-ansi", "96", False),
    0x505A: KeyboardModel("gmmk2-96-iso", "Glorious GMMK 2 96% (ISO)", "iso", "96-iso", "96", False),
}


def _gmmk3(key: str, name: str, iso: bool, size: str, pro: bool) -> KeyboardModel:
    return KeyboardModel(key, name, "iso" if iso else "ansi", "g3-iso" if iso else "g3-ansi", f"g3-{size}", False,
                         vendor=VENDOR_GMMK3, key_count=126, sleep=True)


for _pid, _size, _iso, _pro in ((0xE3C8, "100", False, False), (0xE3D1, "100", True, False),
                               (0xE3C9, "75", False, False), (0xE3D2, "75", True, False),
                               (0xE3CA, "65", False, False), (0xE3D3, "65", True, False),
                               (0xE3C5, "100", False, True), (0xE3CE, "100", True, True),
                               (0xE3C6, "75", False, True), (0xE3CF, "75", True, True),
                               (0xE3C7, "65", False, True), (0xE3D0, "65", True, True)):
    _key = f"gmmk3{'-pro' if _pro else ''}-{_size}-{'iso' if _iso else 'ansi'}"
    _name = f"Glorious GMMK 3{' Pro' if _pro else ''} {_size}%{' (ISO)' if _iso else ''}"
    MODELS[_pid] = _gmmk3(_key, _name, _iso, _size, _pro)
GMMK_PRO = MODELS            # the name before the GMMK 2 joined
# Pro ANSI, ISO, Alt ANSI, Alt ISO; GMMK 2 65 ANSI, 65 ISO, 96 ANSI, 96 ISO. The GMMK 3's bootloader has a
# vendor of its own (2442) that Wheelguard never matches.
BOOTLOADER_PIDS = frozenset((0xB007, 0xB009, 0xB00F, 0xB010, 0xB008, 0xB00A, 0xB00B, 0xB00C))

# ---------------------------------------------------------------- key positions in the keymap frame
_ANSI = {
    "Escape": 0, "F1": 1, "F2": 2, "F3": 3, "F4": 4, "F5": 5, "F6": 6, "F7": 7, "F8": 8, "F9": 9,
    "F10": 10, "F11": 11, "F12": 12, "PrintScreen": 13, "Knob": 14,
    "Backquote": 20, "Digit1": 21, "Digit2": 22, "Digit3": 23, "Digit4": 24, "Digit5": 25, "Digit6": 26,
    "Digit7": 27, "Digit8": 28, "Digit9": 29, "Digit0": 30, "Minus": 31, "Equal": 32, "Backspace": 33,
    "Delete": 34,
    "Tab": 40, "KeyQ": 41, "KeyW": 42, "KeyE": 43, "KeyR": 44, "KeyT": 45, "KeyY": 46, "KeyU": 47,
    "KeyI": 48, "KeyO": 49, "KeyP": 50, "BracketLeft": 51, "BracketRight": 52, "Backslash": 53, "PageUp": 54,
    "CapsLock": 60, "KeyA": 61, "KeyS": 62, "KeyD": 63, "KeyF": 64, "KeyG": 65, "KeyH": 66, "KeyJ": 67,
    "KeyK": 68, "KeyL": 69, "Semicolon": 70, "Quote": 71, "Enter": 73, "PageDown": 74,
    "ShiftLeft": 80, "KeyZ": 81, "KeyX": 82, "KeyC": 83, "KeyV": 84, "KeyB": 85, "KeyN": 86, "KeyM": 87,
    "Comma": 88, "Period": 89, "Slash": 90, "ShiftRight": 91, "ArrowUp": 93, "End": 94,
    "ControlLeft": 100, "MetaLeft": 101, "AltLeft": 102, "Space": 105, "AltRight": 108, "Fn": 109,
    "ControlRight": 110, "ArrowLeft": 112, "ArrowDown": 113, "ArrowRight": 114,
}
_ISO = dict(_ANSI, Hashtag=72, Backslash=81, KeyZ=82, KeyX=83, KeyC=84, KeyV=85, KeyB=86, KeyN=87,
            KeyM=88, Comma=89, Period=90, Slash=91, ShiftRight=92)
_ANSI_ALT = dict(_ANSI, Backslash=90, Slash=53)

# GMMK 2. Read from the board's default key assignment (what each position types), which is also where
# CORE's own tables put every key except / and \: those two are named the other way round there.
_ROW_NUM = {"Digit1": 21, "Digit2": 22, "Digit3": 23, "Digit4": 24, "Digit5": 25, "Digit6": 26, "Digit7": 27,
            "Digit8": 28, "Digit9": 29, "Digit0": 30, "Minus": 31, "Equal": 32, "Backspace": 33}
_ROW_Q = {"Tab": 40, "KeyQ": 41, "KeyW": 42, "KeyE": 43, "KeyR": 44, "KeyT": 45, "KeyY": 46, "KeyU": 47,
          "KeyI": 48, "KeyO": 49, "KeyP": 50, "BracketLeft": 51, "BracketRight": 52}
_ROW_A = {"CapsLock": 60, "KeyA": 61, "KeyS": 62, "KeyD": 63, "KeyF": 64, "KeyG": 65, "KeyH": 66, "KeyJ": 67,
          "KeyK": 68, "KeyL": 69, "Semicolon": 70, "Quote": 71, "Enter": 73}
_ROW_Z = {"ShiftLeft": 80, "KeyZ": 82, "KeyX": 83, "KeyC": 84, "KeyV": 85, "KeyB": 86, "KeyN": 87, "KeyM": 88,
          "Comma": 89, "Period": 90, "Slash": 91, "ShiftRight": 92, "ArrowUp": 93}
_V2_ANSI = dict(Backslash=53)
_V2_ISO = dict(Hashtag=72, Backslash=81)
_65 = {"Escape": 20, **_ROW_NUM, "Delete": 34, **_ROW_Q, "PageUp": 54, **_ROW_A, "PageDown": 74, **_ROW_Z,
       "End": 94, "ControlLeft": 100, "MetaLeft": 101, "AltLeft": 102, "Space": 105, "AltRight": 108,
       "Fn": 109, "ArrowLeft": 112, "ArrowDown": 113, "ArrowRight": 114}
_96 = {"Escape": 0, "F1": 1, "F2": 2, "F3": 3, "F4": 4, "F5": 5, "F6": 6, "F7": 7, "F8": 8, "F9": 9, "F10": 10,
       "F11": 11, "F12": 12, "PrintScreen": 13, "Delete": 14, "Insert": 15, "PageUp": 16, "PageDown": 17,
       "Backquote": 20, **_ROW_NUM, "NumLock": 34, "NumpadDivide": 35, "NumpadMultiply": 36,
       "NumpadSubtract": 37, **_ROW_Q, "Numpad7": 54, "Numpad8": 55, "Numpad9": 56, **_ROW_A, "Numpad4": 74,
       "Numpad5": 75, "Numpad6": 76, "NumpadAdd": 77, **_ROW_Z, "Numpad1": 94, "Numpad2": 95, "Numpad3": 96,
       "ControlLeft": 100, "MetaLeft": 101, "AltLeft": 102, "Space": 105, "AltRight": 109, "Fn": 110,
       "ControlRight": 111, "ArrowLeft": 112, "ArrowDown": 113, "ArrowRight": 114, "Numpad0": 115,
       "NumpadDecimal": 116, "NumpadEnter": 117}
# GMMK 3 (wired): every size shares one 126-position matrix in which a key keeps its place whatever the
# board's size (the 65%'s Page Up is where a 100%'s is). Read from the default key assignment; CORE's own
# tables agree apart from a few typos. The knob press is position 20.
_G3 = {"Escape": 0, **{f"F{n}": n + 1 for n in range(1, 13)}, "PrintScreen": 14, "ScrollLock": 15, "Pause": 16,
       "Knob": 20, "Backquote": 21, **{f"Digit{d}": 22 + i for i, d in enumerate("1234567890")}, "Minus": 32,
       "Equal": 33, "Backspace": 34, "Insert": 35, "Home": 36, "PageUp": 37, "NumLock": 38, "NumpadDivide": 39,
       "NumpadMultiply": 40, "NumpadSubtract": 41,
       "Tab": 42, **{f"Key{c}": 43 + i for i, c in enumerate("QWERTYUIOP")}, "BracketLeft": 53,
       "BracketRight": 54, "Delete": 56, "End": 57, "PageDown": 58, "Numpad7": 59, "Numpad8": 60, "Numpad9": 61,
       "NumpadAdd": 62,
       "CapsLock": 63, **{f"Key{c}": 64 + i for i, c in enumerate("ASDFGHJKL")}, "Semicolon": 73, "Quote": 74,
       "Enter": 76, "Numpad4": 80, "Numpad5": 81, "Numpad6": 82,
       "ShiftLeft": 84, **{f"Key{c}": 86 + i for i, c in enumerate("ZXCVBNM")}, "Comma": 93, "Period": 94,
       "Slash": 95, "ShiftRight": 97, "ArrowUp": 99, "Numpad1": 101, "Numpad2": 102, "Numpad3": 103,
       "NumpadEnter": 104,
       "ControlLeft": 105, "MetaLeft": 106, "AltLeft": 107, "Space": 110, "AltRight": 115, "Fn": 116,
       "ContextMenu": 117, "ControlRight": 118, "ArrowLeft": 119, "ArrowDown": 120, "ArrowRight": 121,
       "Numpad0": 122, "NumpadDecimal": 124}
KEY_INDEX = {"ansi": _ANSI, "iso": _ISO, "ansi-alt": _ANSI_ALT,
             "g3-ansi": dict(_G3, Backslash=55), "g3-iso": dict(_G3, Hashtag=75, Backslash=85),
             "65-ansi": {**_65, **_V2_ANSI}, "65-iso": {**_65, **_V2_ISO},
             "96-ansi": {**_96, **_V2_ANSI}, "96-iso": {**_96, **_V2_ISO}}
KEY_COUNT = 120                              # the Pro and GMMK 2; a model's own count is model.key_count
LOCKED_KEYS = frozenset(("Fn", "Knob"))      # never remapped

# The board's own key assignment, as Glorious CORE sends it: 120 type bytes then 120 code bytes (0xFF = no
# key there). Type 1 = key (code = HID usage), 2 = modifier, 3 = media (code = media function).
_DEFAULT_ANSI = bytes.fromhex(
    "010101010101010101010101010103ffffffffff010101010101010101010101010101ffffffffff0101010101010101"
    "01010101010101ffffffffff010101010101010101010101010101ffffffffff020101010101010101010102010101ff"
    "ffffffff020202ffff01ffff020102ff010101ffffffffff293a3b3c3d3e3f4041424344454602ffffffffff351e1f20"
    "212223242526272d2e2a4cffffffffff2b141a0815171c180c12132f30314bffffffffff39041607090a0b0d0e0f3334"
    "32284effffffffffe11d1b0619051110363738e564524dffffffffffe0e3e2ffff2cffffe6a0e4ff50514fffffffffff")
_DEFAULT_ISO = bytes.fromhex(
    "010101010101010101010101010103ffffffffff010101010101010101010101010101ffffffffff0101010101010101"
    "01010101010101ffffffffff010101010101010101010101010101ffffffffff020101010101010101010101020101ff"
    "ffffffff020202ffff01ffff020102ff010101ffffffffff293a3b3c3d3e3f4041424344454602ffffffffff351e1f20"
    "212223242526272d2e2a4cffffffffff2b141a0815171c180c12132f30314bffffffffff39041607090a0b0d0e0f3334"
    "32284effffffffffe1641d1b0619051110363738e5524dffffffffffe0e3e2ffff2cffffe6a0e4ff50514fffffffffff")


# GMMK 2: one assignment for both 65% boards and one for both 96% boards.
_DEFAULT_65 = bytes.fromhex(
    "010101010101010101010101010103ffffffffff010101010101010101010101010101ffffffffff0101010101010101"
    "01010101010101ffffffffff010101010101010101010101010101ffffffffff020101010101010101010101020101ff"
    "ffffffff020202ffff01ffff020102ff010101ffffffffff353a3b3c3d3e3f4041424344454602ffffffffff291e1f20"
    "212223242526272d2e2a4cffffffffff2b141a0815171c180c12132f30314bffffffffff39041607090a0b0d0e0f3334"
    "32284effffffffffe1641d1b0619051110363738e5524dffffffffffe0e3e2ffff2cffffe6a0e4ff50514fffffffffff")
_DEFAULT_96 = bytes.fromhex(
    "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
    "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
    "010101010101010101010101010101010101010101010101293a3b3c3d3e3f404142434445464c494b4effff351e1f20"
    "212223242526272d2e2a53545556ffff2b141a0815171c180c12132f30315f6061ffffff39041607090a0b0d0e0f3334"
    "32285c5d5e57ffffe1641d1b0619051110363738e552595a5bffffffe0e3e2ffff2cffffffe6a0e450514f626358ffff")


# GMMK 3 (wired): one assignment (126 types, 126 codes, 4 spare) for every size; the GMMK 3 Pro 100% ANSI's
# differs only in leaving out the knob's turn (positions 17 and 18).
_DEFAULT_G3 = bytes.fromhex(
    "01ff0101010101010101010101010101010303ff03010101010101010101010101010101010101010101010101010101"
    "0101010101010101010101010101010101010101010101010101010101ffffff010101ff020101010101010101010101"
    "ff02ff01ff01010101020202ffff01ffffffff0201010201010101ff01ff29ff3a3b3c3d3e3f40414243444546474804"
    "03ff02351e1f20212223242526272d2e2a494a4b535455562b141a0815171c180c12132f30314c4d4e5f606157390416"
    "07090a0b0d0e0f33343228ffffff5c5d5effe1641d1b0619051110363738ffe5ff525b595a5b58e0e3e2ffff2cffffff"
    "ffe6a065e450514f62ff63ffffffffff")


def default_keymap(model: KeyboardModel) -> bytes:
    if model.vendor == VENDOR_GMMK3:
        if model.key == "gmmk3-pro-100-ansi":
            km = bytearray(_DEFAULT_G3)
            for i in (17, 18):
                km[i] = km[126 + i] = 0xFF
            return bytes(km)
        return _DEFAULT_G3
    if model.form == "65":
        return _DEFAULT_65
    if model.form == "96":
        return _DEFAULT_96
    return _DEFAULT_ISO if model.layout == "iso" else _DEFAULT_ANSI


# ---------------------------------------------------------------- what a key can do
# A binding is a small dict, stored as JSON by the GUI:
#   {"kind": "default"}                                   the board's own key
#   {"kind": "disabled"}
#   {"kind": "key", "code": 4, "mods": ["ctrl"]}          a key (HID usage), optionally with modifiers
#   {"kind": "media", "code": 1}                          MEDIA below
#   {"kind": "mouse", "code": 1}                          MOUSE below
#   {"kind": "function", "code": 2}                       FUNCTIONS below (profile/layer cycling)
#   {"kind": "macro", "slot": 1}                          a macro Wheelguard stored on the board
MEDIA = {0: "Media player", 1: "Play / Pause", 2: "Mute", 3: "Volume up", 4: "Volume down", 5: "Stop",
         6: "Previous track", 7: "Next track"}
MOUSE = {1: "Left click", 2: "Right click", 3: "Middle click", 4: "Back", 5: "Forward", 6: "Scroll up",
         7: "Scroll down"}
FUNCTIONS = {2: "Next profile", 1: "Previous profile", 4: "Next layer", 3: "Previous layer"}
MOD_BITS = {"ctrl": 1, "shift": 2, "alt": 4, "super": 8}
MODIFIER_USAGES = frozenset(range(224, 232))
T_KEY, T_MODIFIER, T_MEDIA, T_MACRO, T_FUNCTION, T_MOUSE, T_COMBO = 1, 2, 3, 5, 8, 9, 0xE0


def binding_problem(b: dict, macro_slots: Sequence[int] = ()) -> str:
    """Why a binding can't be sent ("" = fine)."""
    kind = b.get("kind")
    if kind in ("default", "disabled"):
        return ""
    if kind == "key":
        code = b.get("code")
        if not isinstance(code, int) or not 4 <= code <= 231:
            return "Unknown key."
        mods = b.get("mods") or []
        if any(m not in MOD_BITS for m in mods):
            return "Unknown modifier."
        if mods and code in MODIFIER_USAGES:
            return "A modifier key can't take extra modifiers."
        return ""
    table = {"media": MEDIA, "mouse": MOUSE, "function": FUNCTIONS}.get(kind)
    if table is not None:
        return "" if b.get("code") in table else "Unknown action."
    if kind == "macro":
        return "" if b.get("slot") in macro_slots else "That macro doesn't exist."
    return "Unknown action."


def encode_binding(b: dict) -> Tuple[int, int]:
    """(type byte, code byte) for a binding that isn't "default"."""
    kind = b["kind"]
    if kind == "disabled":
        return T_KEY, 0
    if kind == "key":
        code = b["code"]
        mods = 0
        for m in b.get("mods") or []:
            mods |= MOD_BITS[m]
        if mods:
            return T_COMBO + mods, code
        return (T_MODIFIER if code in MODIFIER_USAGES else T_KEY), code
    if kind == "media":
        return T_MEDIA, b["code"]
    if kind == "mouse":
        return T_MOUSE, b["code"]
    if kind == "function":
        return T_FUNCTION, b["code"]
    if kind == "macro":
        return T_MACRO, b["slot"]
    raise ValueError(f"unknown binding {kind!r}")


# ---------------------------------------------------------------- frames
def _frame(cmd: int, length: int = FRAME_LEN) -> bytearray:
    f = bytearray(length)
    f[0], f[1] = REPORT_ID, cmd
    return f


def _check_slot(profile: int, layer: int) -> None:
    if not (0 <= profile < PROFILES and 0 <= layer < LAYERS):
        raise ValueError("profile and layer are 0 to 2")


def profile_frame(profile: int, layer: int) -> bytes:
    """Switch to (and commit) a profile and layer, both 0-based."""
    _check_slot(profile, layer)
    f = _frame(CMD_PROFILE, SHORT_LEN)
    f[2], f[3] = profile + 1, layer + 1
    return bytes(f)


def _check_sleep(sleep: Optional[int]) -> None:
    if sleep is not None and not 1 <= sleep <= SLEEP_NEVER:
        raise ValueError(f"sleep timer {sleep} isn't supported")


def polling_frame(hz: int, sleep: Optional[int] = None) -> bytes:
    """`sleep`: the GMMK 3's idle timer in minutes (255 never), which CORE repeats in this frame."""
    if hz not in POLLING_HZ:
        raise ValueError(f"polling rate {hz} Hz isn't supported")
    _check_sleep(sleep)
    f = _frame(CMD_POLLING)
    f[8], f[9], f[10] = POLLING_HZ.index(hz), 1, 0
    if sleep is not None:
        f[11] = sleep
    return bytes(f)


def properties_frame(profile: int, layer: int, debounce_ms: int, lighting: Optional[dict] = None,
                     sleep: Optional[int] = None) -> bytes:
    """The properties frame: debounce plus the lighting fields it shares with RGBeast, and on the GMMK 3
    the idle timer (`sleep`, minutes; 255 never)."""
    _check_slot(profile, layer)
    _check_sleep(sleep)
    if debounce_ms not in DEBOUNCE_MS:
        raise ValueError(f"debounce {debounce_ms} ms isn't supported")
    light = dict(DEFAULT_LIGHTING, **(lighting or {}))
    effect, brightness = int(light["effect"]), int(light["brightness"])
    if not (0 <= effect <= 18 and 0 <= brightness <= 20):
        raise ValueError("lighting fields out of range")
    f = _frame(CMD_PROPERTIES)
    f[2], f[3], f[4] = profile + 1, layer + 1, effect
    f[5], f[6] = 0, 1
    f[7] = 0 if light["sidelights"] else 1
    f[8] = brightness
    f[10] = debounce_ms
    if sleep is not None:
        f[11] = sleep
    return bytes(f)


def keymap_frame(model: KeyboardModel, profile: int, layer: int, bindings: Dict[str, dict],
                 macro_slots: Sequence[int] = ()) -> bytes:
    """The whole key assignment of one layer: the board's defaults with `bindings` applied."""
    _check_slot(profile, layer)
    index = KEY_INDEX[model.keymap]
    data = bytearray(default_keymap(model))
    for key, b in bindings.items():
        if key in LOCKED_KEYS:
            raise ValueError(f"{key} can't be remapped")
        if key not in index:
            raise ValueError(f"unknown key {key!r}")
        if b.get("kind") == "default":
            continue
        problem = binding_problem(b, macro_slots)
        if problem:
            raise ValueError(f"{key}: {problem}")
        i = index[key]
        data[i], data[model.key_count + i] = encode_binding(b)
    f = _frame(CMD_KEYMAP)
    f[2], f[3] = profile + 1, layer + 1
    f[8:8 + len(data)] = data
    return bytes(f)


def macro_mode_frame(model: KeyboardModel, profile: int, layer: int, bindings: Dict[str, dict],
                     modes: Dict[int, str]) -> bytes:
    """How each key bound to a macro plays it (once, repeat while held, toggle)."""
    _check_slot(profile, layer)
    index = KEY_INDEX[model.keymap]
    f = _frame(CMD_MACRO_MODE)
    f[2], f[3] = profile + 1, layer + 1
    for key, b in bindings.items():
        if b.get("kind") != "macro":
            continue
        value = MACRO_MODES[modes.get(b["slot"], "once")]
        off = 8 + index[key] * 2
        f[off], f[off + 1] = value >> 8, value & 0xFF
    return bytes(f)


def macro_problem(events: Sequence[dict]) -> str:
    if not events:
        return "The macro is empty."
    if len(events) > MAX_MACRO_EVENTS:
        return f"A macro holds at most {MAX_MACRO_EVENTS} key presses and releases."
    for e in events:
        if e.get("type") not in ("press", "release"):
            return "Unknown macro step."
        if not isinstance(e.get("code"), int) or not 4 <= e["code"] <= 231:
            return "Unknown key in the macro."
        if not isinstance(e.get("delay"), int) or not 1 <= e["delay"] <= 0x7FFF:
            return "Delays are 1 to 32767 ms."
    return ""


def macro_frame(slot: int, events: Sequence[dict]) -> bytes:
    """One macro. Each event: {"type": "press"|"release", "code": HID usage, "delay": ms until the next}."""
    if not 1 <= slot <= MAX_MACROS:
        raise ValueError("macro slot out of range")
    problem = macro_problem(events)
    if problem:
        raise ValueError(problem)
    f = _frame(CMD_MACRO_DATA)
    f[2] = slot
    for i, e in enumerate(events):
        off = 8 + i * 3
        f[off] = (e["delay"] >> 8) | (0x80 if e["type"] == "press" else 0)
        f[off + 1] = e["delay"] & 0xFF
        f[off + 2] = e["code"]
    return bytes(f)


# ---------------------------------------------------------------- report descriptor
def feature_report_length(desc: bytes, report_id: int) -> int:
    """Bytes of Feature report `report_id`, report ID byte included (0 = the descriptor has none).

    A minimal HID descriptor walk: global Report Size / Count / ID, Push / Pop, and Feature main items.
    """
    size = count = rid = 0
    stack: List[Tuple[int, int, int]] = []
    bits: Dict[int, int] = {}
    i = 0
    while i < len(desc):
        prefix = desc[i]
        if prefix == 0xFE:                       # long item: skip it
            if i + 1 >= len(desc):
                break
            i += 3 + desc[i + 1]
            continue
        n = (0, 1, 2, 4)[prefix & 3]
        val = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        tag = prefix & 0xFC
        if tag == 0x74:
            size = val
        elif tag == 0x94:
            count = val
        elif tag == 0x84:
            rid = val
        elif tag == 0xA4:
            stack.append((size, count, rid))
        elif tag == 0xB4 and stack:
            size, count, rid = stack.pop()
        elif tag == 0xB0:                        # Feature
            bits[rid] = bits.get(rid, 0) + size * count
        i += 1 + n
    if report_id not in bits:
        return 0
    return (bits[report_id] + 7) // 8 + 1


# ---------------------------------------------------------------- discovery
@dataclass(frozen=True)
class Board:
    path: str                   # /dev/hidrawN of the command interface ("" when not usable)
    pid: int
    model: KeyboardModel
    firmware: str               # "0045" style, from the USB device's bcdDevice ("" unknown)
    report_len: int             # Feature report 7 size from the descriptor (0 = none)
    event_path: str = ""        # /dev/hidrawN of the event interface ("" none)
    qmk: bool = False           # flashed with QMK/VIA: this protocol isn't spoken


def _firmware(sysfs: str, name: str) -> str:
    try:
        hid_dir = os.path.realpath(os.path.join(sysfs, name, "device"))
        usb_dev = os.path.dirname(os.path.dirname(hid_dir))       # hid -> interface -> device
        with open(os.path.join(usb_dev, "bcdDevice")) as f:
            return f.read().strip().zfill(4)
    except OSError:
        return ""


def discover(sysfs: str = SYSFS_HIDRAW, dev_dir: str = DEV_DIR) -> List[Board]:
    """Supported boards that are plugged in. sysfs reads only: nothing is opened."""
    seen: Dict[int, dict] = {}
    for name, vid, pid, desc in _nodes(sysfs):
        if pid not in MODELS or vid != MODELS[pid].vendor:
            continue
        b = seen.setdefault(pid, {"path": "", "event": "", "len": 0, "qmk": False, "fw": ""})
        if COMMAND_COLLECTION in desc and not b["path"]:
            b["path"], b["len"] = os.path.join(dev_dir, name), feature_report_length(desc, REPORT_ID)
            b["fw"] = _firmware(sysfs, name)
        elif EVENT_COLLECTION in desc and not b["event"]:
            b["event"] = os.path.join(dev_dir, name)
        if QMK_PAGE in desc:
            b["qmk"] = True
            b["fw"] = b["fw"] or _firmware(sysfs, name)
    return [Board(b["path"], pid, MODELS[pid], b["fw"], b["len"], b["event"], b["qmk"] and not b["path"])
            for pid, b in sorted(seen.items())]


# ---------------------------------------------------------------- transport
class Transport:
    """Sends frames to one board's command interface, padded to the descriptor's report size."""

    def __init__(self, board: Board, sleep, opener=HidrawDevice):
        if board.qmk or not board.path:
            raise ValueError("this board doesn't speak the Glorious protocol")
        if board.report_len < SHORT_LEN or board.report_len > 1024:
            raise ValueError(f"unexpected settings report size ({board.report_len} bytes)")
        self.board = board
        self._sleep = sleep
        self._dev = opener(board.path)

    def send(self, frame: bytes, delay: float = DELAY_LONG) -> None:
        if not frame or frame[0] != REPORT_ID:
            raise ValueError("not a settings frame")
        n = self.board.report_len
        if len(frame) > n and any(frame[n:]):
            raise ValueError("frame doesn't fit the board's report")
        self._dev.set_feature(bytes(frame[:n]).ljust(n, b"\0"))
        self._sleep(delay)

    def close(self) -> None:
        self._dev.close()


# ---------------------------------------------------------------- drawing (key units; None = a gap)
_TOP = [("Escape", 1), (None, .25), ("F1", 1), ("F2", 1), ("F3", 1), ("F4", 1), (None, .25), ("F5", 1),
        ("F6", 1), ("F7", 1), ("F8", 1), (None, .25), ("F9", 1), ("F10", 1), ("F11", 1), ("F12", 1),
        (None, .25), ("PrintScreen", 1), ("Knob", 1)]
_NUM = [("Backquote", 1)] + [(f"Digit{d}", 1) for d in "1234567890"] + [("Minus", 1), ("Equal", 1),
                                                                          ("Backspace", 2), ("Delete", 1)]
_Q = [("Tab", 1.5)] + [(f"Key{c}", 1) for c in "QWERTYUIOP"] + [("BracketLeft", 1), ("BracketRight", 1)]
_A = [("CapsLock", 1.75)] + [(f"Key{c}", 1) for c in "ASDFGHJKL"] + [("Semicolon", 1), ("Quote", 1)]
_Z = [(f"Key{c}", 1) for c in "ZXCVBNM"] + [("Comma", 1), ("Period", 1), ("Slash", 1)]
_BOTTOM = [("ControlLeft", 1.25), ("MetaLeft", 1.25), ("AltLeft", 1.25), ("Space", 6.25), ("AltRight", 1),
           ("Fn", 1), ("ControlRight", 1), ("ArrowLeft", 1), ("ArrowDown", 1), ("ArrowRight", 1)]
_BOTTOM_65 = _BOTTOM[:5] + [("Fn", 1), (None, 1), ("ArrowLeft", 1), ("ArrowDown", 1), ("ArrowRight", 1)]
_TOP_96 = ([("Escape", 1), (None, .25)] + [(f"F{n}", 1) for n in range(1, 5)] + [(None, .25)]
           + [(f"F{n}", 1) for n in range(5, 9)] + [(None, .25)] + [(f"F{n}", 1) for n in range(9, 13)]
           + [(None, .25), ("PrintScreen", 1), ("Delete", 1), ("Insert", 1), ("PageUp", 1), ("PageDown", 1)])
_NUM_96 = _NUM[:-1] + [("NumLock", 1), ("NumpadDivide", 1), ("NumpadMultiply", 1), ("NumpadSubtract", 1)]
_PAD7 = [("Numpad7", 1), ("Numpad8", 1), ("Numpad9", 1), ("NumpadAdd", 1, 2)]
_PAD4 = [("Numpad4", 1), ("Numpad5", 1), ("Numpad6", 1)]
_PAD1 = [("ArrowUp", 1), ("Numpad1", 1), ("Numpad2", 1), ("Numpad3", 1), ("NumpadEnter", 1, 2)]
_BOTTOM_96 = _BOTTOM[:-3] + [("ArrowLeft", 1), ("ArrowDown", 1), ("ArrowRight", 1), ("Numpad0", 1),
                             ("NumpadDecimal", 1)]
_SHIFT_ANSI = [("ShiftLeft", 2.25)] + _Z + [("ShiftRight", 1.75)]
_SHIFT_ISO = [("ShiftLeft", 1.25), ("Backslash", 1)] + _Z + [("ShiftRight", 1.75)]
_Q_ANSI, _Q_ISO = _Q + [("Backslash", 1.5)], _Q + [("Enter", 1.5)]
_A_ANSI, _A_ISO = _A + [("Enter", 2.25)], _A + [("Hashtag", 1), ("EnterLower", 1.25)]
# GMMK 3: the 75% and 65% have a 1.5-wide right Alt and Fn and no right Ctrl; the badge is a light, drawn as a gap.
_BOTTOM_G3 = _BOTTOM[:4] + [("AltRight", 1.5), ("Fn", 1.5), ("ArrowLeft", 1), ("ArrowDown", 1), ("ArrowRight", 1)]
_TOP_G3_75 = _TOP[:-2] + [(None, 1), ("Knob", 1)]
_TOP_G3_100 = (_TOP[:1] + [(None, 1)] + [(f"F{n}", 1) for n in range(1, 5)] + [(None, .5)]
               + [(f"F{n}", 1) for n in range(5, 9)] + [(None, .5)] + [(f"F{n}", 1) for n in range(9, 13)]
               + [(None, .25), ("PrintScreen", 1), ("ScrollLock", 1), ("Pause", 1), (None, 3.25), ("Knob", 1)])
_NAV_G3 = [(None, .25), ("Insert", 1), ("Home", 1), ("PageUp", 1), (None, .25), ("NumLock", 1),
           ("NumpadDivide", 1), ("NumpadMultiply", 1), ("NumpadSubtract", 1)]
_DEL_G3 = [(None, .25), ("Delete", 1), ("End", 1), ("PageDown", 1), (None, .25)] + _PAD7
_PAD1_G3 = [(None, 1.25), ("ArrowUp", 1), (None, 1.25)] + _PAD1[1:]
_BOTTOM_G3_100 = (_BOTTOM[:5] + [("AltRight", 1.25), ("Fn", 1.25), ("ContextMenu", 1.25), ("ControlRight", 1.25),
                                 (None, .25), ("ArrowLeft", 1), ("ArrowDown", 1), ("ArrowRight", 1), (None, .25),
                                 ("Numpad0", 2), ("NumpadDecimal", 1)])
_BOTTOM_G3_100[3:5] = [("Space", 6.25)]
_SHIFT_G3_ANSI = _SHIFT_ANSI[:-1] + [("ShiftRight", 2.75)]
_SHIFT_G3_ISO = _SHIFT_ISO[:-1] + [("ShiftRight", 2.75)]
# Each key is (id, width) or (id, width, height) in key units; None is a gap.
ROWS = {
    ("pro", "ansi"): [_TOP, _NUM, _Q_ANSI + [("PageUp", 1)], _A_ANSI + [("PageDown", 1)],
                      _SHIFT_ANSI + [("ArrowUp", 1), ("End", 1)], _BOTTOM],
    ("pro", "iso"): [_TOP, _NUM, _Q_ISO + [("PageUp", 1)], _A_ISO + [("PageDown", 1)],
                     _SHIFT_ISO + [("ArrowUp", 1), ("End", 1)], _BOTTOM],
    ("65", "ansi"): [[("Escape", 1)] + _NUM[1:], _Q_ANSI + [("PageUp", 1)], _A_ANSI + [("PageDown", 1)],
                     _SHIFT_ANSI + [("ArrowUp", 1), ("End", 1)], _BOTTOM_65],
    ("65", "iso"): [[("Escape", 1)] + _NUM[1:], _Q_ISO + [("PageUp", 1)], _A_ISO + [("PageDown", 1)],
                    _SHIFT_ISO + [("ArrowUp", 1), ("End", 1)], _BOTTOM_65],
    ("96", "ansi"): [_TOP_96, _NUM_96, _Q_ANSI + _PAD7, _A_ANSI + _PAD4, _SHIFT_ANSI + _PAD1, _BOTTOM_96],
    ("96", "iso"): [_TOP_96, _NUM_96, _Q_ISO + _PAD7, _A_ISO + _PAD4, _SHIFT_ISO + _PAD1, _BOTTOM_96],
    ("g3-65", "ansi"): [[("Escape", 1)] + _NUM[1:-1] + [("Knob", 1)], _Q_ANSI + [(None, 1)],
                        _A_ANSI + [("PageUp", 1)], _SHIFT_ANSI + [("ArrowUp", 1), ("PageDown", 1)], _BOTTOM_G3],
    ("g3-65", "iso"): [[("Escape", 1)] + _NUM[1:-1] + [("Knob", 1)], _Q_ISO + [(None, 1)],
                       _A_ISO + [("PageUp", 1)], _SHIFT_ISO + [("ArrowUp", 1), ("PageDown", 1)], _BOTTOM_G3],
    ("g3-75", "ansi"): [_TOP_G3_75, _NUM, _Q_ANSI + [("PageUp", 1)], _A_ANSI + [("PageDown", 1)],
                        _SHIFT_ANSI + [("ArrowUp", 1), ("End", 1)], _BOTTOM_G3],
    ("g3-75", "iso"): [_TOP_G3_75, _NUM, _Q_ISO + [("PageUp", 1)], _A_ISO + [("PageDown", 1)],
                       _SHIFT_ISO + [("ArrowUp", 1), ("End", 1)], _BOTTOM_G3],
    ("g3-100", "ansi"): [_TOP_G3_100, _NUM[:-1] + _NAV_G3, _Q_ANSI + _DEL_G3, _A_ANSI + [(None, 3.5)] + _PAD4,
                         _SHIFT_G3_ANSI + _PAD1_G3, _BOTTOM_G3_100],
    ("g3-100", "iso"): [_TOP_G3_100, _NUM[:-1] + _NAV_G3, _Q_ISO + _DEL_G3, _A_ISO + [(None, 3.5)] + _PAD4,
                        _SHIFT_G3_ISO + _PAD1_G3, _BOTTOM_G3_100],
}
LABELS = {
    "Escape": "Esc", "PrintScreen": "PrtSc", "Knob": "Knob", "Backquote": "`", "Minus": "-", "Equal": "=",
    "Backspace": "Backspace", "Delete": "Del", "Tab": "Tab", "BracketLeft": "[", "BracketRight": "]",
    "Backslash": "\\", "PageUp": "PgUp", "CapsLock": "Caps", "Semicolon": ";", "Quote": "'", "Enter": "Enter",
    "EnterLower": "", "Hashtag": "#", "PageDown": "PgDn", "ShiftLeft": "Shift", "Comma": ",", "Period": ".",
    "Slash": "/", "ShiftRight": "Shift", "ArrowUp": "↑", "End": "End", "ControlLeft": "Ctrl",
    "MetaLeft": "Super", "AltLeft": "Alt", "Space": "", "AltRight": "Alt", "Fn": "Fn", "ControlRight": "Ctrl",
    "ArrowLeft": "←", "ArrowDown": "↓", "ArrowRight": "→", "Insert": "Ins", "NumLock": "Num",
    "NumpadDivide": "/", "NumpadMultiply": "*", "NumpadSubtract": "-", "NumpadAdd": "+", "NumpadEnter": "Enter",
    "NumpadDecimal": ".", "ScrollLock": "ScrLk", "Pause": "Pause", "Home": "Home", "ContextMenu": "Menu",
}
NAMES = dict(LABELS, Backquote="Backtick", Minus="Minus", Equal="Equals", BracketLeft="Left bracket",
             BracketRight="Right bracket", Backslash="Backslash", Semicolon="Semicolon", Quote="Quote",
             Hashtag="Hash", Comma="Comma", Period="Period", Slash="Slash", ArrowUp="Up arrow",
             ArrowDown="Down arrow", ArrowLeft="Left arrow", ArrowRight="Right arrow", Space="Space",
             ShiftLeft="Left Shift", ShiftRight="Right Shift", ControlLeft="Left Ctrl", ControlRight="Right Ctrl",
             AltLeft="Left Alt", AltRight="Right Alt", MetaLeft="Super", PrintScreen="Print Screen",
             Delete="Delete", PageUp="Page Up", PageDown="Page Down", CapsLock="Caps Lock", Escape="Escape",
             Knob="Knob", Insert="Insert", NumLock="Num Lock", NumpadDivide="Num /", NumpadMultiply="Num *",
             NumpadSubtract="Num -", NumpadAdd="Num +", NumpadEnter="Num Enter", NumpadDecimal="Num .",
             ScrollLock="Scroll Lock")


def key_label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    if key.startswith("Key") or key.startswith("Digit") or key.startswith("Numpad"):
        return key[-1]
    return key


def key_name(key: str) -> str:
    """A spoken / tooltip name."""
    if key in NAMES:
        return NAMES[key]
    return key_label(key)


def layout(model: KeyboardModel) -> List[dict]:
    """Every key with its position in key units: {"id", "x", "y", "w", "h"}. ISO's tall Enter is one key
    drawn as two pieces; the lower piece has id "EnterLower" and selects "Enter"."""
    out = []
    for y, row in enumerate(ROWS[(model.form, model.layout)]):
        x = 0.0
        for key, w, *h in row:
            if key is not None:
                out.append({"id": key, "x": x, "y": float(y), "w": float(w), "h": float(h[0] if h else 1)})
            x += w
    return out


def layout_size(model: KeyboardModel) -> Tuple[float, int, bool]:
    """(width in key units, rows, whether the top row is an F-key row set apart from the rest)."""
    keys = layout(model)
    return (max(k["x"] + k["w"] for k in keys), len(ROWS[(model.form, model.layout)]),
            model.form not in ("65", "g3-65"))


# HID usages the key picker offers, grouped (the label is what the button shows).
_LETTERS = [(f"Key{c}", 4 + i) for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
_DIGITS = [(f"Digit{d}", 30 + i) for i, d in enumerate("1234567890")]
PICKER_KEYS = (
    _LETTERS + _DIGITS
    + [(f"F{n}", 57 + n) for n in range(1, 13)] + [(f"F{n}", 104 + n - 13) for n in range(13, 25)]
    + [("Escape", 41), ("Tab", 43), ("CapsLock", 57), ("Space", 44), ("Enter", 40), ("Backspace", 42),
       ("Delete", 76), ("Insert", 73), ("Home", 74), ("End", 77), ("PageUp", 75), ("PageDown", 78),
       ("ArrowUp", 82), ("ArrowDown", 81), ("ArrowLeft", 80), ("ArrowRight", 79), ("PrintScreen", 70),
       ("ScrollLock", 71), ("Pause", 72), ("ContextMenu", 101),
       ("Backquote", 53), ("Minus", 45), ("Equal", 46), ("BracketLeft", 47), ("BracketRight", 48),
       ("Backslash", 49), ("Semicolon", 51), ("Quote", 52), ("Comma", 54), ("Period", 55), ("Slash", 56),
       ("ControlLeft", 224), ("ShiftLeft", 225), ("AltLeft", 226), ("MetaLeft", 227),
       ("ControlRight", 228), ("ShiftRight", 229), ("AltRight", 230), ("MetaRight", 231),
       ("NumLock", 83), ("NumpadDivide", 84), ("NumpadMultiply", 85), ("NumpadSubtract", 86),
       ("NumpadAdd", 87), ("NumpadEnter", 88)]
    + [(f"Numpad{d}", 89 + i) for i, d in enumerate("123456789")] + [("Numpad0", 98), ("NumpadDecimal", 99)]
)
_EXTRA_NAMES = {"Insert": "Insert", "ScrollLock": "Scroll Lock", "Pause": "Pause", "ContextMenu": "Menu",
                "MetaRight": "Right Super", "NumLock": "Num Lock", "NumpadDivide": "Num /",
                "NumpadMultiply": "Num *", "NumpadSubtract": "Num -", "NumpadAdd": "Num +",
                "NumpadEnter": "Num Enter", "NumpadDecimal": "Num ."}
USAGE_NAMES = {code: _EXTRA_NAMES.get(k) or (f"Num {k[-1]}" if k.startswith("Numpad") else key_name(k))
               for k, code in PICKER_KEYS}
USAGE_NAMES[44] = "Space"


def binding_text(b: Optional[dict], macro_names: Optional[Dict[int, str]] = None) -> str:
    """What a key does, in words ("" for the board's own key)."""
    if not b or b.get("kind") == "default":
        return ""
    kind = b["kind"]
    if kind == "disabled":
        return "Off"
    if kind == "key":
        mods = [m.capitalize() for m in ("ctrl", "shift", "alt", "super") if m in (b.get("mods") or [])]
        return "+".join(mods + [USAGE_NAMES.get(b["code"], f"Key {b['code']}")])
    if kind == "media":
        return MEDIA.get(b["code"], "Media")
    if kind == "mouse":
        return MOUSE.get(b["code"], "Mouse")
    if kind == "function":
        return FUNCTIONS.get(b["code"], "Function")
    if kind == "macro":
        return (macro_names or {}).get(b["slot"]) or f"Macro {b['slot']}"
    return ""


# Linux evdev key codes (what Qt reports as nativeScanCode - 8 on X11 and Wayland) to HID usages, so the
# page can learn a key by having it pressed.
EVDEV_TO_USAGE = {
    1: 41, 2: 30, 3: 31, 4: 32, 5: 33, 6: 34, 7: 35, 8: 36, 9: 37, 10: 38, 11: 39, 12: 45, 13: 46, 14: 42,
    15: 43, 16: 20, 17: 26, 18: 8, 19: 21, 20: 23, 21: 28, 22: 24, 23: 12, 24: 18, 25: 19, 26: 47, 27: 48,
    28: 40, 29: 224, 30: 4, 31: 22, 32: 7, 33: 9, 34: 10, 35: 11, 36: 13, 37: 14, 38: 15, 39: 51, 40: 52,
    41: 53, 42: 225, 43: 49, 44: 29, 45: 27, 46: 6, 47: 25, 48: 5, 49: 17, 50: 16, 51: 54, 52: 55, 53: 56,
    54: 229, 55: 85, 56: 226, 57: 44, 58: 57, 59: 58, 60: 59, 61: 60, 62: 61, 63: 62, 64: 63, 65: 64,
    66: 65, 67: 66, 68: 67, 69: 83, 70: 71, 71: 95, 72: 96, 73: 97, 74: 86, 75: 92, 76: 93, 77: 94, 78: 87,
    79: 89, 80: 90, 81: 91, 82: 98, 83: 99, 86: 100, 87: 68, 88: 69, 96: 88, 97: 228, 98: 84, 99: 70,
    100: 230, 102: 74, 103: 82, 104: 75, 105: 80, 106: 79, 107: 77, 108: 81, 109: 78, 110: 73, 111: 76,
    119: 72, 125: 227, 126: 231, 127: 101,
    183: 104, 184: 105, 185: 106, 186: 107, 187: 108, 188: 109, 189: 110, 190: 111, 191: 112, 192: 113,
    193: 114, 194: 115,
}
USAGE_NAMES.setdefault(100, "\\ (ISO)")


def usage_from_scancode(native_scan_code: int) -> int:
    """HID usage for a key Qt saw (0 = not one Wheelguard can assign)."""
    return EVDEV_TO_USAGE.get(native_scan_code - 8, 0)
