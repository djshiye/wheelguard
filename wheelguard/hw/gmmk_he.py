"""Glorious GMMK 3 HE and GMMK 3 Pro HE (Hall-effect keys): frames for their own settings protocol.
See docs/gmmk-protocol.md, "GMMK 3 HE".

Unlike the other Glorious boards, these take one 64-byte command per setting, as a Feature report without
a report ID on usage page 0xFFFF / usage 2, with a checksum. Profile and layer are 0-based. Wheelguard only
writes; the board can't report most settings, so the page shows what Wheelguard last sent.

Never built here (and so impossible to send): factory reset (0x02), calibration (0x1C / 0x1E), firmware,
lighting and the advanced per-key modes (dynamic keystroke, mod-tap, toggle).
"""
from typing import Dict, Optional, Sequence, Tuple

REPORT_ID = 0
FRAME_LEN = 65                  # the report ID byte (0) plus the 64-byte command

CMD_POLLING = 0x04
CMD_PROFILE = 0x05
CMD_SLEEP = 0x12
CMD_KEY = 0x13
CMD_MAGNET = 0x1A
CMD_MAGNET_MODE = 0x1D
ALLOWED = frozenset((CMD_POLLING, CMD_PROFILE, CMD_SLEEP, CMD_KEY, CMD_MAGNET, CMD_MAGNET_MODE))

POLLING_HZ = (8000, 4000, 2000, 1000, 500, 250, 125)      # firmware codes 0..6
DELAY = 0.55                    # CORE's pause after each write
DELAY_KEY = 0.25
PROFILES = LAYERS = 3

# Analog settings, in millimetres of key travel (0.1 mm steps). The bottom-out cut-off ("dead zone") stays
# at the board's default.
ACTUATION_MM = (0.1, 3.6)
SENSITIVITY_MM = (0.1, 2.0)
DEADZONE_MM = 3.7
DEFAULT_ANALOG = {"actuation": 2.0, "rapid": False, "sensitivity": 0.2}

# Command collection (usage page 0xFFFF, usage 2) and event collection (0xFFFF, usage 1).
COMMAND_COLLECTION = bytes.fromhex("06ffff0902")
EVENT_COLLECTION = bytes.fromhex("06ffff0901")

# The board's own number for each key, per size and layout (from CORE's tables; ISO: Enter takes the
# backslash's place, # takes Enter's, and the key left of Z is "Backslash" as everywhere in Wheelguard).
KEY_NUMBERS = {
    "65-ansi": {"Escape": 1, "Tab": 2, "CapsLock": 3, "ShiftLeft": 4, "ControlLeft": 5, "Digit1": 7, "KeyQ": 8, "KeyA": 9,
        "Digit2": 13, "KeyW": 14, "KeyS": 15, "KeyZ": 16, "MetaLeft": 17, "Digit3": 19, "KeyE": 20, "KeyD": 21,
        "KeyX": 22, "AltLeft": 23, "Digit4": 25, "KeyR": 26, "KeyF": 27, "KeyC": 28, "Digit5": 31, "KeyT": 32,
        "KeyG": 33, "KeyV": 34, "Space": 35, "Digit6": 37, "KeyY": 38, "KeyH": 39, "KeyB": 40, "Digit7": 43, "KeyU":
        44, "KeyJ": 45, "KeyN": 46, "AltRight": 47, "Digit8": 49, "KeyI": 50, "KeyK": 51, "KeyM": 52, "Fn": 53,
        "Digit9": 55, "KeyO": 56, "KeyL": 57, "Comma": 58, "Digit0": 61, "KeyP": 62, "Semicolon": 63, "Period": 64,
        "Minus": 67, "BracketLeft": 68, "Quote": 69, "Slash": 70, "Equal": 73, "BracketRight": 74, "ShiftRight": 76,
        "ArrowLeft": 77, "Backspace": 79, "Backslash": 80, "Enter": 81, "ArrowUp": 82, "ArrowDown": 83, "Knob": 84,
        "PageUp": 87, "PageDown": 88, "ArrowRight": 89},
    "65-iso": {"Escape": 1, "Tab": 2, "CapsLock": 3, "ShiftLeft": 4, "ControlLeft": 5, "Digit1": 7, "KeyQ": 8, "KeyA": 9,
        "Backslash": 10, "Digit2": 13, "KeyW": 14, "KeyS": 15, "KeyZ": 16, "MetaLeft": 17, "Digit3": 19, "KeyE": 20,
        "KeyD": 21, "KeyX": 22, "AltLeft": 23, "Digit4": 25, "KeyR": 26, "KeyF": 27, "KeyC": 28, "Digit5": 31,
        "KeyT": 32, "KeyG": 33, "KeyV": 34, "Space": 35, "Digit6": 37, "KeyY": 38, "KeyH": 39, "KeyB": 40, "Digit7":
        43, "KeyU": 44, "KeyJ": 45, "KeyN": 46, "AltRight": 47, "Digit8": 49, "KeyI": 50, "KeyK": 51, "KeyM": 52,
        "Fn": 53, "Digit9": 55, "KeyO": 56, "KeyL": 57, "Comma": 58, "Digit0": 61, "KeyP": 62, "Semicolon": 63,
        "Period": 64, "Minus": 67, "BracketLeft": 68, "Quote": 69, "Slash": 70, "Equal": 73, "BracketRight": 74,
        "ShiftRight": 76, "ArrowLeft": 77, "Backspace": 79, "Enter": 80, "Hashtag": 81, "ArrowUp": 82, "ArrowDown":
        83, "Knob": 84, "PageUp": 87, "PageDown": 88, "ArrowRight": 89},
    "75-ansi": {"Escape": 0, "Backquote": 1, "Tab": 2, "CapsLock": 3, "ShiftLeft": 4, "ControlLeft": 5, "Digit1": 7, "KeyQ":
        8, "KeyA": 9, "F1": 12, "Digit2": 13, "KeyW": 14, "KeyS": 15, "KeyZ": 16, "MetaLeft": 17, "F2": 18,
        "Digit3": 19, "KeyE": 20, "KeyD": 21, "KeyX": 22, "AltLeft": 23, "F3": 24, "Digit4": 25, "KeyR": 26, "KeyF":
        27, "KeyC": 28, "F4": 30, "Digit5": 31, "KeyT": 32, "KeyG": 33, "KeyV": 34, "Space": 35, "F5": 36, "Digit6":
        37, "KeyY": 38, "KeyH": 39, "KeyB": 40, "F6": 42, "Digit7": 43, "KeyU": 44, "KeyJ": 45, "KeyN": 46,
        "AltRight": 47, "F7": 48, "Digit8": 49, "KeyI": 50, "KeyK": 51, "KeyM": 52, "Fn": 53, "F8": 54, "Digit9":
        55, "KeyO": 56, "KeyL": 57, "Comma": 58, "F9": 60, "Digit0": 61, "KeyP": 62, "Semicolon": 63, "Period": 64,
        "F10": 66, "Minus": 67, "BracketLeft": 68, "Quote": 69, "Slash": 70, "F11": 72, "Equal": 73, "BracketRight":
        74, "ShiftRight": 76, "ArrowLeft": 77, "F12": 78, "Backspace": 79, "Backslash": 80, "Enter": 81, "ArrowUp":
        82, "ArrowDown": 83, "Knob": 84, "Delete": 85, "PageUp": 86, "PageDown": 87, "End": 88, "ArrowRight": 89},
    "75-iso": {"Escape": 0, "Backquote": 1, "Tab": 2, "CapsLock": 3, "ShiftLeft": 4, "ControlLeft": 5, "Digit1": 7, "KeyQ":
        8, "KeyA": 9, "Backslash": 10, "F1": 12, "Digit2": 13, "KeyW": 14, "KeyS": 15, "KeyZ": 16, "MetaLeft": 17,
        "F2": 18, "Digit3": 19, "KeyE": 20, "KeyD": 21, "KeyX": 22, "AltLeft": 23, "F3": 24, "Digit4": 25, "KeyR":
        26, "KeyF": 27, "KeyC": 28, "F4": 30, "Digit5": 31, "KeyT": 32, "KeyG": 33, "KeyV": 34, "Space": 35, "F5":
        36, "Digit6": 37, "KeyY": 38, "KeyH": 39, "KeyB": 40, "F6": 42, "Digit7": 43, "KeyU": 44, "KeyJ": 45,
        "KeyN": 46, "AltRight": 47, "F7": 48, "Digit8": 49, "KeyI": 50, "KeyK": 51, "KeyM": 52, "Fn": 53, "F8": 54,
        "Digit9": 55, "KeyO": 56, "KeyL": 57, "Comma": 58, "F9": 60, "Digit0": 61, "KeyP": 62, "Semicolon": 63,
        "Period": 64, "F10": 66, "Minus": 67, "BracketLeft": 68, "Quote": 69, "Slash": 70, "F11": 72, "Equal": 73,
        "BracketRight": 74, "ShiftRight": 76, "ArrowLeft": 77, "F12": 78, "Backspace": 79, "Enter": 80, "Hashtag":
        81, "ArrowUp": 82, "ArrowDown": 83, "Knob": 84, "Delete": 85, "PageUp": 86, "PageDown": 87, "End": 88,
        "ArrowRight": 89},
    "100-ansi": {"Escape": 0, "Backquote": 1, "Tab": 2, "CapsLock": 3, "ShiftLeft": 4, "ControlLeft": 5, "Digit1": 7, "KeyQ":
        8, "KeyA": 9, "F1": 12, "Digit2": 13, "KeyW": 14, "KeyS": 15, "KeyZ": 16, "MetaLeft": 17, "F2": 18,
        "Digit3": 19, "KeyE": 20, "KeyD": 21, "KeyX": 22, "AltLeft": 23, "F3": 24, "Digit4": 25, "KeyR": 26, "KeyF":
        27, "KeyC": 28, "F4": 30, "Digit5": 31, "KeyT": 32, "KeyG": 33, "KeyV": 34, "Space": 35, "F5": 36, "Digit6":
        37, "KeyY": 38, "KeyH": 39, "KeyB": 40, "F6": 42, "Digit7": 43, "KeyU": 44, "KeyJ": 45, "KeyN": 46,
        "AltRight": 47, "F7": 48, "Digit8": 49, "KeyI": 50, "KeyK": 51, "KeyM": 52, "Fn": 53, "F8": 54, "Digit9":
        55, "KeyO": 56, "KeyL": 57, "Comma": 58, "ContextMenu": 59, "F9": 60, "Digit0": 61, "KeyP": 62, "Semicolon":
        63, "Period": 64, "ControlRight": 65, "F10": 66, "Minus": 67, "BracketLeft": 68, "Quote": 69, "Slash": 70,
        "ArrowLeft": 71, "F11": 72, "Equal": 73, "BracketRight": 74, "ShiftRight": 76, "ArrowDown": 77, "F12": 78,
        "Backspace": 79, "Backslash": 80, "Enter": 81, "ArrowUp": 82, "ArrowRight": 83, "PrintScreen": 84, "Insert":
        85, "Delete": 86, "Numpad7": 87, "Numpad4": 88, "Numpad1": 89, "ScrollLock": 90, "Home": 91, "End": 92,
        "Numpad8": 93, "Numpad5": 94, "Numpad2": 95, "Pause": 96, "PageUp": 97, "PageDown": 98, "Numpad9": 99,
        "Numpad6": 100, "Numpad3": 101, "NumLock": 104, "NumpadMultiply": 105, "NumpadDecimal": 106, "Numpad0": 107,
        "NumpadDivide": 110, "NumpadSubtract": 111, "NumpadAdd": 112, "NumpadEnter": 113, "Knob": 255},
    "100-iso": {"Escape": 0, "Backquote": 1, "Tab": 2, "CapsLock": 3, "ShiftLeft": 4, "ControlLeft": 5, "Digit1": 7, "KeyQ":
        8, "KeyA": 9, "Backslash": 10, "F1": 12, "Digit2": 13, "KeyW": 14, "KeyS": 15, "KeyZ": 16, "MetaLeft": 17,
        "F2": 18, "Digit3": 19, "KeyE": 20, "KeyD": 21, "KeyX": 22, "AltLeft": 23, "F3": 24, "Digit4": 25, "KeyR":
        26, "KeyF": 27, "KeyC": 28, "F4": 30, "Digit5": 31, "KeyT": 32, "KeyG": 33, "KeyV": 34, "Space": 35, "F5":
        36, "Digit6": 37, "KeyY": 38, "KeyH": 39, "KeyB": 40, "F6": 42, "Digit7": 43, "KeyU": 44, "KeyJ": 45,
        "KeyN": 46, "AltRight": 47, "F7": 48, "Digit8": 49, "KeyI": 50, "KeyK": 51, "KeyM": 52, "Fn": 53, "F8": 54,
        "Digit9": 55, "KeyO": 56, "KeyL": 57, "Comma": 58, "ContextMenu": 59, "F9": 60, "Digit0": 61, "KeyP": 62,
        "Semicolon": 63, "Period": 64, "ControlRight": 65, "F10": 66, "Minus": 67, "BracketLeft": 68, "Quote": 69,
        "Slash": 70, "ArrowLeft": 71, "F11": 72, "Equal": 73, "BracketRight": 74, "ShiftRight": 76, "ArrowDown": 77,
        "F12": 78, "Backspace": 79, "Enter": 80, "Hashtag": 81, "ArrowUp": 82, "ArrowRight": 83, "PrintScreen": 84,
        "Insert": 85, "Delete": 86, "Numpad7": 87, "Numpad4": 88, "Numpad1": 89, "ScrollLock": 90, "Home": 91,
        "End": 92, "Numpad8": 93, "Numpad5": 94, "Numpad2": 95, "Pause": 96, "PageUp": 97, "PageDown": 98,
        "Numpad9": 99, "Numpad6": 100, "Numpad3": 101, "NumLock": 104, "NumpadMultiply": 105, "NumpadDecimal": 106,
        "Numpad0": 107, "NumpadDivide": 110, "NumpadSubtract": 111, "NumpadAdd": 112, "NumpadEnter": 113, "Knob":
        255},
}

MODIFIER_USAGES = frozenset(range(0xE0, 0xE8))
MEDIA = {0: (3, 0, 0x83, 0x01), 1: (3, 0, 0xCD, 0), 2: (3, 0, 0xE2, 0), 3: (3, 0, 0xE9, 0), 4: (3, 0, 0xEA, 0),
         5: (3, 0, 0xB7, 0), 6: (3, 0, 0xB6, 0), 7: (3, 0, 0xB5, 0)}
MOUSE = {1: (1, 0, 0xF0, 2), 2: (1, 0, 0xF1, 0), 3: (1, 0, 0xF2, 0), 4: (1, 0, 0xF4, 0), 5: (1, 0, 0xF3, 0),
         6: (1, 0, 0xF8, 0xFF), 7: (1, 0, 0xF8, 1)}
MOD_USAGE = {"ctrl": 0xE0, "shift": 0xE1, "alt": 0xE2, "super": 0xE3}


def checksum(c: bytes) -> int:
    return 255 - (sum(c[0:7]) & 0xFF)


def _frame(cmd: int, params: Sequence[int] = (), payload: Dict[int, int] = None) -> bytes:
    if cmd not in ALLOWED:
        raise ValueError(f"command {cmd:#x} is never sent")
    c = bytearray(FRAME_LEN - 1)
    c[0] = cmd
    for i, v in enumerate(params, 1):
        c[i] = v
    for i, v in (payload or {}).items():
        c[i] = v
    c[7] = checksum(c)
    return bytes([REPORT_ID]) + bytes(c)


def _check_slot(profile: int, layer: int) -> None:
    if not (0 <= profile < PROFILES and 0 <= layer < LAYERS):
        raise ValueError("profile and layer are 0 to 2")


def polling_frame(profile: int, hz: int) -> bytes:
    _check_slot(profile, 0)
    if hz not in POLLING_HZ:
        raise ValueError(f"polling rate {hz} Hz isn't supported")
    return _frame(CMD_POLLING, (profile, POLLING_HZ.index(hz)))


def profile_frame(profile: int, layer: int) -> bytes:
    _check_slot(profile, layer)
    return _frame(CMD_PROFILE, (profile, layer))


def sleep_frame(minutes: int, wireless: bool) -> bytes:
    """Standby timer in minutes (255 = never). A wired board gets its USB timer only; a wireless one gets
    the same timer for its cable, 2.4 GHz and Bluetooth links, as CORE sends it. Deep sleep stays off."""
    if not 1 <= minutes <= 255:
        raise ValueError(f"standby {minutes} min isn't supported")
    s = 0 if minutes == 255 else minutes * 60
    pay: Dict[int, int] = {}

    def u16(at: int, v: int):
        pay[at], pay[at + 1] = v & 0xFF, v >> 8
    for at in (12, 14, 18):         # deep sleep, every link: never
        u16(at, 0xFFFF)
    u16(16, s)                      # USB
    if wireless:
        u16(8, s)                   # Bluetooth
        u16(10, s)                  # 2.4 GHz
    return _frame(CMD_SLEEP, (), pay)


def mm_to_fw(mm: float) -> int:
    return max(1, min(40, round(mm * 10) - 1))


def magnet_mode_frame() -> bytes:
    """"Custom" analog mode, so the board uses the values sent instead of a built-in preset."""
    return _frame(CMD_MAGNET_MODE, (3,))


def check_analog(a: dict) -> None:
    act, sens = a.get("actuation"), a.get("sensitivity")
    if not isinstance(a.get("rapid"), bool):
        raise ValueError("rapid trigger is on or off")
    for v, (lo, hi), what in ((act, ACTUATION_MM, "actuation point"), (sens, SENSITIVITY_MM, "sensitivity")):
        if not isinstance(v, (int, float)) or not lo - 1e-9 <= v <= hi + 1e-9 or abs(v * 10 - round(v * 10)) > 1e-6:
            raise ValueError(f"{what} {v!r} mm isn't supported")


def actuation_frame(profile: int, layer: int, analog: dict) -> bytes:
    """Every key's actuation point and rapid trigger for one profile and layer."""
    _check_slot(profile, layer)
    check_analog(analog)
    act = mm_to_fw(analog["actuation"])
    pay = {8: act, 25: mm_to_fw(DEADZONE_MM)}
    if analog["rapid"]:
        pay[9] = pay[10] = mm_to_fw(analog["sensitivity"])
    return _frame(CMD_MAGNET, ((layer << 4) | profile, 0, 0x80 if analog["rapid"] else 0, act, 1, 0), pay)


def analog_job(profile: int, layer: int, analog: dict) -> list:
    """What CORE sends to apply the analog settings: select the layer, Custom mode, then the values."""
    return [(profile_frame(profile, layer), DELAY), (magnet_mode_frame(), DELAY),
            (actuation_frame(profile, layer, analog), DELAY)]


def default_keycode(key: str, usage: int) -> Tuple[int, int, int, int]:
    return (0, usage, 0, 0) if usage in MODIFIER_USAGES else (0, 0, usage, 0)


def binding_problem(b: dict) -> str:
    kind = b.get("kind")
    if kind == "key" and len(b.get("mods") or []) > 1:
        return "This keyboard takes one modifier with a key."
    if kind == "macro":
        return "Macros aren't available on this keyboard."
    return ""


def keycode(b: dict) -> Tuple[int, int, int, int]:
    kind = b["kind"]
    if kind == "disabled":
        return (0, 0, 0, 0)
    if kind == "key":
        code, mods = b["code"], b.get("mods") or []
        if binding_problem(b):
            raise ValueError(binding_problem(b))
        if code in MODIFIER_USAGES:
            return (0, code, 0, 0)
        return (0, MOD_USAGE[mods[0]] if mods else 0, code, 0)
    if kind == "media":
        return MEDIA[b["code"]]
    if kind == "mouse":
        return MOUSE[b["code"]]
    if kind == "function":
        if b["code"] not in (1, 2, 3, 4):
            raise ValueError("unknown function")
        return (8, 0, b["code"], 0)
    raise ValueError(f"{kind} isn't available on this keyboard")


def key_frame(profile: int, layer: int, number: int, code: Sequence[int]) -> bytes:
    _check_slot(profile, layer)
    if not 0 <= number < 168 or len(code) != 4:
        raise ValueError("bad key")
    return _frame(CMD_KEY, (profile, number, layer, 0), {8 + i: v for i, v in enumerate(code)})


def parse_event(data: bytes) -> Optional[Tuple[int, int]]:
    """(profile, layer), 0-based, from a "profile changed on the board" event."""
    if len(data) >= 4 and data[0] == 5 and data[1] == 0x01 and data[2] < PROFILES and data[3] < LAYERS:
        return data[2], data[3]
    return None
