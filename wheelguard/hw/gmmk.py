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

from . import gmbk, gmmk_eternal, gmmk_he
from .hidraw import DEV_DIR, SYSFS_HIDRAW, HidrawDevice, _nodes

VENDOR = 0x320F          # GMMK Pro and GMMK 2
VENDOR_GMMK3 = 0x342D    # GMMK 3 / GMMK 3 Pro
VENDOR_3794 = 0x3794     # newer Glorious boards (GMMK 3 HE revisions, GMBK, Eternal)
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
    polling_in_properties: bool = False   # GMMK 3 Pro Wireless: no 0x08 frame; the rate is byte 9 of 0x07
    macros: bool = True      # False where CORE's macro frames can't be trusted (the Numpad)
    protocol: str = "gmmk"   # "gmmk" (these frames) | "he" (gmmk_he.py)
    wireless: bool = False   # has 2.4 GHz / Bluetooth links (Wheelguard still only talks over the cable)
    default_debounce: int = 16   # ms, what the board starts with
    default_sleep: int = 7       # minutes (255 never), what the board starts with


@dataclass(frozen=True)
class Protocol:
    report_id: int
    command: bytes           # the command collection in the HID report descriptor
    event: Optional[bytes]   # the event collection (None: Wheelguard doesn't listen)
    polling: Tuple[int, ...]  # rates the board takes, fastest first
    output: bool = False     # settings go as output reports (write), not Feature reports


PROTOCOLS = {
    "gmmk": Protocol(REPORT_ID, COMMAND_COLLECTION, EVENT_COLLECTION, POLLING_HZ),
    "he": Protocol(gmmk_he.REPORT_ID, gmmk_he.COMMAND_COLLECTION, gmmk_he.EVENT_COLLECTION, gmmk_he.POLLING_HZ),
    # The GMBK's event interface only reports whether a write worked, so it isn't opened.
    "gmbk": Protocol(gmbk.REPORT_ID, COMMAND_COLLECTION, None, gmbk.POLLING_HZ),
    "eternal": Protocol(gmmk_eternal.REPORT_ID, gmmk_eternal.COMMAND_COLLECTION, None, gmmk_eternal.POLLING_HZ,
                        output=True),
}


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
# GMMK 3 Pro Wireless, over its USB cable only (Wheelguard never writes through the 2.4 GHz receiver or
# Bluetooth): same frames, 110 key positions, and the polling rate travels in the properties frame.
for _pid, _size, _iso in ((0xE3CB, "100", False), (0xE3D4, "100", True), (0xE3CC, "75", False),
                          (0xE3D5, "75", True), (0xE3CD, "65", False), (0xE3D6, "65", True)):
    _lay = "iso" if _iso else "ansi"
    MODELS[_pid] = KeyboardModel(
        f"gmmk3-pro-wireless-{_size}-{_lay}", f"Glorious GMMK 3 Pro Wireless {_size}%{' (ISO)' if _iso else ''}",
        _lay, f"g3w{'-65' if _size == '65' else ''}-{_lay}", f"g3-{_size}", False, vendor=VENDOR_GMMK3,
        key_count=110, sleep=True, polling_in_properties=True)
# GMMK 3 HE and GMMK 3 Pro HE, wired and wireless, over USB. Each board also has two USB IDs under vendor
# 3794 (later revisions, per CORE: the same board). Wheelguard never writes through their receivers or Bluetooth.
for _ids, _line, _size, _iso, _wl in (
        ((0xE3DF, 0xB31B, 0xB308), "he", "100", False, False), ((0xE3F4, 0xB33B, 0xB328), "he", "100", True, False),
        ((0xE3DE, 0xB31A, 0xB307), "he", "75", False, False), ((0xE3F3, 0xB33A, 0xB327), "he", "75", True, False),
        ((0xE3DD, 0xB319, 0xB306), "he", "65", False, False), ((0xE3F2, 0xB339, 0xB326), "he", "65", True, False),
        ((0xE3DC, 0xB312, 0xB302), "pro-he", "100", False, False),
        ((0xE3F1, 0xB332, 0xB322), "pro-he", "100", True, False),
        ((0xE3DB, 0xB311, 0xB301), "pro-he", "75", False, False),
        ((0xE3F0, 0xB331, 0xB321), "pro-he", "75", True, False),
        ((0xE3DA, 0xB310, 0xB300), "pro-he", "65", False, False),
        ((0xE3EF, 0xB330, 0xB320), "pro-he", "65", True, False),
        ((0xE3D9, 0xB317, 0xB305), "pro-he", "100", False, True), ((0xE3EE, 0xB337, 0xB325), "pro-he", "100", True, True),
        ((0xE3D8, 0xB315, 0xB304), "pro-he", "75", False, True), ((0xE3ED, 0xB335, 0xB324), "pro-he", "75", True, True),
        ((0xE3D7, 0xB313, 0xB303), "pro-he", "65", False, True), ((0xE3EC, 0xB333, 0xB323), "pro-he", "65", True, True)):
    _lay = "iso" if _iso else "ansi"
    _key = f"gmmk3-{_line}{'-wireless' if _wl else ''}-{_size}-{_lay}"
    _name = (f"Glorious GMMK 3 {'Pro HE' if _line == 'pro-he' else 'HE'}{' Wireless' if _wl else ''} {_size}%"
             f"{' (ISO)' if _iso else ''}")
    for _vid, _pid in zip((VENDOR_GMMK3, VENDOR_3794, VENDOR_3794), _ids, strict=True):
        MODELS[_pid] = KeyboardModel(_key, _name, _lay, f"he-{_size}-{_lay}", f"g3-{_size}", False, vendor=_vid,
                                     sleep=True, macros=False, protocol="he", wireless=_wl)
# GMMK Numpad (USB; its Bluetooth link is never used): 20 key positions. CORE's macro frames for it use
# the lighting index instead of the key's, so Wheelguard leaves macros off on it.
MODELS[0x5088] = KeyboardModel("gmmk-numpad", "Glorious GMMK Numpad", "ansi", "numpad", "numpad", True,
                               key_count=20, macros=False)
# GMBK 75%: its own settings and key frames (gmbk.py); no macros. Right Alt is a layer key and stays put.
for _pid, _iso in ((0xB000, False), (0xB001, True)):
    _lay = "iso" if _iso else "ansi"
    MODELS[_pid] = KeyboardModel(f"gmbk-75-{_lay}", f"Glorious GMBK 75%{' (ISO)' if _iso else ''}", _lay,
                                 f"gmbk-{_lay}", "g3-75", False, vendor=VENDOR_3794, key_count=gmbk.PAIRS,
                                 sleep=True, macros=False, protocol="gmbk", default_debounce=gmbk.DEFAULT_DEBOUNCE)
# GMMK Eternal 96: output reports with a checksum, written setting by setting and key by key (gmmk_eternal.py).
for _pid, _iso in ((0xB010, False), (0xB011, True)):
    _lay = "iso" if _iso else "ansi"
    MODELS[_pid] = KeyboardModel(f"gmmk-eternal-96-{_lay}", f"Glorious GMMK Eternal 96%{' (ISO)' if _iso else ''}",
                                 _lay, f"eternal-{_lay}", "96", False, vendor=VENDOR_3794, sleep=True, macros=False,
                                 protocol="eternal", default_debounce=16 if _iso else 8, default_sleep=255)
GMMK_PRO = MODELS            # the name before the GMMK 2 joined
# Pro ANSI, ISO, Alt ANSI, Alt ISO; GMMK 2 65 ANSI, 65 ISO, 96 ANSI, 96 ISO. The GMMK 3's bootloader has a
# vendor of its own (2442) that Wheelguard never matches.
BOOTLOADER_PIDS = frozenset((0xB007, 0xB009, 0xB00F, 0xB010, 0xB008, 0xB00A, 0xB00B, 0xB00C))
# Every bootloader as (vendor, product): the ones above, the GMBK's (B002, B003) and the Eternal's (B012).
BOOTLOADERS = frozenset({(VENDOR, p) for p in BOOTLOADER_PIDS} | {(VENDOR_3794, p) for p in (0xB002, 0xB003, 0xB012)})

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
# GMMK 3 Pro Wireless: its own 110-position matrix (column by column). Read from its default assignment;
# CORE's tables put Super on the Menu key and miss a few keys. On the 65% the top-left key (Esc) sits in
# the backtick's place.
_G3W_SEQ = ("Escape Backquote Tab CapsLock ShiftLeft F1 Digit1 KeyQ KeyA ControlLeft F2 Digit2 KeyW KeyS MetaLeft "
            "F3 Digit3 KeyE KeyZ AltLeft F4 Digit4 KeyR KeyD KeyX F5 Digit5 KeyT KeyF KeyC F6 Digit6 KeyY KeyG KeyV "
            "F7 Digit7 KeyH KeyB Space F8 Digit8 KeyU KeyJ KeyN F9 Digit9 KeyI KeyK KeyM Digit0 KeyO KeyL Comma "
            "AltRight F10 KeyP Semicolon Period Fn Minus BracketLeft Quote Slash ContextMenu F11 Equal BracketRight "
            "ShiftRight ControlRight F12 Backspace Backslash Enter ArrowLeft PrintScreen Insert Delete ArrowUp "
            "ArrowDown Pause ScrollLock Home End ArrowRight PageUp PageDown Numpad4 Numpad1 Numpad0 NumLock Numpad7 "
            "Numpad5 Numpad2 NumpadDecimal NumpadDivide Numpad8 Numpad6 Numpad3 NumpadEnter NumpadMultiply Numpad9 "
            "NumpadSubtract NumpadAdd").split()
_G3W = {k: i for i, k in enumerate(_G3W_SEQ) if k != "Escape"}
_G3W.update(Escape=0, Knob=108)
_G3W_65 = dict(_G3W, Escape=1)
del _G3W_65["Backquote"]
_NUMPAD = {"NumLock": 0, "NumpadDivide": 1, "NumpadMultiply": 2, "NumpadSubtract": 3, "Numpad7": 4, "Numpad8": 5,
           "Numpad9": 6, "NumpadAdd": 7, "Numpad4": 8, "Numpad5": 9, "Numpad6": 10, "Knob": 11, "Numpad1": 12,
           "Numpad2": 13, "Numpad3": 14, "NumpadEnter": 15, "Numpad0": 16, "NumpadDecimal": 19}
KEY_INDEX = {"ansi": _ANSI, "iso": _ISO, "ansi-alt": _ANSI_ALT,
             "g3-ansi": dict(_G3, Backslash=55), "g3-iso": dict(_G3, Hashtag=75, Backslash=85),
             "65-ansi": {**_65, **_V2_ANSI}, "65-iso": {**_65, **_V2_ISO},
             "96-ansi": {**_96, **_V2_ANSI}, "96-iso": {**_96, **_V2_ISO},
             "g3w-ansi": _G3W, "g3w-iso": dict(_G3W, Hashtag=72, Backslash=107),
             "g3w-65-ansi": _G3W_65, "g3w-65-iso": dict(_G3W_65, Hashtag=72, Backslash=107),
             "numpad": _NUMPAD,
             **{f"gmbk-{k}": v for k, v in gmbk.KEY_INDEX.items()},
             **{f"eternal-{k}": v for k, v in gmmk_eternal.KEY_INDEX.items()},
             **{f"he-{k}": v for k, v in gmmk_he.KEY_NUMBERS.items()}}
KEY_COUNT = 120                              # the Pro and GMMK 2; a model's own count is model.key_count
LOCKED_KEYS = frozenset(("Fn", "Knob"))      # never remapped


def locked_keys(model: KeyboardModel) -> frozenset:
    """Keys a board's controls never change: Fn and the knob, plus what its protocol can't put back."""
    extra = {"gmbk": gmbk.LOCKED, "eternal": gmmk_eternal.LOCKED}.get(model.protocol, frozenset())
    return LOCKED_KEYS | extra

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


# GMMK 3 Pro Wireless: one assignment (110 types, 110 codes, 28 spare) for all six boards, as CORE sends it.
_DEFAULT_G3W = bytes.fromhex(
    "010101010201010101020101010102010101010201010101010101010101010101010101010101010101010101010101"
    "0101010101010201010101ff010101010101010101010101010101010101010101010101010101010101010101010101"
    "010101010101010101010101030129292b39e13a1e1404e03b1f1a16e33c20081de23d2115071b3e221709063f231c0a"
    "1940240b052c4125180d1142260c0e1027120f36e643133337fe2d2f343865442e30e5e4452a31285046494c52514847"
    "4a4d4f4b4e5c5962535f5d5a6354605e5b585561565704000000a2030000000000000000000000000000000000000000"
    "0000000000000000")
# GMMK Numpad: 20 types, 20 codes. Position 11 is the knob's press (media: mute); 17 and 18 are empty.
_DEFAULT_NUMPAD = bytes.fromhex("0101010101010101010101030101010101000001535455565f6061575c5d5e02595a5b5862000063")


def _default_g3w(model: KeyboardModel) -> bytes:
    """CORE's assignment with three repairs CORE makes or needs: the knob's turn (positions 104 and 109,
    which CORE always rewrites as media volume down / up), the backtick (CORE's default types Esc there,
    which is right only for the 65%'s Esc in that place) and ISO's key left of Z (empty in CORE's)."""
    km = bytearray(_DEFAULT_G3W)
    n = model.key_count
    km[104], km[n + 104] = T_MEDIA, 3
    km[109], km[n + 109] = T_MEDIA, 4
    if model.form != "g3-65":
        km[n + 1] = 0x35
    if model.layout == "iso":
        km[107], km[n + 107] = T_KEY, 0x64
    return bytes(km)


def default_keymap(model: KeyboardModel) -> bytes:
    if model.keymap == "numpad":
        return _DEFAULT_NUMPAD
    if model.keymap.startswith("g3w"):
        return _default_g3w(model)
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
                     sleep: Optional[int] = None, polling: Optional[int] = None) -> bytes:
    """The properties frame: debounce plus the lighting fields it shares with RGBeast, on the GMMK 3
    the idle timer (`sleep`, minutes; 255 never) and on the GMMK 3 Pro Wireless the polling rate (Hz):
    that board has no polling frame, and a properties frame without the rate would reset it."""
    _check_slot(profile, layer)
    _check_sleep(sleep)
    if polling is not None and polling not in POLLING_HZ:
        raise ValueError(f"polling rate {polling} Hz isn't supported")
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
    if polling is not None:
        f[9] = POLLING_HZ.index(polling)
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
        if b.get("kind") == "macro" and not model.macros:
            raise ValueError(f"{key}: macros aren't available on this keyboard")
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
def feature_report_length(desc: bytes, report_id: int, main_tag: int = 0xB0) -> int:
    """Bytes of Feature report `report_id`, report ID byte included (0 = the descriptor has none).
    `main_tag` 0x90 measures the Output report instead.

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
        elif tag == main_tag:                    # Feature (or Output)
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
        proto = PROTOCOLS[MODELS[pid].protocol]
        b = seen.setdefault(pid, {"path": "", "event": "", "len": 0, "qmk": False, "fw": ""})
        if proto.command in desc and not b["path"]:
            b["path"] = os.path.join(dev_dir, name)
            b["len"] = feature_report_length(desc, proto.report_id, 0x90 if proto.output else 0xB0)
            b["fw"] = _firmware(sysfs, name)
            continue                             # the Eternal's settings page is QMK's page too
        elif proto.event and proto.event in desc and not b["event"]:
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
        if (board.model.vendor, board.pid) in BOOTLOADERS:
            raise ValueError("Wheelguard never writes to a bootloader")
        if board.report_len < SHORT_LEN or board.report_len > 1024:
            raise ValueError(f"unexpected settings report size ({board.report_len} bytes)")
        self.board = board
        self._sleep = sleep
        self._dev = opener(board.path)

    def send(self, frame: bytes, delay: float = DELAY_LONG) -> None:
        proto_name = self.board.model.protocol
        proto = PROTOCOLS[proto_name]
        if not frame or frame[0] != proto.report_id:
            raise ValueError("not a settings frame")
        if proto_name == "he" and frame[1] not in gmmk_he.ALLOWED:
            raise ValueError("not a settings frame")
        why = {"gmbk": gmbk.check, "eternal": gmmk_eternal.check}.get(proto_name, lambda _f: None)(frame)
        if why:
            raise ValueError(why)
        n = self.board.report_len
        if len(frame) > n and any(frame[n:]):
            raise ValueError("frame doesn't fit the board's report")
        data = bytes(frame[:n]).ljust(n, b"\0")
        if proto.output:
            self._dev.write(data)
        else:
            self._dev.set_feature(data)
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
    # GMMK Numpad: the knob sits right of the top row; the slider below it is analog and not a key.
    ("numpad", "ansi"): [[("NumLock", 1), ("NumpadDivide", 1), ("NumpadMultiply", 1), ("NumpadSubtract", 1),
                          (None, .5), ("Knob", 1)],
                         [("Numpad7", 1), ("Numpad8", 1), ("Numpad9", 1), ("NumpadAdd", 1, 2)],
                         [("Numpad4", 1), ("Numpad5", 1), ("Numpad6", 1)],
                         [("Numpad1", 1), ("Numpad2", 1), ("Numpad3", 1), ("NumpadEnter", 1, 2)],
                         [("Numpad0", 2), ("NumpadDecimal", 1)]],
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
            model.form not in ("65", "g3-65", "numpad"))


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


def default_usage(model: KeyboardModel, key: str) -> int:
    """The HID usage a key types by default (for boards written key by key, like the GMMK 3 HE)."""
    if key == "Hashtag":
        return 0x32
    if key == "Backslash" and model.layout == "iso":
        return 0x64
    return dict(PICKER_KEYS)[key]


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
