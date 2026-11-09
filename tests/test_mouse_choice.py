import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard import daemon, scan
from wheelguard.control import Controller
from wheelguard.gui import protocol
from wheelguard.ipc import Server


class FakeInput:
    def __init__(self, name, vendor, product, wheel=True, buttons=True, letters=False):
        self.name = name
        self.info = SimpleNamespace(vendor=vendor, product=product)
        self._caps = {}
        if wheel:
            self._caps[e.EV_REL] = [e.REL_X, e.REL_Y, e.REL_WHEEL]
        if buttons:
            self._caps[e.EV_KEY] = [e.BTN_LEFT, e.BTN_RIGHT]
        if letters:
            self._caps.setdefault(e.EV_KEY, []).extend([e.KEY_A, e.KEY_Z])
        self.closed = False

    def capabilities(self):
        return self._caps

    def close(self):
        self.closed = True


class World:
    """Pretend /dev/input: path -> FakeInput | 'denied' | 'gone'."""

    def __init__(self, nodes, sysfs=None):
        self.nodes, self.sysfs = nodes, sysfs or {}
        self.opened = []

    def glob(self, _pattern):
        return list(self.nodes)

    def open(self, path):
        n = self.nodes[path]
        if n == "denied":
            raise PermissionError(13, "denied", path)
        if n == "gone":
            raise OSError(19, "gone")
        self.opened.append(n)
        return n

    def __enter__(self):
        self.patches = [mock.patch.object(daemon.glob, "glob", self.glob),
                        mock.patch.object(scan.glob, "glob", self.glob),
                        mock.patch.object(daemon.evdev, "InputDevice", self.open),
                        mock.patch.object(scan.evdev, "InputDevice", self.open),
                        mock.patch.object(daemon, "_sysfs_is_wheel_mouse",
                                          lambda p, v, pr: self.sysfs.get(p) == (v, pr)),
                        mock.patch.object(scan, "_sysfs_info", lambda p: self.sysfs.get(p) and (
                            "Hidden Mouse", *self.sysfs[p]))]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.patches:
            p.stop()


SUPERLIGHT = ("Logitech USB Receiver Mouse", 0x046D, 0x4093)
ENDGAME = ("Endgame Gear Endgame Gear HS Dongle", 0x3367, 0x1970)
ACME = ("Acme Optical Mouse", 0x1111, 0x2222)


class Config(unittest.TestCase):
    def test_default_and_validation(self):
        self.assertEqual(C.DEFAULTS["mouse"], "auto")
        self.assertEqual(C.normalize({"mouse": "3367:1970"})["mouse"], "3367:1970")
        self.assertEqual(C.normalize({"mouse": "046D:C094"})["mouse"], "046d:c094")       # stored in lower case
        self.assertEqual(C.normalize({"mouse": "auto"})["mouse"], "auto")
        for bad in ("", "AUTO", "3367", "3367:", "33670:1970", "zzzz:1970", "3367-1970", " 3367:1970", 3367,
                    None, True, ["3367:1970"], "3367:1970\n"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                C.normalize({"mouse": bad})

    def test_old_files_and_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"vendor": "3367", "product": "1970"}))
            cfg = C.load(p)
            self.assertEqual(cfg["mouse"], "auto")
            cfg["mouse"] = "1111:2222"
            C.save(cfg, p)
            self.assertEqual(C.load(p)["mouse"], "1111:2222")


class Discover(unittest.TestCase):
    def tearDown(self):
        scan.set_selected("auto")

    def test_auto_keeps_todays_behaviour(self):
        w = World({"/dev/input/event1": FakeInput(*ACME), "/dev/input/event2": FakeInput(*SUPERLIGHT),
                   "/dev/input/event3": FakeInput(*ENDGAME)})
        with w:
            dev, err = daemon.discover(0x3367, 0x1970, "auto")
            self.assertEqual((dev.info.vendor, dev.info.product, err), (0x3367, 0x1970, None))
            dev, err = daemon.discover(0x046D, 0x4093, "auto")           # configured one first
            self.assertEqual(dev.info.vendor, 0x046D)
        w = World({"/dev/input/event1": FakeInput(*ACME)})
        with w:
            self.assertEqual(daemon.discover(0x3367, 0x1970, "auto"), (None, "mouse not found"))

    def test_any_mouse_can_be_picked(self):
        w = World({"/dev/input/event1": FakeInput(*ACME), "/dev/input/event2": FakeInput(*ENDGAME)})
        with w:
            dev, err = daemon.discover(0x3367, 0x1970, "1111:2222")
            self.assertEqual((dev.name, err), ("Acme Optical Mouse", None))

    def test_picked_mouse_does_not_fall_back(self):
        w = World({"/dev/input/event2": FakeInput(*ENDGAME)})
        with w:
            self.assertEqual(daemon.discover(0x3367, 0x1970, "1111:2222"), (None, "selected mouse not found"))

    def test_default_follows_the_config_setting(self):
        w = World({"/dev/input/event1": FakeInput(*ACME), "/dev/input/event2": FakeInput(*ENDGAME)})
        with w:
            scan.set_selected("1111:2222")
            self.assertEqual(daemon.discover(0x3367, 0x1970)[0].name, "Acme Optical Mouse")
            scan.set_selected("auto")
            self.assertEqual(daemon.discover(0x3367, 0x1970)[0].name, ENDGAME[0])
            scan.set_selected("garbage")                              # never trusted: back to automatic
            self.assertEqual(scan.selected(), "auto")

    def test_keyboard_and_consumer_nodes_and_our_own_device_are_skipped(self):
        w = World({"/dev/input/event1": FakeInput("Acme Keyboard", 0x1111, 0x2222),
                   "/dev/input/event2": FakeInput("Acme Consumer Control", 0x1111, 0x2222),
                   "/dev/input/event3": FakeInput("wheelguard Endgame Gear", 0x1111, 0x2222),
                   "/dev/input/event4": FakeInput("No wheel", 0x1111, 0x2222, wheel=False)})
        with w:
            self.assertEqual(daemon.discover(0x3367, 0x1970, "1111:2222"), (None, "selected mouse not found"))

    def test_nodes_with_keyboard_letter_keys_are_never_picked(self):
        w = World({"/dev/input/event1": FakeInput("Acme Combo", 0x1111, 0x2222, letters=True)})
        with w:
            self.assertEqual(daemon.discover(0x3367, 0x1970, "1111:2222"), (None, "selected mouse not found"))
        w = World({"/dev/input/event1": FakeInput("Acme Combo", 0x1111, 0x2222, letters=True),
                   "/dev/input/event2": FakeInput("Acme Optical Mouse", 0x1111, 0x2222)})
        with w:
            self.assertEqual(daemon.discover(0x3367, 0x1970, "1111:2222")[0].name, "Acme Optical Mouse")

    def test_permission_error_for_a_picked_mouse_is_clear(self):
        w = World({"/dev/input/event7": "denied", "/dev/input/event8": FakeInput(*ENDGAME)},
                  sysfs={"/dev/input/event7": (0x1111, 0x2222)})
        with w:
            dev, err = daemon.discover(0x3367, 0x1970, "1111:2222")
            self.assertIsNone(dev)
            self.assertEqual(err, "permission denied: /dev/input/event7")
            self.assertEqual(daemon.describe(PermissionError(13, "x", "/dev/input/event7")),
                             "permission denied: /dev/input/event7")

    def test_unopenable_other_mice_do_not_matter(self):
        w = World({"/dev/input/event7": "denied", "/dev/input/event8": FakeInput(*ENDGAME)},
                  sysfs={"/dev/input/event7": (0x1111, 0x2222)})
        with w:
            dev, err = daemon.discover(0x3367, 0x1970, "3367:1970")
            self.assertIsNotNone(dev)
            self.assertIsNone(err)


class Listing(unittest.TestCase):
    def test_lists_connected_wheel_mice_once_each_supported_first(self):
        w = World({"/dev/input/event1": FakeInput(*ACME),
                   "/dev/input/event2": FakeInput(*SUPERLIGHT),
                   "/dev/input/event3": FakeInput("Logitech USB Receiver Keyboard", 0x046D, 0x4093),
                   "/dev/input/event4": FakeInput("Logitech USB Receiver Consumer Control", 0x046D, 0x4093),
                   "/dev/input/event5": FakeInput("wheelguard Endgame Gear", 0x3367, 0x1970),
                   "/dev/input/event6": FakeInput("Pen", 0x2222, 0x3333, wheel=False),
                   "/dev/input/event7": "gone",
                   "/dev/input/event8": FakeInput(*ENDGAME),
                   "/dev/input/event9": FakeInput(*ENDGAME)})
        with w:
            found = scan.list_mice()
        self.assertEqual([(m["vendor"], m["product"], m["supported"]) for m in found],
                         [("3367", "1970", True), ("046d", "4093", True), ("1111", "2222", False)])
        self.assertEqual(found[0]["name"], "Endgame Gear XM2w 4k v2")
        self.assertEqual(found[0]["connection"], "dongle")
        self.assertEqual(found[0]["device"], "Endgame Gear HS Dongle")
        self.assertEqual(found[2]["name"], "Acme Optical Mouse")
        self.assertIsNone(found[2]["connection"])
        self.assertTrue(all(n.closed for n in w.opened))

    def test_unopenable_mouse_is_listed_from_sysfs(self):
        w = World({"/dev/input/event7": "denied"}, sysfs={"/dev/input/event7": (0x046D, 0xC094)})
        with w:
            found = scan.list_mice()
        self.assertEqual([(m["vendor"], m["product"], m["supported"]) for m in found], [("046d", "c094", True)])
        self.assertFalse(found[0]["accessible"])

    def test_accessible_flag(self):
        w = World({"/dev/input/event1": FakeInput(*ACME), "/dev/input/event7": "denied",
                   "/dev/input/event8": FakeInput(*SUPERLIGHT), "/dev/input/event9": "denied"},
                  sysfs={"/dev/input/event7": (0x3367, 0x1970), "/dev/input/event9": (0x046D, 0x4093)})
        with w:
            found = {m["vendor"]: m["accessible"] for m in scan.list_mice()}
        self.assertEqual(found, {"3367": False, "046d": True, "1111": True})       # one openable node is enough

    def test_combo_keyboard_nodes_are_not_listed(self):
        w = World({"/dev/input/event1": FakeInput("Acme Combo", 0x1111, 0x2222, letters=True)})
        with w:
            self.assertEqual(scan.list_mice(), [])

    def test_nothing_connected(self):
        with World({}):
            self.assertEqual(scan.list_mice(), [])


class ControllerSide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 100.0
        self.scans = 0
        self.mice = [{"name": "Acme", "device": "Acme", "vendor": "1111", "product": "2222",
                      "supported": False, "connection": None}]
        self.events = []
        self.ctl = Controller(config_path=Path(self.tmp.name) / "c.json", publish=self.events.append,
                              clock=lambda: self.now, mouse_lister=self.lister)
        self.addCleanup(scan.set_selected, "auto")

    def lister(self):
        self.scans += 1
        return list(self.mice)

    def test_status_carries_the_list_and_scans_are_rate_limited(self):
        self.assertEqual(self.ctl.status()["mice"], [])             # nothing scanned yet
        srv = Server(self.ctl, path=str(Path(self.tmp.name) / "s"))
        st = srv.dispatch(None, {"cmd": "status"})
        self.assertEqual(st["mice"], self.mice)
        srv.dispatch(None, {"cmd": "status"})
        reply = srv.dispatch(None, {"cmd": "mice"})
        self.assertEqual(reply, {"type": "mice", "mice": self.mice})
        self.assertEqual(self.scans, 1)
        self.now += 6
        srv.dispatch(None, {"cmd": "mice"})
        self.assertEqual(self.scans, 1)                              # reused for 30 s
        self.now += 31
        srv.dispatch(None, {"cmd": "mice"})
        self.assertEqual(self.scans, 2)

    def test_hotplug_ends_the_cache_but_never_scans_more_than_every_few_seconds(self):
        srv = Server(self.ctl, path=str(Path(self.tmp.name) / "s"))
        srv.dispatch(None, {"cmd": "mice"})
        self.now += 1
        self.ctl.invalidate_mice()
        srv.dispatch(None, {"cmd": "mice"})
        self.assertEqual(self.scans, 1)                              # too soon after the last scan
        self.now += 3
        srv.dispatch(None, {"cmd": "mice"})
        self.assertEqual(self.scans, 2)
        srv.dispatch(None, {"cmd": "mice"})
        self.assertEqual(self.scans, 2)                              # fresh again

    def test_status_does_not_scan_when_the_list_is_fresh(self):
        srv = Server(self.ctl, path=str(Path(self.tmp.name) / "s"))
        for _ in range(5):
            self.now += 6
            srv.dispatch(None, {"cmd": "status"})
        self.assertEqual(self.scans, 1)

    def test_no_lister_means_an_empty_list(self):
        ctl = Controller(config_path=Path(self.tmp.name) / "x.json")
        self.assertEqual(ctl.refresh_mice(), [])

    def test_a_failing_scan_keeps_the_old_list(self):
        self.ctl.refresh_mice()
        self.ctl.mouse_lister = mock.Mock(side_effect=OSError("boom"))
        self.now += 60
        self.assertEqual(self.ctl.refresh_mice(), self.mice)

    def test_choosing_a_mouse_while_connected_asks_the_session_to_restart(self):
        self.assertIsNone(self.ctl.set_config({"mouse": "1111:2222"}))
        self.assertFalse(self.ctl.restart_requested)                # nothing running: it is found on the next search
        self.assertEqual(scan.selected(), "1111:2222")
        self.ctl.set_device(True, "Endgame", None, ident=(0x3367, 0x1970))
        self.assertIsNone(self.ctl.set_config({"mouse": "auto"}))
        self.assertTrue(self.ctl.restart_requested)
        self.assertEqual(scan.selected(), "auto")

    def test_unrelated_changes_do_not_restart(self):
        self.ctl.set_device(True, "Endgame", None)
        self.ctl.set_config({"lock_ms": 400})
        self.assertFalse(self.ctl.restart_requested)

    def test_session_ends_when_the_choice_changes(self):
        from tests.test_daemon import FakeDevice, FakeUInput
        dev = FakeDevice(held_polls=0)
        dev.name, dev.path = "fake", "/dev/input/event9"
        dev.ungrab = dev.close = lambda: None
        dev.read = lambda: []
        self.ctl.set_device(True, "fake", None)
        calls = []

        def service(ctl, server, timeout, extra=()):
            calls.append(1)
            if len(calls) == 2:
                ctl.restart_requested = True        # as set_config would
            return []
        ui = FakeUInput()
        with mock.patch.object(daemon.UInput, "from_device", return_value=ui), \
                mock.patch.object(daemon, "service", service):
            switched = daemon.run_session(dev, self.ctl, server=None, tick_log=lambda d: None)
        self.assertTrue(switched)                    # returns instead of looping on
        self.assertFalse(self.ctl.restart_requested)
        self.assertFalse(self.ctl.connected)
        self.assertEqual(ui.log[-1], "close")


class GuiOptions(unittest.TestCase):
    MICE = [{"name": "Endgame Gear XM2w 4k v2", "vendor": "3367", "product": "1970", "supported": True,
             "connection": "dongle", "device": "Endgame Gear HS Dongle"},
            {"name": "Acme Optical Mouse", "vendor": "1111", "product": "2222", "supported": False,
             "connection": None, "device": "Acme Optical Mouse"}]

    def test_inaccessible_mice_are_marked_and_detected(self):
        mice = [dict(self.MICE[0], accessible=True), dict(self.MICE[1], accessible=False)]
        opts = protocol.mouse_options(mice, "auto")
        self.assertEqual(opts[1]["label"], "Endgame Gear XM2w 4k v2 \u00b7 dongle")
        self.assertEqual(opts[2]["label"], "Acme Optical Mouse (no access)")
        self.assertTrue(protocol.mouse_no_access(mice, "1111:2222"))
        self.assertFalse(protocol.mouse_no_access(mice, "3367:1970"))
        self.assertFalse(protocol.mouse_no_access(mice, "auto"))
        self.assertFalse(protocol.mouse_no_access(self.MICE, "1111:2222"))      # an older daemon doesn't say

    def test_choice_support(self):
        self.assertTrue(protocol.mouse_choice_supported("auto"))
        self.assertTrue(protocol.mouse_choice_supported("3367:1970"))
        self.assertFalse(protocol.mouse_choice_supported("1111:2222"))

    def test_options(self):
        opts = protocol.mouse_options(self.MICE, "auto")
        self.assertEqual([o["id"] for o in opts], ["auto", "3367:1970", "1111:2222"])
        self.assertEqual(opts[0]["label"], "Automatic")
        self.assertEqual(opts[1]["label"], "Endgame Gear XM2w 4k v2 · dongle")
        self.assertEqual(opts[2]["label"], "Acme Optical Mouse (not tested)")
        self.assertEqual(protocol.mouse_index(opts, "1111:2222"), 2)
        self.assertEqual(protocol.mouse_index(opts, "auto"), 0)

    def test_a_chosen_mouse_that_is_unplugged_stays_visible(self):
        opts = protocol.mouse_options(self.MICE[:1], "1111:2222")
        self.assertEqual(opts[-1], {"id": "1111:2222", "label": "Saved mouse 1111:2222 (not connected)"})
        self.assertEqual(protocol.mouse_index(opts, "1111:2222"), 2)

    def test_junk_from_the_daemon_is_ignored(self):
        junk = [None, 5, {}, {"vendor": "3367"}, {"vendor": "zz", "product": "1"}, dict(self.MICE[1])]
        opts = protocol.mouse_options(junk, "auto")
        self.assertEqual([o["id"] for o in opts], ["auto", "1111:2222"])
        self.assertEqual(protocol.status_mice({"mice": "x"}), [])
        self.assertEqual(protocol.status_mice({}), [])
        self.assertEqual(len(protocol.status_mice({"mice": self.MICE})), 2)

    def test_status_config_carries_the_choice(self):
        self.assertEqual(protocol.status_config({"config": {"mouse": "1111:2222"}})["mouse"], "1111:2222")
        self.assertEqual(protocol.status_config({"config": {"mouse": 5}})["mouse"], "auto")
        self.assertEqual(protocol.status_config({"config": {}})["mouse"], "auto")


class Switching(unittest.TestCase):
    def tearDown(self):
        scan.set_selected("auto")

    def test_a_choice_made_between_discover_and_grab_is_not_lost(self):
        from tests.test_daemon import FakeDevice, FakeUInput
        with tempfile.TemporaryDirectory() as tmp:
            ctl = Controller(config_path=Path(tmp) / "c.json")
            dev = FakeDevice(held_polls=0)
            dev.name, dev.path = "fake", "/dev/input/event9"
            dev.info = SimpleNamespace(vendor=0x3367, product=0x1970)
            dev.ungrab = dev.close = lambda: None
            dev.read = lambda: []
            ui = FakeUInput()
            self.assertIsNone(ctl.set_config({"mouse": "1111:2222"}))      # not connected yet: no restart flag
            self.assertFalse(ctl.restart_requested)
            with mock.patch.object(daemon.UInput, "from_device", return_value=ui):
                self.assertTrue(daemon.run_session(dev, ctl, server=None, tick_log=lambda d: None))
            self.assertFalse(ctl.connected)
            self.assertEqual(ui.log[-1], "close")

    def test_the_search_restarts_at_once_after_a_switch(self):
        ctl = mock.MagicMock()
        ctl.cfg = {"vendor": 1, "product": 2, "retry_s": 99}
        found = iter([(object(), None), KeyboardInterrupt()])

        def discover(*a):
            r = next(found)
            if isinstance(r, BaseException):
                raise r
            return r
        with mock.patch.object(daemon, "Controller", return_value=ctl), \
                mock.patch.object(daemon, "Server"), \
                mock.patch.object(daemon.DeviceWatcher, "open", return_value=None), \
                mock.patch.object(daemon, "discover", discover), \
                mock.patch.object(daemon, "run_session", return_value=True), \
                mock.patch.object(daemon, "idle") as idle:
            daemon.main([])
        idle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
