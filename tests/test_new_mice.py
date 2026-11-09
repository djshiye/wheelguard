"""Razer DeathAdder V4 Pro and Logitech G Pro X Superlight: transports, codecs and the session, all run
against the fake devices in wheelguard.hw.mock (which answer like the real protocols)."""
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard.hw import hidpp, hidraw, logitech, mock, razer, session
from wheelguard.hw.endgame import Rejected, WriteBlocked


def make_session(mode, tmp):
    mock._singletons.clear()
    dev = mock.open_mock(mode)[0]
    return session.MouseSession(lambda: mock.open_mock(mode), tmp, lambda t: None), dev


class TmpCase(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = t.name


# ---------------------------------------------------------------- Razer

class RazerTransportTests(unittest.TestCase):
    def test_report_shape_and_crc(self):
        r = razer.build(razer.GET_STAGES, bytes((razer.VARSTORE,)))
        self.assertEqual((len(r), r[1], r[5], r[6], r[7]), (90, 0x1F, 0x26, 0x04, 0x86))
        c = 0
        for b in r[2:88]:
            c ^= b
        self.assertEqual(r[88], c)
        self.assertEqual(razer.check_request(r), razer.GET_STAGES)

    def test_unknown_and_dangerous_commands_are_refused(self):
        for cmd in ((0x00, 0x04), (0x00, 0x87), (0x0F, 0x02), (0x00, 0x00)):   # device mode, ..., lighting
            with self.subTest(cmd=cmd):
                with self.assertRaises(Rejected):
                    razer.check_request(razer.build(cmd))

    def test_write_arguments_are_checked(self):
        bad = [
            (razer.SET_DPI, bytes((1, 0, 50, 0, 50, 0, 0))),                    # below 100 DPI
            (razer.SET_DPI, bytes((1, 0xC0, 0, 0xC0, 0, 0, 0))),                # above 45000
            (razer.SET_POLLING, bytes((0, 0x03))),                              # not a polling code
            (razer.SET_IDLE, bytes((0, 30))),                                    # 30 s
            (razer.SET_LOW_BATTERY, bytes((0x50,))),
            (razer.SET_STAGES, bytes((1, 3, 2)) + bytes((0, 3, 0x20, 3, 0x20, 0, 0)) * 2),   # active > count
            (razer.SET_STAGES, bytes((1, 1, 6))),                               # 6 stages
        ]
        for cmd, args in bad:
            with self.subTest(cmd=cmd, args=args.hex()):
                with self.assertRaises(Rejected):
                    razer.check_request(razer.build(cmd, args))

    def test_tampered_crc_is_refused(self):
        r = bytearray(razer.build(razer.GET_FIRMWARE))
        r[88] ^= 1
        with self.assertRaises(Rejected):
            razer.check_request(bytes(r))

    def test_writes_need_an_identified_mouse(self):
        dev = mock.FakeRazer()
        rz = razer.RazerDevice(dev, False, lambda t: None)
        with self.assertRaises(WriteBlocked):
            rz.set_idle(300)
        self.assertEqual(dev.sent, [])
        rz.firmware()
        rz.set_idle(300)
        self.assertEqual(dev.idle, 300)


class RazerCodecTests(unittest.TestCase):
    BLOB = razer.pack(1, [(400, 400), (800, 800), (1600, 1600)], 0x08, 300, 0x26)

    def test_round_trip(self):
        self.assertEqual(razer.encode(self.BLOB, razer.decode(self.BLOB)), self.BLOB)
        self.assertEqual(razer.blob_problems(self.BLOB), [])

    def test_plan_only_what_changed(self):
        new = razer.encode(self.BLOB, {"polling": 0x01})
        self.assertEqual([op.kind for op in razer.plan_writes(self.BLOB, new)], ["polling"])
        new = razer.encode(self.BLOB, {"dpi_y2": 1200, "idle_min": 10})
        self.assertEqual([op.kind for op in razer.plan_writes(self.BLOB, new)], ["stages", "idle"])

    def test_more_stages_start_from_the_previous_one(self):
        new = razer.decode(razer.encode(self.BLOB, {"stage_count": 5}))
        self.assertEqual((new["dpi_x3"], new["dpi_x4"]), (1600, 1600))

    def test_validation(self):
        with self.assertRaises(ValueError):
            razer.validate_settings(razer.decode(razer.encode(self.BLOB, {"active_stage": 4})))
        for key, val in (("dpi_x0", 50), ("idle_min", 16), ("polling", 0x03), ("low_battery", 1)):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    razer.encode(self.BLOB, {key: val})


class RazerSessionTests(TmpCase):
    def test_read(self):
        s, dev = make_session("razer", self.tmp)
        snap = s.read()
        self.assertEqual((snap.model, snap.mouse_fw, snap.battery, snap.charging, snap.writable, snap.wired),
                         ("razer-deathadder-v4-pro", "1.3", 80, False, True, False))
        v = razer.decode(snap.blob)
        self.assertEqual((v["stage_count"], v["active_stage"], v["dpi_x2"], v["idle_min"]), (4, 1, 1600, 5))

    def test_apply_writes_only_changes_and_keeps_a_backup(self):
        s, dev = make_session("razer", self.tmp)
        res = s.apply({"dpi_x1": 1200, "dpi_y1": 1000})
        self.assertEqual(res.written, ["sensitivity"])
        self.assertTrue(res.backup_created)
        self.assertEqual(dev.stages[1], (1200, 1000))
        sent = [(r[6], r[7]) for r in dev.sent]
        self.assertIn(razer.SET_STAGES, sent)
        self.assertIn(razer.SET_DPI, sent)
        self.assertNotIn(razer.SET_POLLING, sent)
        self.assertNotIn(razer.SET_IDLE, sent)

    def test_polling_is_sent_twice_like_openrazer(self):
        s, dev = make_session("razer", self.tmp)
        s.apply({"polling": 0x01})
        polls = [r[8:10] for r in dev.sent if (r[6], r[7]) == razer.SET_POLLING]
        self.assertEqual(polls, [b"\x00\x01", b"\x01\x01"])
        self.assertEqual(dev.polling, 0x01)

    def test_restore(self):
        s, dev = make_session("razer", self.tmp)
        s.apply({"polling": 0x40, "idle_min": 12, "low_battery": 0x0D})
        res = s.restore()
        self.assertEqual((dev.polling, dev.idle, dev.low), (0x08, 300, 0x26))
        self.assertTrue(res.written)

    def test_asleep(self):
        s, _dev = make_session("razer-asleep", self.tmp)
        with self.assertRaises(session.MouseAsleep):
            s.read()

    def test_wired_reports_charging(self):
        s, _dev = make_session("razer-wired", self.tmp)
        self.assertTrue(s.read().charging)
        self.assertTrue(s.read_battery().charging)


# ---------------------------------------------------------------- Logitech

class HidppTests(unittest.TestCase):
    def make(self, mode="logitech"):
        dev = mock.FakeLogitech(mode)
        return hidpp.HidppDevice(dev, lambda t: None), dev

    def test_crc_matches_the_standard_check_value(self):
        self.assertEqual(hidpp.crc_ccitt(b"123456789"), 0x29B1)

    def test_voltage_curve(self):
        self.assertEqual(hidpp.voltage_percent(4200), 100)
        self.assertEqual(hidpp.voltage_percent(3811), 50)
        self.assertEqual(hidpp.voltage_percent(3400), 0)

    def test_noise_is_skipped_and_features_resolved(self):
        hp, _dev = self.make()
        hp.ping()
        self.assertTrue(hp.has(hidpp.ONBOARD_PROFILES))
        self.assertFalse(hp.has(hidpp.UNIFIED_BATTERY))

    def test_features_off_the_list_are_refused(self):
        hp, dev = self.make()
        n = len(dev.sent)
        for fid in (0x1814, 0x1B04, 0x00C2, 0x1802):     # change host, reprogram controls, DFU, reset
            with self.subTest(fid=hex(fid)):
                with self.assertRaises(Rejected):
                    hp.feature(fid)
        self.assertEqual(len(dev.sent), n)

    def test_memory_writes_are_gated(self):
        hp, dev = self.make()
        hp.feature(hidpp.ONBOARD_PROFILES)
        hp.sector_size = 255
        with self.assertRaises(WriteBlocked):
            hp.write_sector(1, bytes(255))
        hp.write_enabled = True
        with self.assertRaises(Rejected):            # sector not unlocked for this write
            hp.write_sector(1, bytes(255))
        hp.writable_sectors = {1}
        with self.assertRaises(Rejected):            # data without an address write
            hp.request(hidpp.ONBOARD_PROFILES, hidpp.OP_MEM_WRITE, bytes(16))
        with self.assertRaises(Rejected):            # only onboard mode may be selected
            hp.request(hidpp.ONBOARD_PROFILES, hidpp.OP_SET_MODE, bytes((2,)))
        self.assertEqual(dev.writes, [])

    def test_read_sector_aligns_the_last_chunk(self):
        hp, dev = self.make()
        hp.feature(hidpp.ONBOARD_PROFILES)
        self.assertEqual(hp.read_sector(1, 255), dev.sectors[1])


class LogitechCodecTests(unittest.TestCase):
    BLOB = mock.superlight_profile() + b"\x01"

    def test_round_trip_and_crc(self):
        self.assertEqual(logitech.encode(self.BLOB, logitech.decode(self.BLOB)), self.BLOB)
        self.assertEqual(logitech.blob_problems(self.BLOB), [])
        new = logitech.encode(self.BLOB, {"dpi_x0": 450})
        self.assertTrue(logitech.sector_crc_ok(new[:-1]))
        changed = [i for i in range(len(self.BLOB)) if new[i] != self.BLOB[i]]
        self.assertTrue(set(changed) <= {3, 4, 253, 254})     # the DPI value and the CRC, nothing else

    def test_active_stage_alone_never_touches_flash(self):
        new = logitech.encode(self.BLOB, {"active_stage": 3})
        self.assertEqual([op.kind for op in logitech.plan_writes(self.BLOB, new)], ["dpi_index"])

    def test_stage_count(self):
        v = logitech.decode(logitech.encode(self.BLOB, {"stage_count": 2}))
        self.assertEqual((v["stage_count"], v["dpi_x2"], v["active_stage"]), (2, 0, 1))
        v = logitech.decode(logitech.encode(self.BLOB, {"stage_count": 5}))
        self.assertEqual(v["dpi_x4"], 3200)

    def test_bad_crc_is_read_only(self):
        b = bytearray(self.BLOB)
        b[10] ^= 0xFF
        self.assertTrue(logitech.blob_problems(bytes(b)))

    def test_directory(self):
        d = logitech.directory(255)
        self.assertTrue(logitech.sector_crc_ok(d))
        self.assertEqual(logitech.directory_entries(d), [(1, True)])


class LogitechSessionTests(TmpCase):
    def test_read(self):
        s, _dev = make_session("logitech", self.tmp)
        snap = s.read()
        self.assertEqual((snap.model, snap.mouse_fw, snap.writable, snap.battery),
                         ("logitech-pro-x-superlight", "MPM 25.01.B0010", True, 67))
        v = logitech.decode(snap.blob)
        self.assertEqual((v["stage_count"], v["active_stage"], v["polling"]), (4, 1, 1))

    def test_apply_writes_the_active_profile_only(self):
        s, dev = make_session("logitech", self.tmp)
        before = dict(dev.sectors)
        res = s.apply({"dpi_x2": 2000, "dpi_y2": 2000, "polling": 2})
        self.assertEqual(res.written, ["profile", "DPI stage"])
        self.assertEqual(dev.writes, [1])
        self.assertEqual(dev.sectors[0], before[0])
        new = dev.sectors[1]
        self.assertTrue(logitech.sector_crc_ok(new))
        self.assertEqual(new[13:250], before[1][13:250])          # buttons, name, lighting untouched
        self.assertEqual(logitech.decode(new + b"\x01")["dpi_x2"], 2000)

    def test_switches_back_to_onboard_mode(self):
        s, dev = make_session("logitech", self.tmp)
        dev.onboard = 2
        res = s.apply({"polling": 8})
        self.assertEqual(res.written, ["onboard mode", "profile", "DPI stage"])
        self.assertEqual(dev.onboard, 1)

    def test_factory_mouse_gets_a_profile(self):
        s, dev = make_session("logitech-factory", self.tmp)
        snap = s.read()
        self.assertTrue(snap.writable)
        s.apply({"dpi_x0": 900, "dpi_y0": 900})
        self.assertEqual(dev.writes, [1, 0])
        self.assertEqual(dev.current, 1)
        self.assertEqual(dev.sectors[0x0101], mock.superlight_profile(255, (800, 1600, 3200, 0, 0), 1, 0))
        self.assertTrue(s.read().writable)

    def test_failed_directory_read_never_overwrites_a_saved_profile(self):
        # The directory read fails (e.g. the mouse is busy) on a mouse that has a saved profile 1:
        # it must not be mistaken for a factory-new mouse, whose first write replaces sector 1.
        s, dev = make_session("logitech", self.tmp)
        del dev.sectors[0]
        snap = s.read()
        self.assertFalse(snap.writable)
        self.assertIn("saved profile", snap.problem)
        with self.assertRaises(session.HwError):
            s.apply({"dpi_x0": 900, "dpi_y0": 900})
        self.assertEqual(dev.writes, [])

    def test_corrupt_directory_is_read_only(self):
        s, dev = make_session("logitech", self.tmp)
        d = bytearray(dev.sectors[0])
        d[0] ^= 0x01
        dev.sectors[0] = bytes(d)
        snap = s.read()
        self.assertFalse(snap.writable)
        self.assertEqual(dev.writes, [])

    def test_factory_restore_goes_back_to_factory_settings(self):
        s, dev = make_session("logitech-factory", self.tmp)
        factory = dev.sectors[0x0101]
        s.apply({"dpi_x0": 900, "dpi_y0": 900})
        s.apply({"dpi_x0": 1000, "dpi_y0": 1000})       # a second edit must not become the "original"
        s.read()
        s.restore()
        self.assertEqual(logitech.decode(dev.sectors[1] + b"\x00")["dpi_x0"],
                         logitech.decode(factory + b"\x00")["dpi_x0"])

    def test_zero_based_current_profile_with_one_profile(self):
        s, dev = make_session("logitech", self.tmp)
        dev.current = 0
        self.assertTrue(s.read().writable)
        s.apply({"polling": 8})
        self.assertEqual(dev.writes, [1])

    def test_built_in_profile_selected_while_saved_ones_exist_is_read_only(self):
        s, dev = make_session("logitech", self.tmp)
        dev.current = 0x0101
        snap = s.read()
        self.assertFalse(snap.writable)
        self.assertIn("built-in", snap.problem)
        with self.assertRaises(session.HwError):
            s.apply({"polling": 8})
        self.assertEqual(dev.writes, [])

    def test_restore(self):
        s, dev = make_session("logitech", self.tmp)
        original = dev.sectors[1]
        s.apply({"dpi_x0": 1000, "dpi_y0": 1000})
        s.restore()
        self.assertEqual(dev.sectors[1], original)

    def test_wireless_replies_with_receiver_index_are_accepted(self):
        s, dev = make_session("logitech", self.tmp)      # the wireless fake answers on index 0x01
        self.assertFalse(dev.wired)
        self.assertTrue(s.read().writable)
        s.apply({"dpi_x1": 1700, "dpi_y1": 1700})
        self.assertEqual(logitech.decode(dev.sectors[1] + b"\x00")["dpi_x1"], 1700)

    def test_profile_missing_from_directory_is_read_only(self):
        s, dev = make_session("logitech", self.tmp)
        dev.sectors[2] = dev.sectors[1]
        dev.current = 2                                   # sector 2 exists but isn't listed
        snap = s.read()
        self.assertFalse(snap.writable)
        self.assertIn("profile list", snap.problem)
        with self.assertRaises(session.HwError):
            s.apply({"polling": 8})
        self.assertEqual(dev.writes, [])

    def test_backup_name_includes_the_profile(self):
        s, _dev = make_session("logitech", self.tmp)
        res = s.apply({"polling": 8})
        self.assertIn("-p0001-", Path(res.backup_created).name)

    def test_asleep(self):
        s, _dev = make_session("logitech-asleep", self.tmp)
        with self.assertRaises(session.MouseAsleep):
            s.read()


# ---------------------------------------------------------------- discovery

class NewDiscoveryTests(unittest.TestCase):
    def tree(self, nodes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, hid_id, desc, iface in nodes:
            hid = root / "devices" / f"usb-{name}" / f"1-1:1.{iface}" / "hid"
            hid.mkdir(parents=True)
            (hid.parent / "bInterfaceNumber").write_text(f"{iface:02x}\n")
            (hid / "uevent").write_text(f"HID_ID={hid_id}\n")
            (hid / "report_descriptor").write_bytes(desc)
            (root / "class" / name).mkdir(parents=True)
            os.symlink(hid, root / "class" / name / "device")
        return str(root / "class")

    HIDPP = bytes.fromhex("0600ff0901a1018510c0" "0600ff0902a1018511c0")
    MOUSE = bytes.fromhex("05010902a101c0")

    def test_razer_interface_zero_only(self):
        root = self.tree([("hidraw0", "0003:00001532:000000BF", self.MOUSE, 0),
                          ("hidraw1", "0003:00001532:000000BF", self.MOUSE, 1),
                          ("hidraw2", "0003:00001532:000000AA", self.MOUSE, 0)])
        self.assertEqual([c.sysname for c in hidraw.discover_razer(root)], ["hidraw0"])

    def test_logitech_needs_hidpp(self):
        root = self.tree([("hidraw3", "0003:0000046D:00004093", self.MOUSE, 2),
                          ("hidraw4", "0003:0000046D:00004093", self.HIDPP, 2),
                          ("hidraw5", "0003:0000046D:0000C547", self.HIDPP, 2)])     # the receiver itself
        found = hidraw.discover_logitech(root)
        self.assertEqual([(c.sysname, c.kind, c.wired) for c in found], [("hidraw4", "logitech", False)])


if __name__ == "__main__":
    unittest.main()


try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui.mouse import MouseController
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ControllerModelTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def wait(self, c, states, timeout=5000):
        loop = QEventLoop()
        c.stateChanged.connect(lambda: loop.quit() if c.state in states else None)
        QTimer.singleShot(timeout, loop.quit)
        if c.state not in states:
            loop.exec()
        self.assertIn(c.state, states)

    def make(self, mode):
        s, dev = make_session(mode, self.tmp)
        c = MouseController(s)
        self.addCleanup(c.shutdown)
        c.ensureLoaded()
        self.wait(c, ("ready",))
        return c, dev

    def test_razer_page_model(self):
        c, dev = self.make("razer")
        self.assertEqual((c.modelName, c.maxStages, c.xyDpi, c.dpiMax), ("Razer DeathAdder V4 Pro", 5, True, 45000))
        self.assertIn("idle", c.features)
        self.assertEqual(c.pollingLabels[-1], "8000 Hz")
        c.setValue("idle_min", 10)
        c.setValue("glass", True)                  # not a Razer setting: ignored
        self.assertEqual(c.changeSummary, "Sleep")
        c.apply()
        self.wait(c, ("ready",))
        self.assertEqual((dev.idle, c.messageKind), (600, "ok"))
        self.assertIn("glass", c.values)           # neutral values keep hidden QML bindings defined

    def test_superlight_tray_stage_switch(self):
        c, dev = self.make("logitech")
        self.assertEqual((c.modelName, c.xyDpi, c.stageLabels), ("Logitech G Pro X Superlight", False,
                                                                 ["400", "800", "1600", "3200"]))
        c.setActiveStage(3)
        self.wait(c, ("ready",))
        self.assertEqual((dev.dpi_index, dev.writes), (3, []))      # no flash write for a stage switch
