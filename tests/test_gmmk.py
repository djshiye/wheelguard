"""Glorious GMMK Pro: frames (checked byte by byte against what Glorious CORE 2.1.23 builds), the report
descriptor walk, discovery from a fake sysfs, the udev helper, and the Keyboard page's controller."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard.hw import gmmk

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


class DiscoveryTests(unittest.TestCase):
    def make_node(self, root, name, pid, desc, iface, bcd="0045"):
        usb = Path(root) / "usb" / f"1-1:{pid:04x}"
        hid = usb / f"1-1:1.{iface}" / f"0003:320F:{pid:04X}.000{iface}"
        hid.mkdir(parents=True, exist_ok=True)
        (usb / "bcdDevice").write_text(bcd + "\n")
        (hid / "uevent").write_text(f"HID_ID=0003:0000320F:0000{pid:04X}\n")
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
