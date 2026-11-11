"""The Razer family: every listed model is recognised everywhere it has to be, talks with its own polling
command, and stays read-only until it is confirmed on real hardware."""
import re
import tempfile
import unittest
from pathlib import Path

from wheelguard import mice
from wheelguard.hw import hidraw, mock, models, razer, session
from wheelguard.hw.endgame import Rejected, WriteBlocked

RULES = (Path(__file__).resolve().parent.parent / "build-aux" / "70-wheelguard.rules").read_text()


class Table(unittest.TestCase):
    def test_every_link_is_known_found_and_granted(self):
        for pid, link in models.RAZER_LINKS.items():
            with self.subTest(pid=f"{pid:04x}"):
                model = models.MODELS[link.model]
                self.assertEqual(mice.identify(0x1532, pid), (model.name, "cable" if link.wired else "dongle"))
                self.assertIn(pid, hidraw.RAZER_PIDS)
                self.assertIn(f'ATTRS{{idProduct}}=="{pid:04x}", ENV{{WHEELGUARD_RAZER}}="1"', RULES)
                self.assertRegex(RULES, rf'KERNEL=="event\*", ATTRS{{idVendor}}=="1532", ATTRS{{idProduct}}=="{pid:04x}"')
                self.assertIn(link.polling, ("hyper", "classic"))
                code_of = {hz: code for code, hz in razer.RATE_OF.items()}
                for rate in link.rates:
                    self.assertIn(code_of[rate], model.polling_codes)

    def test_new_models_only_grant_pure_mouse_nodes(self):
        for line in RULES.splitlines():
            m = re.search(r'KERNEL=="event\*", ATTRS\{idVendor\}=="1532", ATTRS\{idProduct\}=="([0-9a-f]{4})"', line)
            if m and int(m.group(1), 16) not in (0x00BE, 0x00BF):
                self.assertIn('ENV{ID_INPUT_MOUSE}=="1", ENV{ID_INPUT_KEYBOARD}!="1"', line)

    def test_only_the_deathadder_v4_pro_is_confirmed(self):
        razers = [m for m in models.MODELS.values() if m.brand == "Razer"]
        self.assertGreaterEqual(len(razers), 11)
        self.assertEqual([m.key for m in razers if m.confirmed], ["razer-deathadder-v4-pro"])
        self.assertEqual(set(session.CODECS), set(models.MODELS))

    def test_confirmed_names_stay_short(self):
        self.assertEqual(len(models.confirmed_names()), 3)
        self.assertEqual(set(models.supported_names()), {m.name for m in models.MODELS.values()})


class ClassicPolling(unittest.TestCase):
    CLASSIC = models.RAZER_LINKS[0x00B7]

    def test_each_link_takes_only_its_own_polling_command(self):
        m = models.MODELS[self.CLASSIC.model]
        razer.check_request(razer.build(razer.GET_POLLING_CLASSIC), m, self.CLASSIC)
        razer.check_request(razer.build(razer.SET_POLLING_CLASSIC, bytes((0x02,))), m, self.CLASSIC)
        for cmd, args in ((razer.GET_POLLING, b""), (razer.SET_POLLING, bytes((0, 0x08)))):
            with self.assertRaises(Rejected):
                razer.check_request(razer.build(cmd, args), m, self.CLASSIC)
        for cmd, args in ((razer.GET_POLLING_CLASSIC, b""), (razer.SET_POLLING_CLASSIC, bytes((0x01,)))):
            with self.assertRaises(Rejected):
                razer.check_request(razer.build(cmd, args))           # the DeathAdder V4 Pro: hyper only
        with self.assertRaises(Rejected):
            razer.check_request(razer.build(razer.SET_POLLING_CLASSIC, bytes((0x04,))), m, self.CLASSIC)

    def test_dpi_range_is_the_models(self):
        m = models.MODELS["razer-deathadder-v3-hyperspeed"]           # 26000 DPI
        link = models.RAZER_LINKS[0x00C5]
        ok = razer.build(razer.SET_DPI, bytes((1, 0x65, 0x90, 0x65, 0x90, 0, 0)))      # 26000
        razer.check_request(ok, m, link)
        with self.assertRaises(Rejected):
            razer.check_request(razer.build(razer.SET_DPI, bytes((1, 0x75, 0x30, 0x75, 0x30, 0, 0))), m, link)


class ReadOnlyUntilConfirmed(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        mock._singletons.clear()
        self.addCleanup(mock._singletons.clear)
        self.dev = mock.open_mock("razer-classic")[0]
        self.s = session.MouseSession(lambda: mock.open_mock("razer-classic"), t.name, lambda _t: None)

    def test_reads_settings_and_battery(self):
        snap = self.s.read()
        self.assertEqual(snap.model, "razer-deathadder-v3-pro")
        flat = session.codec_for(snap.model).decode(snap.blob)
        self.assertEqual((flat["polling"], flat["stage_count"], flat["active_stage"]), (0x08, 4, 1))   # 1000 Hz
        self.assertEqual(snap.battery, 80)
        self.assertFalse(snap.writable)
        self.assertIn("hasn't been tested on the Razer DeathAdder V3 Pro", snap.problem)

    def test_nothing_is_written(self):
        self.s.read()
        sent = len(self.dev.sent)
        with self.assertRaises(session.HwError):
            self.s.apply({"idle_min": 10})
        writes = [r for r in self.dev.sent[sent:] if not r[7] & 0x80]
        self.assertEqual(writes, [])

    def test_driver_refuses_writes_itself(self):
        drv = session.make_driver(mock.open_mock("razer-classic"), lambda _t: None)
        drv.identify(lambda *_a: None, 0, 1)
        self.assertFalse(drv.write_enabled)
        with self.assertRaises(WriteBlocked):
            drv.write(razer.Op("idle", "sleep", drv.read_blob()))


if __name__ == "__main__":
    unittest.main()
