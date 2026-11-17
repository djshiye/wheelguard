"""Glorious GMMK Pro: frames (checked byte by byte against what Glorious CORE 2.1.23 builds), the report
descriptor walk, discovery from a fake sysfs, the udev helper, and the Keyboard page's controller."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard.hw import gmmk, gmmk_he

ROOT = Path(__file__).resolve().parent.parent
ANSI = gmmk.GMMK_PRO[0x5044]
ISO = gmmk.GMMK_PRO[0x5046]
ALT = gmmk.GMMK_PRO[0x5092]

# A descriptor like the board's settings interface: usage page 0xFF01, usage 1, application collection,
# report ID 7, 263 bytes of 8 bits, Feature.
SETTINGS_DESC = bytes.fromhex("0601ff0901a101" "8507" "150026ff00" "7508" "960701" "0901" "b102" "c0")
EVENTS_DESC = bytes.fromhex("0600ff0a00ffa101" "8504" "7508" "953f" "0901" "8102" "c0")
KEYBOARD_DESC = bytes.fromhex("05010906a101" "0507" "19e029e7" "75019508" "8102" "c0")


def load_helper():
    path = str(ROOT / "build-aux" / "wheelguard-gmmk-check")
    loader = importlib.machinery.SourceFileLoader("wheelguard_gmmk_check", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FrameTests(unittest.TestCase):
    def test_default_keymaps(self):
        a, i = gmmk.default_keymap(ANSI), gmmk.default_keymap(ISO)
        self.assertEqual((len(a), len(i)), (240, 240))
        # every key Wheelguard knows has a real key behind it, at the place CORE puts it
        for name, model in (("ansi", ANSI), ("iso", ISO), ("ansi-alt", ALT)):
            km = gmmk.default_keymap(model)
            for key, idx in gmmk.KEY_INDEX[name].items():
                self.assertNotEqual(km[idx], 0xFF, key)
        self.assertEqual(a[120 + 0], 41)          # Esc
        self.assertEqual(a[120 + 13], 70)         # Print Screen
        self.assertEqual(a[120 + 34], 76)         # Delete
        self.assertEqual((a[109], a[120 + 109]), (1, 160))      # Fn
        self.assertEqual((a[14], a[120 + 14]), (3, 2))          # knob press: mute
        self.assertEqual(i[120 + 81], 100)        # ISO: the extra \ key next to left Shift

    def test_polling(self):
        f = gmmk.polling_frame(500)
        self.assertEqual(len(f), 264)
        self.assertEqual(f[:11], bytes([7, 8, 0, 0, 0, 0, 0, 0, 1, 1, 0]))
        self.assertFalse(any(f[11:]))
        self.assertEqual(gmmk.polling_frame(125)[8], 3)
        with self.assertRaises(ValueError):
            gmmk.polling_frame(8000)

    def test_profile(self):
        f = gmmk.profile_frame(2, 1)
        self.assertEqual(len(f), 64)
        self.assertEqual(f[:4], bytes([7, 1, 3, 2]))
        with self.assertRaises(ValueError):
            gmmk.profile_frame(3, 0)

    def test_properties(self):
        f = gmmk.properties_frame(0, 2, 8)
        self.assertEqual(f[:12], bytes([7, 7, 1, 3, 1, 0, 1, 0, 20, 0, 8, 0]))
        f = gmmk.properties_frame(1, 0, 2, {"effect": 7, "brightness": 10, "sidelights": False})
        self.assertEqual(f[:12], bytes([7, 7, 2, 1, 7, 0, 1, 1, 10, 0, 2, 0]))
        with self.assertRaises(ValueError):
            gmmk.properties_frame(0, 0, 5)

    def test_keymap(self):
        b = {"CapsLock": {"kind": "key", "code": 224},
             "KeyA": {"kind": "key", "code": 6, "mods": ["ctrl", "super"]},
             "F5": {"kind": "media", "code": 1},
             "F6": {"kind": "mouse", "code": 3},
             "F7": {"kind": "function", "code": 2},
             "F8": {"kind": "macro", "slot": 2},
             "KeyQ": {"kind": "disabled"},
             "KeyW": {"kind": "default"}}
        f = gmmk.keymap_frame(ANSI, 1, 2, b, [2])
        self.assertEqual(f[:4], bytes([7, 3, 2, 3]))
        km = f[8:248]
        default = gmmk.default_keymap(ANSI)
        ix = gmmk.KEY_INDEX["ansi"]

        def at(key):
            return km[ix[key]], km[120 + ix[key]]
        self.assertEqual(at("CapsLock"), (2, 224))
        self.assertEqual(at("KeyA"), (0xE0 + 1 + 8, 6))
        self.assertEqual(at("F5"), (3, 1))
        self.assertEqual(at("F6"), (9, 3))
        self.assertEqual(at("F7"), (8, 2))
        self.assertEqual(at("F8"), (5, 2))
        self.assertEqual(at("KeyQ"), (1, 0))
        self.assertEqual(at("KeyW"), (default[ix["KeyW"]], default[120 + ix["KeyW"]]))
        changed = {k for k in ix if at(k) != (default[ix[k]], default[120 + ix[k]])}
        self.assertEqual(changed, {"CapsLock", "KeyA", "F5", "F6", "F7", "F8", "KeyQ"})
        self.assertFalse(any(f[248:]))

    def test_keymap_refuses(self):
        for bad in ({"Fn": {"kind": "disabled"}}, {"Knob": {"kind": "media", "code": 2}},
                    {"Nope": {"kind": "disabled"}}, {"KeyA": {"kind": "key", "code": 3}},
                    {"KeyA": {"kind": "macro", "slot": 4}}, {"KeyA": {"kind": "media", "code": 9}},
                    {"KeyA": {"kind": "key", "code": 225, "mods": ["ctrl"]}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                gmmk.keymap_frame(ANSI, 0, 0, bad, [1])

    def test_alt_board_swaps_slash(self):
        f = gmmk.keymap_frame(ALT, 0, 0, {"Slash": {"kind": "disabled"}})
        self.assertEqual(f[8 + 53], 1)
        self.assertEqual(f[8 + 120 + 53], 0)

    def test_macro(self):
        ev = [{"type": "press", "code": 224, "delay": 300}, {"type": "press", "code": 6, "delay": 20},
              {"type": "release", "code": 6, "delay": 20}, {"type": "release", "code": 224, "delay": 1}]
        f = gmmk.macro_frame(3, ev)
        self.assertEqual(f[:3], bytes([7, 5, 3]))
        self.assertEqual(f[8:20], bytes([0x81, 0x2C, 224, 0x80, 20, 6, 0x00, 20, 6, 0x00, 1, 224]))
        self.assertEqual(gmmk.macro_problem([]), "The macro is empty.")
        self.assertTrue(gmmk.macro_problem(ev * 21))
        with self.assertRaises(ValueError):
            gmmk.macro_frame(0, ev)
        m = gmmk.macro_mode_frame(ANSI, 0, 0, {"F1": {"kind": "macro", "slot": 3}, "F2": {"kind": "macro", "slot": 4}},
                                  {3: "repeat", 4: "toggle"})
        self.assertEqual(m[:4], bytes([7, 4, 1, 1]))
        self.assertEqual(m[8 + 2:8 + 4], b"\xff\xff")
        self.assertEqual(m[8 + 4:8 + 6], b"\x00\x00")

    def test_descriptor_length(self):
        self.assertEqual(gmmk.feature_report_length(SETTINGS_DESC, 7), 264)
        self.assertEqual(gmmk.feature_report_length(SETTINGS_DESC, 4), 0)
        self.assertEqual(gmmk.feature_report_length(EVENTS_DESC, 4), 0)

    def test_layout(self):
        for model in (ANSI, ISO):
            keys = gmmk.layout(model)
            ids = {k["id"] for k in keys} - {"EnterLower"}
            self.assertEqual(ids, set(gmmk.KEY_INDEX[model.keymap]))
            for row in range(6):
                self.assertAlmostEqual(max(k["x"] + k["w"] for k in keys if k["y"] == row), 16.0)

    def test_scancodes(self):
        self.assertEqual(gmmk.usage_from_scancode(30 + 8), 4)        # A
        self.assertEqual(gmmk.usage_from_scancode(29 + 8), 224)      # left Ctrl
        self.assertEqual(gmmk.usage_from_scancode(500), 0)


class FakeDev:
    def __init__(self, path):
        self.sent = FakeDev.log
        self.closed = False

    def set_feature(self, data):
        self.sent.append(bytes(data))

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def test_pads_to_descriptor(self):
        FakeDev.log = []
        board = gmmk.Board("/dev/x", 0x5044, ANSI, "0045", 265)
        t = gmmk.Transport(board, lambda s: None, FakeDev)
        t.send(gmmk.profile_frame(0, 0))
        t.send(gmmk.polling_frame(1000))
        self.assertEqual([len(f) for f in FakeDev.log], [265, 265])
        board = gmmk.Board("/dev/x", 0x5044, ANSI, "0045", 64)
        t = gmmk.Transport(board, lambda s: None, FakeDev)
        t.send(gmmk.profile_frame(0, 0))
        with self.assertRaises(ValueError):            # a keymap doesn't fit a 64-byte report
            t.send(gmmk.keymap_frame(ANSI, 0, 0, {"KeyA": {"kind": "disabled"}}))

    def test_refuses_odd_boards(self):
        for b in (gmmk.Board("", 0x5044, ANSI, "", 0, qmk=True), gmmk.Board("/dev/x", 0x5044, ANSI, "", 0),
                  gmmk.Board("/dev/x", 0x5044, ANSI, "", 4096)):
            with self.assertRaises(ValueError):
                gmmk.Transport(b, lambda s: None, FakeDev)


class Gmmk2Tests(unittest.TestCase):
    """The GMMK 2 (65% and 96%): same protocol as the Pro, its own key positions, drawing and defaults."""
    BOARDS = {pid: gmmk.MODELS[pid] for pid in (0x5045, 0x504A, 0x504B, 0x505A)}

    def test_every_key_types_itself_by_default(self):
        usage = {code: key for key, code in gmmk.PICKER_KEYS}
        usage.update({0x32: "Hashtag", 0x64: "Backslash"})        # ISO's two extra keys
        for model in self.BOARDS.values():
            km = gmmk.default_keymap(model)
            self.assertEqual(len(km), 2 * gmmk.KEY_COUNT)
            for key, i in gmmk.KEY_INDEX[model.keymap].items():
                with self.subTest(model=model.key, key=key):
                    if key == "Fn":
                        self.assertEqual(km[gmmk.KEY_COUNT + i], 0xA0)
                    else:
                        self.assertEqual(usage.get(km[gmmk.KEY_COUNT + i]), key)

    def test_slash_and_backslash_where_they_type(self):
        # CORE's own tables name these two the other way round; the default assignment decides.
        ansi = gmmk.KEY_INDEX["65-ansi"]
        self.assertEqual((ansi["Backslash"], ansi["Slash"]), (53, 91))
        iso = gmmk.KEY_INDEX["96-iso"]
        self.assertEqual((iso["Backslash"], iso["Hashtag"], iso["Slash"]), (81, 72, 91))

    def test_drawings_match_the_keys(self):
        for model in self.BOARDS.values():
            keys = gmmk.layout(model)
            ids = {k["id"] for k in keys} - {"EnterLower"}
            self.assertEqual(ids, set(gmmk.KEY_INDEX[model.keymap]), model.key)
            boxes = [(k["x"], k["y"], k["w"], k["h"]) for k in keys]
            for i, a in enumerate(boxes):           # nothing overlaps
                for b in boxes[i + 1:]:
                    overlap = (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                    self.assertFalse(overlap, (model.key, a, b))
        self.assertEqual(gmmk.layout_size(gmmk.MODELS[0x5045]), (16.0, 5, False))
        self.assertEqual(gmmk.layout_size(gmmk.MODELS[0x505A]), (19.0, 6, True))
        tall = {k["id"]: k["h"] for k in gmmk.layout(gmmk.MODELS[0x504B]) if k["h"] > 1}
        self.assertEqual(tall, {"NumpadAdd": 2.0, "NumpadEnter": 2.0})

    def test_keymap_frame(self):
        m = gmmk.MODELS[0x504B]
        f = gmmk.keymap_frame(m, 1, 2, {"Numpad5": {"kind": "media", "code": 1}})
        self.assertEqual(f[:4], bytes([7, gmmk.CMD_KEYMAP, 2, 3]))
        self.assertEqual((f[8 + 75], f[8 + 120 + 75]), (3, 1))
        rest = bytearray(f[8:8 + 240])
        rest[75], rest[120 + 75] = gmmk.default_keymap(m)[75], gmmk.default_keymap(m)[195]
        self.assertEqual(bytes(rest), gmmk.default_keymap(m))
        with self.assertRaises(ValueError):
            gmmk.keymap_frame(gmmk.MODELS[0x5045], 0, 0, {"F1": {"kind": "disabled"}})   # no F-row on a 65%

    def test_boards_are_locked_and_never_the_bootloader(self):
        for pid, model in self.BOARDS.items():
            self.assertFalse(model.confirmed)
            self.assertFalse(model.commit_keys)
            self.assertNotIn(pid, gmmk.BOOTLOADER_PIDS)
        self.assertTrue({0xB008, 0xB00A, 0xB00B, 0xB00C} <= gmmk.BOOTLOADER_PIDS)

    def test_udev_rule_grants_them_through_the_helper(self):
        rules = (Path(__file__).resolve().parent.parent / "build-aux" / "70-wheelguard.rules").read_text()
        granted = set()
        for vid in (gmmk.VENDOR, gmmk.VENDOR_GMMK3, gmmk.VENDOR_3794):
            line = next(x for x in rules.splitlines() if f'idVendor}}=="{vid:04x}"' in x and "gmmk-check" in x)
            pids = {int(p, 16) for p in line.split('idProduct}=="')[1].split('"')[0].split("|")}
            self.assertEqual(pids, {pid for pid, m in gmmk.MODELS.items() if m.vendor == vid})
            self.assertNotIn("uaccess", line)
            granted |= pids
        self.assertEqual(granted, set(gmmk.MODELS))


class Gmmk3Tests(unittest.TestCase):
    """The GMMK 3 and GMMK 3 Pro (wired): same frames, another vendor, 126 key positions, a standby timer."""
    BOARDS = {pid: m for pid, m in gmmk.MODELS.items()
              if m.vendor == gmmk.VENDOR_GMMK3 and m.key_count == 126 and m.protocol == "gmmk"}

    def test_twelve_boards_all_locked(self):
        self.assertEqual(len(self.BOARDS), 12)
        self.assertEqual(len({m.key for m in self.BOARDS.values()}), 12)
        for m in self.BOARDS.values():
            self.assertFalse(m.confirmed)
            self.assertFalse(m.commit_keys)
            self.assertTrue(m.sleep)
            self.assertEqual(m.key_count, 126)
        self.assertEqual(gmmk.MODELS[0xE3C5].name, "Glorious GMMK 3 Pro 100%")
        self.assertEqual(gmmk.MODELS[0xE3D3].name, "Glorious GMMK 3 65% (ISO)")

    def test_every_key_types_itself_by_default(self):
        usage = {code: key for key, code in gmmk.PICKER_KEYS}
        usage.update({0x32: "Hashtag", 0x64: "Backslash"})
        for model in self.BOARDS.values():
            km = gmmk.default_keymap(model)
            self.assertEqual(len(km), 256)
            for key in {k["id"] for k in gmmk.layout(model)} - {"EnterLower"}:
                i = gmmk.KEY_INDEX[model.keymap][key]
                with self.subTest(model=model.key, key=key):
                    if key == "Fn":
                        self.assertEqual(km[126 + i], 0xA0)
                    elif key == "Knob":
                        self.assertEqual((km[i], km[126 + i]), (3, 2))       # media: play / pause
                    else:
                        self.assertEqual(usage.get(km[126 + i]), key)

    def test_pro_100_ansi_has_no_knob_turn_in_its_default(self):
        a = gmmk.default_keymap(gmmk.MODELS[0xE3C8])
        b = gmmk.default_keymap(gmmk.MODELS[0xE3C5])
        self.assertEqual([i for i in range(256) if a[i] != b[i]], [17, 18, 143, 144])
        self.assertEqual({b[i] for i in (17, 18, 143, 144)}, {0xFF})

    def test_drawings_match_the_keys(self):
        for model in self.BOARDS.values():
            keys = gmmk.layout(model)
            ids = {k["id"] for k in keys} - {"EnterLower"}
            self.assertLessEqual(ids, set(gmmk.KEY_INDEX[model.keymap]), model.key)
            boxes = [(k["x"], k["y"], k["w"], k["h"]) for k in keys]
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    overlap = (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                    self.assertFalse(overlap, (model.key, a, b))
        sizes = {m.key: (gmmk.layout_size(m), len(gmmk.layout(m))) for m in self.BOARDS.values()}
        self.assertEqual(sizes["gmmk3-100-ansi"], ((22.5, 6, True), 105))
        self.assertEqual(sizes["gmmk3-pro-100-iso"], ((22.5, 6, True), 107))     # tall Enter drawn in two
        self.assertEqual(sizes["gmmk3-75-ansi"], ((16.0, 6, True), 81))
        self.assertEqual(sizes["gmmk3-65-ansi"], ((16.0, 5, False), 66))
        sixty = {k["id"] for k in gmmk.layout(gmmk.MODELS[0xE3CA])}
        self.assertNotIn("Delete", sixty)
        self.assertNotIn("ControlRight", sixty)
        self.assertIn("ContextMenu", {k["id"] for k in gmmk.layout(gmmk.MODELS[0xE3C8])})

    def test_keymap_frame_has_126_positions(self):
        m = gmmk.MODELS[0xE3C9]
        f = gmmk.keymap_frame(m, 0, 1, {"End": {"kind": "disabled"}, "Pause": {"kind": "key", "code": 4}})
        self.assertEqual(f[:4], bytes([7, gmmk.CMD_KEYMAP, 1, 2]))
        self.assertEqual(f[8 + 126 + 57], 0)                    # End: disabled
        self.assertEqual((f[8 + 16], f[8 + 126 + 16]), (1, 4))  # Pause isn't drawn on a 75%, but has a place
        rest = bytearray(f[8:8 + 252])
        d = gmmk.default_keymap(m)
        for i in (57, 16):
            rest[i], rest[126 + i] = d[i], d[126 + i]
        self.assertEqual(bytes(rest), d[:252])

    def test_standby_timer_in_both_frames(self):
        self.assertEqual(gmmk.polling_frame(1000, 15)[11], 15)
        self.assertEqual(gmmk.properties_frame(2, 1, 8, sleep=gmmk.SLEEP_NEVER)[11], 255)
        self.assertEqual(gmmk.polling_frame(1000), gmmk.polling_frame(1000, None))
        for bad in (0, 256, -1):
            with self.assertRaises(ValueError):
                gmmk.polling_frame(1000, bad)


_USAGE = {code: key for key, code in gmmk.PICKER_KEYS}
_USAGE.update({0x53: "NumLock", 0x54: "NumpadDivide", 0x55: "NumpadMultiply", 0x56: "NumpadSubtract",
               0x57: "NumpadAdd", 0x58: "NumpadEnter", 0x62: "Numpad0", 0x63: "NumpadDecimal",
               **{0x58 + i: f"Numpad{i}" for i in range(1, 10)}, 0xE4: "ControlRight", 0xE5: "ShiftRight",
               0xE6: "AltRight"})


class Gmmk3WirelessTests(unittest.TestCase):
    """GMMK 3 Pro Wireless over its cable: 110 positions, polling inside the properties frame."""
    BOARDS = {pid: m for pid, m in gmmk.MODELS.items() if m.polling_in_properties}

    def test_six_boards_locked_cable_only(self):
        self.assertEqual(sorted(self.BOARDS), [0xE3CB, 0xE3CC, 0xE3CD, 0xE3D4, 0xE3D5, 0xE3D6])
        for m in self.BOARDS.values():
            self.assertEqual((m.vendor, m.key_count, m.sleep, m.confirmed, m.commit_keys),
                             (gmmk.VENDOR_GMMK3, 110, True, False, False))
        for receiver in (0xE3E6, 0xE3E7, 0xE3E8, 0xE3FB, 0xE3FC, 0xE3FD, 0xE3E9, 0xE3EA, 0xE3EB):
            self.assertNotIn(receiver, gmmk.MODELS)

    def test_every_drawn_key_types_itself(self):
        for m in self.BOARDS.values():
            km, n, index = gmmk.default_keymap(m), m.key_count, gmmk.KEY_INDEX[m.keymap]
            self.assertEqual(len(km), 248)
            for key in {k["id"] for k in gmmk.layout(m)} - {"EnterLower", "Fn", "Knob"}:
                with self.subTest(model=m.key, key=key):
                    want = {"Hashtag": 0x31, "Backslash": 0x64 if m.layout == "iso" else 0x31}.get(key)
                    if want is None:
                        self.assertEqual(_USAGE.get(km[n + index[key]]), key)
                    else:
                        self.assertEqual(km[n + index[key]], want)
            self.assertEqual((km[104], km[n + 104], km[109], km[n + 109]), (3, 3, 3, 4))   # knob turn: volume

    def test_esc_on_the_65_is_where_the_backtick_is(self):
        self.assertEqual(gmmk.KEY_INDEX["g3w-65-ansi"]["Escape"], 1)
        self.assertEqual(gmmk.KEY_INDEX["g3w-ansi"]["Escape"], 0)
        self.assertEqual(gmmk.default_keymap(gmmk.MODELS[0xE3CD])[110 + 1], 0x29)
        self.assertEqual(gmmk.default_keymap(gmmk.MODELS[0xE3CC])[110 + 1], 0x35)

    def test_drawings(self):
        for m in self.BOARDS.values():
            ids = {k["id"] for k in gmmk.layout(m)} - {"EnterLower"}
            self.assertLessEqual(ids, set(gmmk.KEY_INDEX[m.keymap]), m.key)

    def test_keymap_and_properties_frames(self):
        m = gmmk.MODELS[0xE3CC]
        f = gmmk.keymap_frame(m, 0, 0, {"KeyA": {"kind": "disabled"}})
        self.assertEqual((f[8 + 8], f[8 + 110 + 8]), (1, 0))
        self.assertEqual(f[8 + 110 + 13], 0x16)                 # S untouched, at its own place
        p = gmmk.properties_frame(1, 2, 8, sleep=30, polling=250)
        self.assertEqual((p[9], p[10], p[11]), (2, 8, 30))
        with self.assertRaises(ValueError):
            gmmk.properties_frame(0, 0, 8, polling=8000)


class HeTests(unittest.TestCase):
    """GMMK 3 HE / Pro HE: their own 64-byte commands, written key by key."""
    BOARDS = {pid: m for pid, m in gmmk.MODELS.items() if m.protocol == "he"}

    def test_eighteen_boards_three_ids_each_locked(self):
        self.assertEqual(len(self.BOARDS), 54)
        self.assertEqual(len({m.key for m in self.BOARDS.values()}), 18)
        for m in self.BOARDS.values():
            self.assertEqual((m.confirmed, m.macros, m.sleep), (False, False, True))
        self.assertEqual(gmmk.MODELS[0xE3D9].name, "Glorious GMMK 3 Pro HE Wireless 100%")
        self.assertEqual(gmmk.MODELS[0xB319].key, gmmk.MODELS[0xE3DD].key)
        self.assertEqual(gmmk.MODELS[0xB319].vendor, 0x3794)
        for receiver in (0xE3E0, 0xE3E1, 0xE3E2, 0xE3F5, 0xE3F6, 0xE3F7, 0xB314, 0xB316, 0xB318, 0xB31C):
            self.assertNotIn(receiver, gmmk.MODELS)

    def test_every_drawn_key_has_a_number_and_a_default(self):
        for m in self.BOARDS.values():
            numbers = gmmk.KEY_INDEX[m.keymap]
            ids = {k["id"] for k in gmmk.layout(m)} - {"EnterLower"}
            self.assertLessEqual(ids, set(numbers), m.key)
            used = [numbers[k] for k in ids]
            self.assertEqual(len(used), len(set(used)), m.key)      # no two keys share a number
            for k in ids - {"Fn", "Knob"}:
                gmmk.default_usage(m, k)

    def test_frames_and_checksum(self):
        f = gmmk_he.polling_frame(2, 8000)
        self.assertEqual(len(f), 65)
        self.assertEqual(f[:4], bytes([0, 0x04, 2, 0]))
        self.assertEqual(f[8], 255 - (0x04 + 2))
        self.assertEqual(gmmk_he.polling_frame(0, 125)[3], 6)
        p = gmmk_he.profile_frame(1, 2)
        self.assertEqual((p[1], p[2], p[3], p[8]), (0x05, 1, 2, 255 - 8))

    def test_sleep_frame(self):
        wired = gmmk_he.sleep_frame(7, False)
        c = wired[1:]
        self.assertEqual(int.from_bytes(c[16:18], "little"), 420)
        self.assertEqual(int.from_bytes(c[8:10], "little"), 0)
        self.assertEqual({int.from_bytes(c[i:i + 2], "little") for i in (12, 14, 18)}, {0xFFFF})
        c = gmmk_he.sleep_frame(255, True)[1:]
        self.assertEqual([int.from_bytes(c[i:i + 2], "little") for i in (8, 10, 16)], [0, 0, 0])
        c = gmmk_he.sleep_frame(15, True)[1:]
        self.assertEqual([int.from_bytes(c[i:i + 2], "little") for i in (8, 10, 16)], [900] * 3)

    def test_actuation(self):
        c = gmmk_he.actuation_frame(1, 2, {"actuation": 2.0, "rapid": False, "sensitivity": 0.2})[1:]
        self.assertEqual(list(c[:7]), [0x1A, 0x21, 0, 0, 19, 1, 0])
        self.assertEqual((c[8], c[9], c[10], c[25]), (19, 0, 0, 36))
        c = gmmk_he.actuation_frame(0, 0, {"actuation": 0.1, "rapid": True, "sensitivity": 0.3})[1:]
        self.assertEqual((c[3], c[4], c[8], c[9], c[10]), (0x80, 1, 1, 2, 2))
        for bad in ({"actuation": 4.0, "rapid": False, "sensitivity": 0.2},
                    {"actuation": 1.25, "rapid": False, "sensitivity": 0.2},
                    {"actuation": 1.0, "rapid": "yes", "sensitivity": 0.2}):
            with self.assertRaises(ValueError):
                gmmk_he.actuation_frame(0, 0, bad)
        job = gmmk_he.analog_job(0, 1, gmmk_he.DEFAULT_ANALOG)
        self.assertEqual([f[1] for f, _d in job], [0x05, 0x1D, 0x1A])

    def test_keycodes(self):
        self.assertEqual(gmmk_he.keycode({"kind": "key", "code": 4, "mods": ["ctrl"]}), (0, 0xE0, 4, 0))
        self.assertEqual(gmmk_he.keycode({"kind": "key", "code": 0xE1}), (0, 0xE1, 0, 0))
        self.assertEqual(gmmk_he.keycode({"kind": "media", "code": 3}), (3, 0, 0xE9, 0))
        self.assertEqual(gmmk_he.keycode({"kind": "disabled"}), (0, 0, 0, 0))
        with self.assertRaises(ValueError):
            gmmk_he.keycode({"kind": "key", "code": 4, "mods": ["ctrl", "shift"]})
        f = gmmk_he.key_frame(0, 1, 9, (0, 0, 5, 0))[1:]
        self.assertEqual(list(f[:5]), [0x13, 0, 9, 1, 0])
        self.assertEqual(list(f[8:12]), [0, 0, 5, 0])

    def test_dangerous_commands_cannot_be_built_or_sent(self):
        for cmd in (0x02, 0x1C, 0x1E, 0x07, 0x0C):
            with self.assertRaises(ValueError):
                gmmk_he._frame(cmd)
        FakeDev.log = []
        board = gmmk.Board("/dev/x", 0xE3DD, gmmk.MODELS[0xE3DD], "0100", 65)
        t = gmmk.Transport(board, lambda s: None, FakeDev)
        t.send(gmmk_he.profile_frame(0, 0))
        self.assertEqual(len(FakeDev.log[0]), 65)
        for frame in (bytes([0, 0x02]) + bytes(63), gmmk.profile_frame(0, 0)):
            with self.assertRaises(ValueError):
                t.send(frame)

    def test_events(self):
        self.assertEqual(gmmk_he.parse_event(bytes([5, 1, 2, 0])), (2, 0))
        self.assertIsNone(gmmk_he.parse_event(bytes([4, 0xF1, 1, 1])))


class NumpadTests(unittest.TestCase):
    M = gmmk.MODELS[0x5088]

    def test_model(self):
        self.assertEqual((self.M.key_count, self.M.macros, self.M.sleep, self.M.confirmed, self.M.commit_keys),
                         (20, False, False, False, True))

    def test_every_key_types_itself(self):
        km, index = gmmk.default_keymap(self.M), gmmk.KEY_INDEX["numpad"]
        self.assertEqual(len(km), 40)
        for key in {k["id"] for k in gmmk.layout(self.M)} - {"Knob"}:
            self.assertEqual(_USAGE.get(km[20 + index[key]]), key, key)
        self.assertEqual((km[11], km[31]), (3, 2))              # knob press: mute
        self.assertEqual(gmmk.layout_size(self.M), (5.5, 5, False))

    def test_no_macros(self):
        with self.assertRaises(ValueError):
            gmmk.keymap_frame(self.M, 0, 0, {"Numpad1": {"kind": "macro", "slot": 1}}, [1])
        f = gmmk.keymap_frame(self.M, 0, 0, {"Numpad1": {"kind": "media", "code": 1}})
        self.assertEqual((f[8 + 12], f[8 + 20 + 12]), (3, 1))
        self.assertEqual(f[8 + 40:], bytes(len(f) - 48))


class DiscoveryTests(unittest.TestCase):
    def make_node(self, root, name, pid, desc, iface, bcd="0045", vid=0x320F):
        usb = Path(root) / "usb" / f"1-1:{pid:04x}"
        hid = usb / f"1-1:1.{iface}" / f"0003:{vid:04X}:{pid:04X}.000{iface}"
        hid.mkdir(parents=True, exist_ok=True)
        (usb / "bcdDevice").write_text(bcd + "\n")
        (hid / "uevent").write_text(f"HID_ID=0003:0000{vid:04X}:0000{pid:04X}\n")
        (hid / "report_descriptor").write_bytes(desc)
        node = Path(root) / "class" / name
        node.mkdir(parents=True)
        (node / "device").symlink_to(hid)

    def test_finds_both_interfaces(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_node(root, "hidraw3", 0x5044, KEYBOARD_DESC, 0)
            self.make_node(root, "hidraw4", 0x5044, SETTINGS_DESC, 1)
            self.make_node(root, "hidraw5", 0x5044, EVENTS_DESC, 2)
            boards = gmmk.discover(os.path.join(root, "class"), "/dev")
        self.assertEqual(len(boards), 1)
        b = boards[0]
        self.assertEqual((b.path, b.event_path, b.report_len, b.firmware, b.qmk, b.model.key),
                         ("/dev/hidraw4", "/dev/hidraw5", 264, "0045", False, "gmmk-pro-ansi"))

    def test_finds_a_gmmk2(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_node(root, "hidraw4", 0x505A, SETTINGS_DESC, 1)
            self.make_node(root, "hidraw5", 0x505A, EVENTS_DESC, 2)
            self.make_node(root, "hidraw6", 0xB00C, SETTINGS_DESC, 1)          # a board in its bootloader
            boards = gmmk.discover(os.path.join(root, "class"), "/dev")
        self.assertEqual([b.model.key for b in boards], ["gmmk2-96-iso"])

    def test_finds_a_gmmk3_only_under_its_own_vendor(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_node(root, "hidraw4", 0xE3C6, SETTINGS_DESC, 1, vid=0x342D)
            self.make_node(root, "hidraw5", 0xE3C6, EVENTS_DESC, 2, vid=0x342D)
            self.make_node(root, "hidraw7", 0xE3C8, SETTINGS_DESC, 1)             # right number, wrong vendor
            boards = gmmk.discover(os.path.join(root, "class"), "/dev")
        self.assertEqual([(b.model.key, b.path) for b in boards], [("gmmk3-pro-75-ansi", "/dev/hidraw4")])

    def test_finds_a_gmmk3_he_by_its_own_collections(self):
        he_set = bytes.fromhex("06ffff0902a101" "7508" "9540" "0900" "b102" "c0")      # no report IDs: 64 bytes
        he_events = bytes.fromhex("06ffff0901a101" "7508" "9540" "0900" "8102" "c0")
        with tempfile.TemporaryDirectory() as root:
            self.make_node(root, "hidraw2", 0xB319, he_set, 1, vid=0x3794)
            self.make_node(root, "hidraw3", 0xB319, he_events, 2, vid=0x3794)
            self.make_node(root, "hidraw4", 0xB319, SETTINGS_DESC, 3, vid=0x3794)     # the GMMK page: not this board's
            boards = gmmk.discover(os.path.join(root, "class"), "/dev")
        self.assertEqual([(b.model.key, b.path, b.event_path, b.report_len) for b in boards],
                         [("gmmk3-he-65-ansi", "/dev/hidraw2", "/dev/hidraw3", 65)])

    def test_qmk_board(self):
        qmk = bytes.fromhex("0660ff0961a101" "0962" "8102" "c0")
        with tempfile.TemporaryDirectory() as root:
            self.make_node(root, "hidraw1", 0x5044, qmk, 1)
            boards = gmmk.discover(os.path.join(root, "class"), "/dev")
        self.assertTrue(boards[0].qmk)


class UdevHelperTests(unittest.TestCase):
    def test_vendor_only(self):
        h = load_helper()
        self.assertTrue(h.vendor_only(SETTINGS_DESC))
        self.assertTrue(h.vendor_only(EVENTS_DESC))
        self.assertFalse(h.vendor_only(KEYBOARD_DESC))
        self.assertFalse(h.vendor_only(KEYBOARD_DESC + SETTINGS_DESC))     # never a node that also types
        self.assertFalse(h.vendor_only(b""))


try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui.keyboard import KeyboardController, macro_text
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class FakeRGBeast:
    """Stands in for RGBeast's daemon: `dev` is its id for the board, None when it doesn't manage it."""

    def __init__(self, dev):
        self.dev, self.error, self.calls = dev, "", []

    def check(self, vendor, product, done):
        done(self.dev)

    def managed(self, vendor, product):
        return self.dev

    def set_options(self, dev_id, profile, layer, debounce_ms):
        self.calls.append((dev_id, profile, layer, debounce_ms))
        return self.error


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self, board=None, rgbeast=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "keyboard.json"
        FakeDev.log = []
        self.board = board or gmmk.Board("/dev/hidraw9", 0x5044, ANSI, "0045", 264)
        c = KeyboardController(store_path=self.path, discover=lambda: [self.board], opener=FakeDev,
                               sleep=lambda s: None, rgbeast=rgbeast or FakeRGBeast(None), poll=False, can_open=lambda p: True)
        self.addCleanup(c.shutdown)
        return c

    def settle(self, c):
        loop = QEventLoop()
        QTimer.singleShot(3000, loop.quit)
        c.changed.connect(lambda: loop.quit() if not c.busy else None)
        if c.busy:
            loop.exec()
        self.assertFalse(c.busy)

    def test_locked_until_turned_on(self):
        c = self.make()
        self.assertEqual(c.state, "ready")
        self.assertTrue(c.locked)
        c.setPolling(0)
        self.assertEqual(FakeDev.log, [])
        c.unlock()
        self.assertFalse(c.locked)
        c.setPolling(0)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.polling_frame(125)])
        self.assertEqual(c.pollingIndex, 0)
        self.assertEqual(json.loads(self.path.read_text())["boards"]["gmmk-pro-ansi"]["polling"], 125)

    def test_debounce_without_rgbeast_keeps_the_default_lighting(self):
        c = self.make()
        c.unlock()
        c.setDebounce(1)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.properties_frame(0, layer, 8) for layer in range(3)])
        self.assertFalse(c.rgbeastLighting)

    def test_debounce_goes_through_rgbeast_while_it_manages_the_board(self):
        rgb = FakeRGBeast("gmmk:320f:5044:x")
        c = self.make(rgbeast=rgb)
        self.assertTrue(c.rgbeastLighting)
        self.assertEqual(rgb.calls, [], "nothing is sent while Wheelguard is locked")
        c.unlock()
        c.rescan()              # RGBeast seen again after unlocking: nothing new to tell it
        self.settle(c)
        c.setDebounce(1)
        self.settle(c)
        self.assertEqual(FakeDev.log, [], "RGBeast writes the frame, with its lighting")
        self.assertEqual(rgb.calls[-1], ("gmmk:320f:5044:x", 0, 0, 8))
        self.assertEqual(c.debounceIndex, 1)

        rgb.error = "RGBeast couldn't change the keyboard: denied"
        c.setDebounce(0)
        self.settle(c)
        self.assertEqual(c.debounceIndex, 1, "a refused change isn't remembered")
        self.assertIn("denied", c.message)

        rgb.error = ""
        c.setLayer(2)
        self.settle(c)
        self.assertEqual(rgb.calls[-1], ("gmmk:320f:5044:x", 0, 2, 8))

    def test_rgbeast_turned_on_later_is_told_the_settings(self):
        rgb = FakeRGBeast(None)
        c = self.make(rgbeast=rgb)
        c.unlock()
        rgb.dev = "gmmk:320f:5044:x"
        c.rescan()
        self.settle(c)
        self.assertTrue(c.rgbeastLighting)
        self.assertEqual(rgb.calls, [("gmmk:320f:5044:x", 0, 0, gmmk.DEFAULT_DEBOUNCE)])
        rgb.dev = None
        c.rescan()
        self.assertFalse(c.rgbeastLighting)

    def test_keys_and_macros(self):
        c = self.make()
        c.unlock()
        c.bindKey("F5", "media", 1, "")
        self.settle(c)
        self.assertEqual(FakeDev.log[0], gmmk.keymap_frame(ANSI, 0, 0, {"F5": {"kind": "media", "code": 1}}))
        self.assertEqual(FakeDev.log[1:], [gmmk.profile_frame(0, 0).ljust(264, b"\0")] * 2)     # padded to the report
        self.assertEqual(c.remappedCount, 1)
        f5 = next(k for k in c.keys if k["id"] == "F5")
        self.assertEqual((f5["action"], f5["remapped"]), ("Play / Pause", True))

        ev = [{"type": "press", "code": 4, "delay": 10}, {"type": "release", "code": 4, "delay": 10}]
        slot = c.saveMacro(0, "Say a", "once", json.dumps(ev))
        self.assertEqual(slot, 1)
        FakeDev.log = []
        c.bindKey("F6", "macro", slot, "")
        self.settle(c)
        self.assertEqual(FakeDev.log[0][1], gmmk.CMD_MACRO_MODE)
        self.assertEqual(FakeDev.log[1], gmmk.macro_frame(1, ev))
        self.assertEqual(FakeDev.log[2][1], gmmk.CMD_KEYMAP)
        self.assertEqual(c.macros[0]["used"], True)

        FakeDev.log = []
        c.deleteMacro(1)
        self.settle(c)
        self.assertEqual(c.macros, [])
        self.assertNotIn("F6", {k["id"] for k in c.keys if k["remapped"]})
        self.assertEqual(FakeDev.log[0], gmmk.keymap_frame(ANSI, 0, 0, {"F5": {"kind": "media", "code": 1}}))

        c.resetLayer()
        self.settle(c)
        self.assertEqual(c.remappedCount, 0)
        self.assertEqual(c.message, "Every key on this layer is back to normal.")

    def test_gmmk2_key_change_has_no_commit(self):
        c = self.make(board=gmmk.Board("/dev/hidraw9", 0x5045, gmmk.MODELS[0x5045], "0101", 264))
        self.assertEqual((c.shortName, c.hasKnob, c.layoutSize), ("GMMK 2 65%", False, [16.0, 5, False]))
        self.assertTrue(c.locked)
        c.unlock()
        c.bindKey("Delete", "key", 74, "")              # Delete types Home
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.keymap_frame(gmmk.MODELS[0x5045], 0, 0,
                                                         {"Delete": {"kind": "key", "code": 74}})])
        c.bindKey("F1", "disabled", 0, "")              # not on this board: ignored
        self.assertEqual(len(FakeDev.log), 1)

    def test_gmmk3_standby(self):
        m = gmmk.MODELS[0xE3CA]
        c = self.make(board=gmmk.Board("/dev/hidraw9", 0xE3CA, m, "0100", 264))
        self.assertEqual((c.shortName, c.hasKnob, c.hasSleep, c.sleepIndex, c.rgbeastSupports),
                         ("GMMK 3 65%", True, True, 1, False))
        c.setSleep(2)
        self.assertEqual(FakeDev.log, [], "locked")
        c.unlock()
        c.setSleep(2)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.polling_frame(1000, 15)]
                         + [gmmk.properties_frame(p, layer, gmmk.DEFAULT_DEBOUNCE, sleep=15)
                            for p in range(3) for layer in range(3)])
        self.assertEqual(c.sleepIndex, 2)
        FakeDev.log = []
        c.setPolling(0)
        c.setDebounce(0)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.polling_frame(125, 15)]
                         + [gmmk.properties_frame(0, layer, 2, sleep=15) for layer in range(3)])
        stored = json.loads(self.path.read_text())["boards"]["gmmk3-65-ansi"]
        self.assertEqual(stored["sleep"], 15)

    def test_gmmk3_wireless_polling_rides_in_properties(self):
        m = gmmk.MODELS[0xE3CB]
        c = self.make(board=gmmk.Board("/dev/hidraw9", 0xE3CB, m, "0100", 264))
        c.unlock()
        c.setPolling(1)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.properties_frame(0, 0, 16, sleep=7, polling=250)])
        FakeDev.log = []
        c.setDebounce(1)
        self.settle(c)
        c.setSleep(0)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk.properties_frame(0, layer, 8, sleep=7, polling=250) for layer in range(3)]
                         + [gmmk.properties_frame(p, layer, 8 if p == 0 else 16, sleep=3, polling=250)
                            for p in range(3) for layer in range(3)])
        self.assertTrue(all(f[1] != gmmk.CMD_POLLING for f in FakeDev.log))

    def test_he_board(self):
        m = gmmk.MODELS[0xE3DE]
        c = self.make(board=gmmk.Board("/dev/hidraw9", 0xE3DE, m, "0100", 65))
        self.assertEqual((c.hasAnalog, c.hasDebounce, c.hasMacros, c.hasSleep), (True, False, False, True))
        self.assertEqual([x["label"] for x in c.pollingChoices],
                         ["125 Hz", "250 Hz", "500 Hz", "1000 Hz", "2K Hz", "4K Hz", "8K Hz"])
        self.assertEqual(c.pollingIndex, 3)
        c.unlock()
        c.setPolling(6)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk_he.polling_frame(p, 8000) for p in range(3)])
        FakeDev.log = []
        c.setAnalog(1.2, True, 0.1)
        self.settle(c)
        a = {"actuation": 1.2, "rapid": True, "sensitivity": 0.1}
        self.assertEqual(FakeDev.log, [f for f, _d in gmmk_he.analog_job(0, 0, a)])
        self.assertEqual((c.actuation, c.rapidTrigger, c.sensitivity), (1.2, True, 0.1))
        FakeDev.log = []
        c.setLayer(1)
        self.settle(c)
        self.assertEqual(FakeDev.log, [f for f, _d in gmmk_he.analog_job(0, 1, a)])
        FakeDev.log = []
        c.bindKey("KeyA", "key", 5, "")
        self.settle(c)
        number = gmmk.KEY_INDEX[m.keymap]["KeyA"]
        self.assertEqual(FakeDev.log, [gmmk_he.key_frame(0, 1, number, (0, 0, 5, 0))])
        FakeDev.log = []
        c.resetLayer()
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk_he.key_frame(0, 1, number, (0, 0, 4, 0))])
        FakeDev.log = []
        c.setSleep(4)
        self.settle(c)
        self.assertEqual(FakeDev.log, [gmmk_he.sleep_frame(255, False)])
        FakeDev.log = []
        c.setDebounce(0)
        self.assertEqual(FakeDev.log, [])
        stored = json.loads(self.path.read_text())["boards"]["gmmk3-he-75-ansi"]
        self.assertEqual((stored["polling"], stored["analog"]["actuation"]), (8000, 1.2))

    def test_numpad_has_no_macros(self):
        c = self.make(board=gmmk.Board("/dev/hidraw9", 0x5088, gmmk.MODELS[0x5088], "0100", 264))
        self.assertEqual((c.hasMacros, c.hasKnob, c.hasSleep), (False, True, False))
        c.unlock()
        c.bindKey("Numpad1", "macro", 1, "")
        self.assertEqual(FakeDev.log, [])
        c.bindKey("Numpad1", "disabled", 0, "")
        self.settle(c)
        f = gmmk.keymap_frame(gmmk.MODELS[0x5088], 0, 0, {"Numpad1": {"kind": "disabled"}})
        self.assertEqual(FakeDev.log[0], f)
        self.assertEqual(FakeDev.log[1][1], gmmk.CMD_PROFILE)    # the Numpad commits, like CORE

    def test_pro_has_no_standby_setting(self):
        c = self.make()
        self.assertFalse(c.hasSleep)
        self.assertTrue(c.rgbeastSupports)
        c.unlock()
        c.setSleep(0)
        self.assertEqual(FakeDev.log, [])

    def test_never_remaps_fn(self):
        c = self.make()
        c.unlock()
        c.bindKey("Fn", "disabled", 0, "")
        c.bindKey("Knob", "disabled", 0, "")
        self.assertEqual(FakeDev.log, [])

    def test_write_failure_keeps_old_value(self):
        class Broken(FakeDev):
            def set_feature(self, data):
                raise OSError(5, "I/O error")
        c = self.make()
        c._worker._opener = Broken
        c.unlock()
        c.setPolling(1)
        self.settle(c)
        self.assertEqual(c.pollingIndex, 3)
        self.assertEqual(c.message, "The keyboard didn't take the change.")

    def test_states(self):
        c = self.make(gmmk.Board("", 0x5044, ANSI, "0045", 0, qmk=True))
        self.assertEqual(c.state, "qmk")
        c = self.make(gmmk.Board("/dev/x", 0x5044, ANSI, "0045", 12))
        self.assertEqual(c.state, "odd")
        self.assertTrue(c.known)

    def test_board_switch_event(self):
        from wheelguard.gui.keyboard import _Events
        self.assertEqual(_Events.parse(bytes([4, 241, 2, 3])), (1, 2))
        self.assertIsNone(_Events.parse(bytes([4, 250, 1, 0])))
        self.assertIsNone(_Events.parse(bytes([4, 241, 9, 1])))

    def test_macro_text(self):
        ev = []
        for code in (6, 25):
            ev += [{"type": "press", "code": 224}, {"type": "press", "code": code},
                   {"type": "release", "code": code}, {"type": "release", "code": 224}]
        self.assertEqual(macro_text(ev), "Ctrl+C, Ctrl+V")


if __name__ == "__main__":
    unittest.main()
