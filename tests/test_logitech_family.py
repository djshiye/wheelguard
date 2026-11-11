"""The Logitech family: every listed G mouse is recognised everywhere it has to be, uses its own DPI range,
and stays read-only until it is confirmed on real hardware."""
import os
import re
import tempfile
import unittest
from pathlib import Path

from wheelguard import mice
from wheelguard.hw import hidraw, logitech, mock, models, session
from wheelguard.hw.endgame import WriteBlocked

RULES = (Path(__file__).resolve().parent.parent / "build-aux" / "70-wheelguard.rules").read_text()
SUPERLIGHT_PIDS = (0x4093, 0xC094)


class Table(unittest.TestCase):
    def test_every_link_is_known_found_and_granted(self):
        for pid, link in models.LOGI_LINKS.items():
            with self.subTest(pid=f"{pid:04x}"):
                model = models.MODELS[link.model]
                self.assertEqual(model.brand, "Logitech")
                self.assertEqual(mice.identify(0x046D, pid), (model.name, "cable" if link.wired else "receiver"))
                self.assertIn(pid, hidraw.LOGI_PIDS)
                if link.wired:
                    self.assertIn(f'SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="046d", ATTRS{{idProduct}}=="{pid:04x}", TAG+="uaccess"', RULES)
                    self.assertRegex(RULES, rf'KERNEL=="event\*", ATTRS{{idVendor}}=="046d", ATTRS{{idProduct}}=="{pid:04x}"')
                else:
                    self.assertIn(f'SUBSYSTEM=="hidraw", KERNELS=="0003:046D:{pid:04X}.*", TAG+="uaccess"', RULES)
                    self.assertIn(f'KERNEL=="event*", KERNELS=="0003:046D:{pid:04X}.*"', RULES)
                self.assertEqual(model.polling_codes, models.SUPERLIGHT.polling_codes)     # 125-1000 Hz only

    def test_no_pid_is_listed_twice(self):
        pids = [link.pid for _m, links in models._LOGI_FAMILY for link in links]
        self.assertEqual(len(pids), len(set(pids)))
        self.assertFalse(set(pids) & set(SUPERLIGHT_PIDS))
        self.assertFalse(set(pids) & set(models.RAZER_LINKS))       # different vendor ids, but keep them apart

    def test_new_models_only_grant_pure_mouse_nodes(self):
        for line in RULES.splitlines():
            m = re.search(r'KERNEL=="event\*", (?:KERNELS=="0003:046D:([0-9A-F]{4})\.\*"|ATTRS\{idVendor\}=="046d", '
                          r'ATTRS\{idProduct\}=="([0-9a-f]{4})")', line)
            if m and int(m.group(1) or m.group(2), 16) not in SUPERLIGHT_PIDS:
                self.assertIn('ENV{ID_INPUT_MOUSE}=="1", ENV{ID_INPUT_KEYBOARD}!="1"', line)

    def test_only_the_superlight_is_confirmed(self):
        logis = [m for m in models.MODELS.values() if m.brand == "Logitech"]
        self.assertGreaterEqual(len(logis), 15)
        self.assertEqual([m.key for m in logis if m.confirmed], ["logitech-pro-x-superlight"])
        for m in logis:
            self.assertIn(m.key, session.CODECS)
            self.assertIs(session.codec_for(m.key).model, m)

    def test_summary_names_both_families(self):
        text = models.supported_summary()
        self.assertTrue(text.startswith("the Endgame Gear XM2w 4k v2, Logitech G Pro X Superlight and Razer DeathAdder V4 Pro"))
        self.assertIn("more Logitech and Razer mice (read-only for now)", text)


class DpiRange(unittest.TestCase):
    BLOB = mock.superlight_profile(255, (400, 800, 1600, 3200, 25600)) + b"\x01"

    def test_each_model_checks_its_own_range(self):
        g502 = session.codec_for("logitech-g502-lightspeed")
        g305 = session.codec_for("logitech-g305")
        self.assertEqual(g502.blob_problems(self.BLOB), [])
        self.assertTrue(any("out of range" in p for p in g305.blob_problems(self.BLOB)))     # 12000 DPI sensor
        with self.assertRaises(ValueError):
            g305.check_key_value("dpi_x0", 16000)
        with self.assertRaises(ValueError):
            g305.check_key_value("dpi_x0", 100)                     # starts at 200
        g502.check_key_value("dpi_x0", 25600)
        self.assertEqual(g305.snap_dpi(20000), 12000)


class Discovery(unittest.TestCase):
    HIDPP = bytes.fromhex("0600ff0901a1018510c0" "0600ff0902a1018511c0")

    def test_family_is_found_wired_first(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, hid_id in (("hidraw0", "0003:0000046D:0000407F"), ("hidraw1", "0003:0000046D:0000C08B"),
                             ("hidraw2", "0003:0000046D:0000C548")):       # a receiver: never a mouse
            hid = root / "devices" / name / "hid"
            hid.mkdir(parents=True)
            (hid / "uevent").write_text(f"HID_ID={hid_id}\n")
            (hid / "report_descriptor").write_bytes(self.HIDPP)
            (root / "class" / name).mkdir(parents=True)
            os.symlink(hid, root / "class" / name / "device")
        found = hidraw.discover_logitech(str(root / "class"))
        self.assertEqual([(c.sysname, c.pid, c.wired) for c in found], [("hidraw1", 0xC08B, True), ("hidraw0", 0x407F, False)])


class ReadOnlyUntilConfirmed(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        mock._singletons.clear()
        self.addCleanup(mock._singletons.clear)
        self.dev = mock.open_mock("logitech-g502")[0]
        self.s = session.MouseSession(lambda: mock.open_mock("logitech-g502"), t.name, lambda _t: None)

    def test_reads_settings_and_battery(self):
        snap = self.s.read()
        self.assertEqual(snap.model, "logitech-g502-lightspeed")
        flat = session.codec_for(snap.model).decode(snap.blob)
        self.assertEqual((flat["stage_count"], flat["dpi_x4"], flat["polling"]), (5, 25600, 1))
        self.assertIsNotNone(snap.battery)
        self.assertFalse(snap.writable)
        self.assertIn("hasn't been tested on the Logitech G502 Lightspeed", snap.problem)

    def test_nothing_is_written(self):
        self.s.read()
        with self.assertRaises(session.HwError):
            self.s.apply({"dpi_x0": 1200})
        self.assertEqual(self.dev.writes, [])

    def test_driver_and_transport_refuse_writes(self):
        drv = session.make_driver(mock.open_mock("logitech-g502"), lambda _t: None)
        drv.identify(lambda *_a: None, 0, 1)
        self.assertFalse(drv.write_enabled)
        self.assertFalse(drv.hp.write_enabled)
        with self.assertRaises(WriteBlocked):
            drv.write(logitech.Op("dpi_index", "DPI stage", index=0))

    def test_superlight_still_writes(self):
        mock._singletons.clear()
        drv = session.make_driver(mock.open_mock("logitech"), lambda _t: None)
        drv.identify(lambda *_a: None, 0, 1)
        self.assertTrue(drv.write_enabled)
        self.assertEqual(drv.state_problem(), "")


if __name__ == "__main__":
    unittest.main()
