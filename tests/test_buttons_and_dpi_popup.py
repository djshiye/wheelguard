"""Button actions on the Logitech G Pro X Superlight and the Razer DeathAdder V4 Pro, the DPI popup
(the mouse's own DPI button noticed and announced), and the narrowed udev rule. Everything runs
against the fake devices in wheelguard.hw.mock, which answer like the documented protocols."""
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

from wheelguard.hw import logitech, mock, razer, session
from wheelguard.hw.endgame import Rejected

ROOT = Path(__file__).resolve().parent.parent


def make_session(mode, tmp):
    mock._singletons.clear()
    dev = mock.open_mock(mode)[0]
    return session.MouseSession(lambda: mock.open_mock(mode), tmp, lambda t: None), dev


class TmpCase(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = t.name


def never():
    return False


# ---------------------------------------------------------------- Logitech buttons

class LogitechButtonCodecTests(unittest.TestCase):
    def blob(self):
        return mock.superlight_profile() + b"\x01"

    def test_decode_the_five_buttons(self):
        d = logitech.decode(self.blob())
        self.assertEqual([d[f"button{k}"] for k in range(5)], [0x01, 0x02, 0x04, 0x08, 0x10])

    def test_encode_writes_only_the_binding_and_a_new_crc(self):
        old = self.blob()
        new = logitech.encode(old, {"button3": logitech.NEXT_DPI, "button4": logitech.MIDDLE})
        self.assertEqual(new[0x2C:0x30], bytes((0x90, 0x05, 0x00, 0x00)))
        self.assertEqual(new[0x30:0x34], bytes((0x80, 0x01, 0x00, 0x04)))
        changed = [i for i in range(len(old) - 3) if old[i] != new[i]]
        self.assertTrue(all(0x2C <= i < 0x34 for i in changed), changed)     # G-shift, name, lighting untouched
        self.assertTrue(logitech.sector_crc_ok(new[:-1]))
        self.assertEqual(logitech.describe_changes(logitech.decode(old), logitech.decode(new)),
                         ["Back (side) action", "Forward (side) action"])

    def test_a_key_or_macro_binding_is_custom_and_never_rewritten(self):
        b = bytearray(mock.superlight_profile())
        b[0x24:0x28] = bytes((0x80, 0x02, 0x01, 0x04))     # right button = Ctrl+A in G HUB
        blob = logitech.with_crc(bytes(b)) + b"\x01"
        self.assertEqual(logitech.decode(blob)["button1"], 0)
        new = logitech.encode(blob, {"dpi_x0": 500})
        self.assertEqual(new[0x24:0x28], bytes((0x80, 0x02, 0x01, 0x04)))
        new = logitech.encode(blob, {"button1": logitech.RIGHT})       # the user can still pick an action
        self.assertEqual(new[0x24:0x28], bytes((0x80, 0x01, 0x00, 0x02)))

    def test_left_click_must_stay_somewhere(self):
        with self.assertRaises(ValueError):
            logitech.encode(self.blob(), {"button0": logitech.RIGHT})
        ok = logitech.encode(self.blob(), {"button0": logitech.RIGHT, "button1": logitech.LEFT})
        self.assertEqual(logitech.decode(ok)["button1"], logitech.LEFT)
        with self.assertRaises(ValueError):
            logitech.check_key_value("button0", 0x0101)       # an Endgame-only action


class LogitechButtonSessionTests(TmpCase):
    def test_apply_writes_the_profile_and_reads_it_back(self):
        s, dev = make_session("logitech", self.tmp)
        res = s.apply({"button4": logitech.NEXT_DPI})
        self.assertEqual(dev.writes, [0x0001])
        self.assertEqual(dev.sectors[0x0001][0x30:0x34], bytes((0x90, 0x05, 0x00, 0x00)))
        self.assertEqual(logitech.decode(res.snapshot.blob)["button4"], logitech.NEXT_DPI)


# ---------------------------------------------------------------- Razer buttons

class RazerButtonTransportTests(unittest.TestCase):
    def req(self, args, cmd=razer.SET_BUTTON, size=0x0A):
        return razer.build(cmd, bytes(args), size)

    def test_allowed_writes(self):
        for size in razer.BUTTON_SIZES:
            razer.check_request(self.req((1, 4, 0, 1, 1, 3), size=size))       # back -> middle click
            razer.check_request(self.req((1, 5, 0, 6, 1, 6), size=size))       # forward -> next DPI stage
            razer.check_request(self.req((1, 2, 0), cmd=razer.GET_BUTTON, size=size))

    def test_everything_else_is_refused(self):
        bad = [
            (2, 4, 0, 1, 1, 3),          # another profile
            (1, 4, 1, 1, 1, 3),          # the HyperShift layer
            (1, 0x60, 0, 1, 1, 3),       # the DPI button
            (1, 4, 0, 2, 2, 0, 4),       # a keyboard key
            (1, 4, 0, 1, 1, 9),          # wheel up
            (1, 4, 0, 0, 0),             # disabled
            (1, 4, 0, 1, 1, 3, 7),       # extra data
            (1, 4, 0, 6, 5, 5),          # DPI clutch
        ]
        for args in bad:
            with self.assertRaises(Rejected, msg=args):
                razer.check_request(self.req(args))
        with self.assertRaises(Rejected):
            razer.check_request(self.req((1, 4, 0, 1, 1, 3), size=0x20))
        with self.assertRaises(Rejected):
            razer.check_request(self.req((1, 4, 1), cmd=razer.GET_BUTTON))


class RazerButtonSessionTests(TmpCase):
    def test_read_and_apply(self):
        s, dev = make_session("razer", self.tmp)
        snap = s.read()
        d = razer.decode(snap.blob)
        self.assertEqual([d[f"button{k}"] for k in range(5)], [0x01, 0x02, 0x04, 0x08, 0x10])
        self.assertTrue(d["buttons_known"])
        dev.sent.clear()
        s.apply({"button3": razer.NEXT_DPI})
        writes = [(r[6], r[7]) for r in dev.sent if not r[7] & 0x80]
        self.assertEqual(writes, [razer.SET_BUTTON])            # nothing else was written
        self.assertEqual(dev.buttons[4][:3], bytes((0x06, 0x01, 0x06)))
        self.assertEqual(dev.buttons[5][:3], bytes((0x01, 0x01, 0x05)))

    def test_uses_the_size_byte_the_mouse_accepts(self):
        s, dev = make_session("razer", self.tmp)
        dev.button_sizes = (0x50,)
        s.apply({"button4": razer.MIDDLE})
        sets = [r for r in dev.sent if (r[6], r[7]) == razer.SET_BUTTON]
        self.assertEqual([r[5] for r in sets], [0x50])
        self.assertEqual(dev.buttons[5][:3], bytes((0x01, 0x01, 0x03)))

    def test_mouse_without_button_commands_still_works(self):
        s, dev = make_session("razer-nobuttons", self.tmp)
        snap = s.read()
        self.assertTrue(snap.writable)
        self.assertFalse(razer.decode(snap.blob)["buttons_known"])
        s.apply({"idle_min": 10})
        self.assertEqual(dev.idle, 600)
        with self.assertRaises(session.HwError):
            s.apply({"button3": razer.MIDDLE})

    def test_left_click_must_stay_somewhere(self):
        s, _dev = make_session("razer", self.tmp)
        with self.assertRaises(session.HwError):
            s.apply({"button0": razer.BACK})

    def test_a_custom_assignment_is_kept(self):
        s, dev = make_session("razer", self.tmp)
        dev.buttons[4] = bytes((0x02, 0x02, 0x00, 0x68, 0, 0, 0))     # F13 from Synapse
        blob = s.read().blob
        self.assertEqual(razer.decode(blob)["button3"], 0)
        s.apply({"dpi_x0": 500, "dpi_y0": 500})
        self.assertEqual(dev.buttons[4], bytes((0x02, 0x02, 0x00, 0x68, 0, 0, 0)))

    def test_old_backup_without_buttons_restores_the_rest(self):
        s, dev = make_session("razer", self.tmp)
        snap = s.read()
        Path(self.tmp).mkdir(exist_ok=True)
        old = bytearray(snap.blob[:razer.BLOB_LEN_V1])
        old[1] = 1
        (Path(self.tmp) / f"razer-deathadder-v4-pro-{snap.mouse_fw}-20250101-000000.bin").write_bytes(bytes(old))
        res = s.apply({"idle_min": 10, "button4": razer.MIDDLE})
        self.assertIsNotNone(res.backup_created)          # the old original can't undo buttons: one more backup
        s.restore()
        self.assertEqual(dev.idle, 300)                                   # from the old original
        self.assertEqual(dev.buttons[5][:3], bytes((0x01, 0x01, 0x05)))   # from the new backup
        res = s.apply({"button3": razer.MIDDLE})
        self.assertIsNone(res.backup_created)                             # only once

    def test_restore_sends_only_writable_assignments(self):
        s, dev = make_session("razer", self.tmp)
        dev.buttons[5] = bytes((0x01, 0x01, 0x05, 0x00, 0x07, 0, 0))    # forward, with a stray byte
        s.apply({"button4": razer.MIDDLE})
        s.restore()
        self.assertEqual(dev.buttons[5][:3], bytes((0x01, 0x01, 0x05)))

    def test_a_dongle_timeout_on_buttons_is_a_read_error_not_no_buttons(self):
        s, dev = make_session("razer", self.tmp)
        real = dev.set_feature

        def flaky(data):
            real(data)
            if data[7:9] == bytes((0x02, 0x8C)):
                r = bytearray(dev._reply)
                r[1] = 0x04                       # timeout: the mouse didn't answer through the dongle
                c = 0
                for x in r[3:89]:
                    c ^= x
                r[89] = c
                dev._reply = bytes(r)
        dev.set_feature = flaky
        with self.assertRaises(session.HwError):
            s.read()


# ---------------------------------------------------------------- DPI stage watch

class StageWatchTests(TmpCase):
    def test_endgame_reports_its_dpi_button(self):
        s, dev = make_session("connected", self.tmp)
        dev.press_dpi_button()
        self.assertEqual(s.watch_stage(never, 1.0), 2)
        self.assertIsNone(s.watch_stage(never, 0.1))            # nothing more to report
        self.assertEqual(dev.sent, [])                          # listening sends nothing

    def test_logitech_reports_its_dpi_button(self):
        s, dev = make_session("logitech", self.tmp)
        self.assertEqual(s.watch_stage(never, 0.1), 1)          # the current index, read once
        dev.press_dpi_button()
        self.assertEqual(s.watch_stage(never, 1.0), 2)
        writes = [r for r in dev.sent if r[3] >> 4 in (1, 3, 6, 7, 8, 0xC)]
        self.assertEqual(writes, [])

    def test_razer_is_read(self):
        s, dev = make_session("razer", self.tmp)
        self.assertEqual(s.watch_stage(never, 1.0), 1)
        dev.press_dpi_button()
        self.assertEqual(s.watch_stage(never, 1.0), 2)
        self.assertTrue(all(r[7] & 0x80 for r in dev.sent))     # reads only

    def test_asleep_raises(self):
        s, _dev = make_session("razer-asleep", self.tmp)
        with self.assertRaises(session.HwError):
            s.watch_stage(never, 0.1)

    def test_a_waiting_operation_gets_the_mouse_quickly(self):
        s, _dev = make_session("connected", self.tmp)
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("stage", s.watch_stage(never, 10.0)))
        t.start()
        time.sleep(0.1)
        began = time.monotonic()
        s.read()
        self.assertLess(time.monotonic() - began, 1.0)
        t.join(2)
        self.assertIsNone(out["stage"])


try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui.mouse import MouseController
    HAVE_QT = True
except ImportError:     # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DpiPopupControllerTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def spin(self, until, timeout=4000):
        loop = QEventLoop()
        t = QTimer()
        t.timeout.connect(lambda: loop.quit() if until() else None)
        t.start(20)
        QTimer.singleShot(timeout, loop.quit)
        if not until():
            loop.exec()
        t.stop()

    def make(self, mode, popup=True):
        s, dev = make_session(mode, self.tmp)
        c = MouseController(s)
        self.addCleanup(c.shutdown)
        got = []
        c.stageAnnounced.connect(lambda label, stage, count: got.append((label, stage, count)))
        c.setStagePopup(popup)
        c.ensureLoaded()
        self.spin(lambda: c.state == "ready")
        c.startStageWatch()
        return c, dev, got

    def test_endgame_dpi_button_is_announced(self):
        c, dev, got = self.make("connected")
        dev.press_dpi_button()
        self.spin(lambda: got)
        self.assertEqual(got, [("1600", 2, 4)])
        self.assertEqual((c.activeStage, c.values["active_stage"], c.changeCount), (2, 2, 0))

    def test_logitech_dpi_button_is_announced(self):
        c, dev, got = self.make("logitech")
        self.spin(lambda: dev.sent)
        dev.press_dpi_button()
        self.spin(lambda: got)
        self.assertEqual(got, [("1600", 2, 4)])

    def test_razer_dpi_button_is_announced(self):
        c, dev, got = self.make("razer")
        dev.press_dpi_button()
        self.spin(lambda: got)
        self.assertEqual(got, [("1600", 2, 4)])

    def test_popup_off_means_no_watching(self):
        c, dev, got = self.make("razer", popup=False)
        dev.sent.clear()
        dev.press_dpi_button()
        self.spin(lambda: False, 600)
        self.assertEqual((got, dev.sent), ([], []))

    def test_shortcut_stage_is_announced_but_panel_clicks_are_not(self):
        c, dev, got = self.make("logitech")
        c.setActiveStage(0)
        self.spin(lambda: c.state == "ready" and dev.dpi_index == 0)
        self.spin(lambda: False, 300)
        self.assertEqual(got, [])
        c.setActiveStage(3, True)
        self.spin(lambda: got)
        self.assertEqual(got, [("3200", 3, 4)])

    def test_staged_edits_survive_a_stage_change(self):
        c, dev, got = self.make("connected")
        c.setValue("dpi_x0", 500)
        dev.press_dpi_button()
        self.spin(lambda: got)
        self.assertEqual(c.editedKeys, ["dpi_x0"])
        self.assertEqual(c.values["active_stage"], 2)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ServiceRelayTests(unittest.TestCase):
    def test_announcement_reaches_the_bus_adaptor(self):
        from PySide6.QtCore import QObject, Signal
        from wheelguard.gui import service

        class Mouse(QObject):
            trayChanged = Signal()
            stageAnnounced = Signal(str, int, int)

        class Daemon(QObject):
            stateChanged = Signal()
            configChanged = Signal()
            statsChanged = Signal()
            clickLockActiveChanged = Signal()

        QCoreApplication.instance() or QCoreApplication([])
        svc = service.Service()
        svc.publish = lambda: None
        m = Mouse()
        svc.attach(Daemon(), m)
        got = []
        svc.adaptor.StageAnnounced.connect(lambda *a: got.append(a))
        m.stageAnnounced.emit("1600", 2, 4)
        self.assertEqual(got, [("1600", 2, 4)])


# ---------------------------------------------------------------- udev rule

class UdevRuleTests(unittest.TestCase):
    def rules(self):
        text = (ROOT / "build-aux" / "70-wheelguard.rules").read_text()
        return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]

    def test_event_nodes_are_mouse_nodes_only(self):
        for ln in self.rules():
            if 'KERNEL=="event*"' in ln:
                self.assertIn('ENV{ID_INPUT_MOUSE}=="1"', ln)

    def test_hidraw_grants_name_an_interface_or_the_receivers_mouse_device(self):
        grants = [ln for ln in self.rules() if 'SUBSYSTEM=="hidraw"' in ln and "uaccess" in ln]
        self.assertEqual(len(grants), 4)
        for ln in grants:
            self.assertTrue(re.search(r'ATTRS\{bInterfaceNumber\}=="0[01]"', ln) or 'KERNELS=="0003:046D:4093.*"' in ln, ln)
            self.assertNotIn("ATTRS{idVendor}", ln)      # never a whole USB device

    def test_every_grant_is_uaccess_only(self):
        for ln in self.rules():
            self.assertNotRegex(ln, r'MODE=|GROUP=|OWNER=|RUN')


if __name__ == "__main__":
    unittest.main()
