"""Glorious GMMK Pro keyboards on their stock (Glorious CORE) firmware: discovery, the frames, and the
transport. Lighting is not here: RGBeast owns it (see docs/gmmk-protocol.md, "Sharing the board").

The board takes vendor Feature reports, report ID 7, on the HID interface with usage page 0xFF01,
usage 0x01. It can't report its settings back, so the caller keeps them (gui/keyboard.py) and sends
whole frames, the way Glorious CORE does. Every frame builder here is a pure function, so the bytes are
unit-tested without hardware.

Never touched: the bootloader IDs (B007/B009/B00F/B010) and anything to do with firmware updates. The
Fn key and the knob are never remapped, so the board's own shortcuts always stay reachable.
"""
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .hidraw import DEV_DIR, SYSFS_HIDRAW, HidrawDevice, _nodes

VENDOR = 0x320F
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
    confirmed: bool = False  # nobody has tried Wheelguard on this board yet: controls start locked


GMMK_PRO = {
    0x5044: KeyboardModel("gmmk-pro-ansi", "Glorious GMMK Pro", "ansi", "ansi"),
    0x5046: KeyboardModel("gmmk-pro-iso", "Glorious GMMK Pro (ISO)", "iso", "iso"),
    0x5092: KeyboardModel("gmmk-pro-ansi-alt", "Glorious GMMK Pro", "ansi", "ansi-alt"),
    0x5093: KeyboardModel("gmmk-pro-iso-alt", "Glorious GMMK Pro (ISO)", "iso", "iso"),
}
BOOTLOADER_PIDS = frozenset((0xB007, 0xB009, 0xB00F, 0xB010))

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
KEY_INDEX = {"ansi": _ANSI, "iso": _ISO, "ansi-alt": _ANSI_ALT}
KEY_COUNT = 120
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


def default_keymap(model: KeyboardModel) -> bytes:
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


def polling_frame(hz: int) -> bytes:
    if hz not in POLLING_HZ:
        raise ValueError(f"polling rate {hz} Hz isn't supported")
    f = _frame(CMD_POLLING)
    f[8], f[9], f[10] = POLLING_HZ.index(hz), 1, 0
    return bytes(f)


def properties_frame(profile: int, layer: int, debounce_ms: int, lighting: Optional[dict] = None) -> bytes:
    """The properties frame: debounce plus the lighting fields it shares with RGBeast."""
    _check_slot(profile, layer)
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
        data[i], data[KEY_COUNT + i] = encode_binding(b)
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
    """GMMK Pro boards that are plugged in. sysfs reads only: nothing is opened."""
    seen: Dict[int, dict] = {}
    for name, vid, pid, desc in _nodes(sysfs):
        if vid != VENDOR or pid not in GMMK_PRO:
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
    return [Board(b["path"], pid, GMMK_PRO[pid], b["fw"], b["len"], b["event"], b["qmk"] and not b["path"])
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
ROWS = {
    "ansi": [_TOP, _NUM, _Q + [("Backslash", 1.5), ("PageUp", 1)],
             _A + [("Enter", 2.25), ("PageDown", 1)],
             [("ShiftLeft", 2.25)] + _Z + [("ShiftRight", 1.75), ("ArrowUp", 1), ("End", 1)], _BOTTOM],
    "iso": [_TOP, _NUM, _Q + [("Enter", 1.5), ("PageUp", 1)],
            _A + [("Hashtag", 1), ("EnterLower", 1.25), ("PageDown", 1)],
            [("ShiftLeft", 1.25), ("Backslash", 1)] + _Z + [("ShiftRight", 1.75), ("ArrowUp", 1), ("End", 1)],
            _BOTTOM],
}
LABELS = {
    "Escape": "Esc", "PrintScreen": "PrtSc", "Knob": "Knob", "Backquote": "`", "Minus": "-", "Equal": "=",
    "Backspace": "Backspace", "Delete": "Del", "Tab": "Tab", "BracketLeft": "[", "BracketRight": "]",
    "Backslash": "\\", "PageUp": "PgUp", "CapsLock": "Caps", "Semicolon": ";", "Quote": "'", "Enter": "Enter",
    "EnterLower": "", "Hashtag": "#", "PageDown": "PgDn", "ShiftLeft": "Shift", "Comma": ",", "Period": ".",
    "Slash": "/", "ShiftRight": "Shift", "ArrowUp": "↑", "End": "End", "ControlLeft": "Ctrl",
    "MetaLeft": "Super", "AltLeft": "Alt", "Space": "", "AltRight": "Alt", "Fn": "Fn", "ControlRight": "Ctrl",
    "ArrowLeft": "←", "ArrowDown": "↓", "ArrowRight": "→",
}
NAMES = dict(LABELS, Backquote="Backtick", Minus="Minus", Equal="Equals", BracketLeft="Left bracket",
             BracketRight="Right bracket", Backslash="Backslash", Semicolon="Semicolon", Quote="Quote",
             Hashtag="Hash", Comma="Comma", Period="Period", Slash="Slash", ArrowUp="Up arrow",
             ArrowDown="Down arrow", ArrowLeft="Left arrow", ArrowRight="Right arrow", Space="Space",
             ShiftLeft="Left Shift", ShiftRight="Right Shift", ControlLeft="Left Ctrl", ControlRight="Right Ctrl",
             AltLeft="Left Alt", AltRight="Right Alt", MetaLeft="Super", PrintScreen="Print Screen",
             Delete="Delete", PageUp="Page Up", PageDown="Page Down", CapsLock="Caps Lock", Escape="Escape",
             Knob="Knob")


def key_label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    if key.startswith("Key") or key.startswith("Digit"):
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
    for y, row in enumerate(ROWS[model.layout]):
        x = 0.0
        for key, w in row:
            if key is not None:
                out.append({"id": key, "x": x, "y": float(y), "w": float(w), "h": 1.0})
            x += w
    return out


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
