"""Tray icon model (menu rows, tooltip, DPI rules), single-instance messages and the autostart override."""
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard.gui import autostart, traymodel
from wheelguard.gui.traymodel import Row, TrayState

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui import single
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

STAGES = ("400", "800", "1600", "3200")


def state(**kw):
    base = dict(daemon_state="running", protecting=True, online=True, battery=83, stages=STAGES, active=1)
    base.update(kw)
    return TrayState(**base)


class MenuTests(unittest.TestCase):
    def test_full_menu(self):
        rows = traymodel.menu_rows(state())
        self.assertEqual([(r.kind, r.text) for r in rows], [
            ("info", "Battery 83%"), ("toggle", "Scroll protection"), ("separator", ""),
            ("stage", "400 DPI"), ("stage", "800 DPI"), ("stage", "1600 DPI"), ("stage", "3200 DPI"),
            ("separator", ""), ("action", "Quick view"), ("action", "Open Wheelguard"), ("action", "Quit")])
        self.assertFalse(rows[0].enabled)
        self.assertTrue(rows[1].checked and rows[1].enabled)
        self.assertEqual([r.checked for r in rows if r.kind == "stage"], [False, True, False, False])
        self.assertEqual([r.index for r in rows if r.kind == "stage"], [0, 1, 2, 3])
        self.assertTrue(all(r.enabled for r in rows[3:]))
        self.assertEqual([r.key for r in rows if r.kind == "action"], ["popup", "open", "quit"])

    def test_stage_count_follows_the_mouse(self):
        rows = traymodel.menu_rows(state(stages=("800", "1600"), active=0))
        self.assertEqual([r.text for r in rows if r.kind == "stage"], ["800 DPI", "1600 DPI"])

    def test_charging_and_unknown_battery(self):
        self.assertEqual(traymodel.menu_rows(state(charging=True))[0].text, "Battery 83% · Charging")
        self.assertEqual(traymodel.menu_rows(state(battery=-1))[0].text, "Battery unknown")

    def test_offline_mouse(self):
        rows = traymodel.menu_rows(state(online=False, battery=-1, stages=(), active=-1,
                                         stage_reason="Mouse not connected"))
        self.assertEqual(rows[0], Row("info", "Mouse not connected", enabled=False))
        self.assertFalse([r for r in rows if r.kind == "stage"])
        self.assertEqual(rows[3], Row("info", "DPI: Mouse not connected", enabled=False))

    def test_staged_edits_disable_but_keep_the_stage_rows(self):
        rows = traymodel.menu_rows(state(stage_reason=traymodel.REASON_EDITS))
        stages = [r for r in rows if r.kind == "stage"]
        self.assertEqual(len(stages), 4)
        self.assertFalse(any(r.enabled for r in stages))
        self.assertIn(Row("info", "Unsaved changes on the Mouse page", enabled=False), rows)

    def test_protection_toggle_needs_the_service(self):
        for ds, on in (("running", True), ("waiting", True), ("stopped", False), ("permission", False)):
            self.assertEqual(traymodel.menu_rows(state(daemon_state=ds))[1].enabled, on, ds)
        self.assertFalse(traymodel.menu_rows(state(protecting=False))[1].checked)

    def test_tooltip(self):
        self.assertEqual(traymodel.tooltip(state()), "Wheelguard — protection on · 83%")
        self.assertEqual(traymodel.tooltip(state(protecting=False, online=False)), "Wheelguard — protection off")
        self.assertEqual(traymodel.tooltip(state(daemon_state="stopped")), "Wheelguard — service not running · 83%")
        self.assertEqual(traymodel.tooltip(state(battery=-1)), "Wheelguard — protection on")


class StageRuleTests(unittest.TestCase):
    def test_labels(self):
        flat = {"stage_count": 3, "dpi_x0": 400, "dpi_y0": 400, "dpi_x1": 800, "dpi_y1": 400,
                "dpi_x2": 1600, "dpi_y2": 1600, "dpi_x3": 3200, "dpi_y3": 3200}
        self.assertEqual(traymodel.stage_labels(flat), ["400", "800 × 400", "1600"])
        self.assertEqual(traymodel.stage_labels({}), [])

    def test_reason(self):
        r = traymodel.stage_reason
        self.assertEqual(r("ready", True, True, 0), "")
        self.assertEqual(r("applying", True, True, 0), "")
        self.assertEqual(r("ready", True, True, 2), traymodel.REASON_EDITS)
        self.assertEqual(r("ready", True, False, 0), traymodel.REASON_READONLY)
        self.assertEqual(r("asleep", True, True, 0), traymodel.REASON_OFFLINE)
        self.assertEqual(r("missing", False, False, 0), traymodel.REASON_OFFLINE)
        self.assertEqual(r("reading", False, False, 0), traymodel.REASON_READING)
        self.assertEqual(r("idle", False, False, 0), traymodel.REASON_READING)


class MessageTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_parse(self):
        self.assertEqual(single.parse_message(b"show\n"), "show")
        self.assertEqual(single.parse_message(b"tray"), "tray")
        self.assertIsNone(single.parse_message(b"quit\n"))
        self.assertIsNone(single.parse_message(b""))
        self.assertIsNone(single.parse_message(b"\xff\xfe"))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "wg.sock")

    def pump(self, got, n=1):
        loop = QEventLoop()
        QTimer.singleShot(2000, loop.quit)
        t = QTimer()
        t.timeout.connect(lambda: loop.quit() if len(got) >= n else None)
        t.start(10)
        loop.exec()

    def test_nobody_listening(self):
        self.assertFalse(single.send("show", self.path))

    def test_message_reaches_the_running_instance(self):
        srv = single.InstanceServer(self.path)
        self.addCleanup(srv.close)
        self.assertTrue(srv.listen())
        got = []
        srv.messageReceived.connect(got.append)
        self.assertTrue(single.send("show", self.path))
        self.assertTrue(single.send("tray", self.path))
        self.pump(got, 2)
        self.assertEqual(got, ["show", "tray"])

    def test_stale_socket_is_replaced(self):
        Path(self.path).write_text("")           # what a crashed instance leaves behind
        self.assertFalse(single.send("show", self.path))
        srv = single.InstanceServer(self.path)
        self.addCleanup(srv.close)
        self.assertTrue(srv.listen())
        got = []
        srv.messageReceived.connect(got.append)
        self.assertTrue(single.send("show", self.path))
        self.pump(got)
        self.assertEqual(got, ["show"])


class AutostartTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.sys = Path(tmp.name) / "etc"

    def install_system(self):
        (self.sys / "autostart").mkdir(parents=True)
        (self.sys / "autostart" / autostart.FILENAME).write_text("[Desktop Entry]\nType=Application\n")

    def test_not_packaged(self):
        self.assertFalse(autostart.available(str(self.sys)))
        self.assertFalse(autostart.is_enabled(self.home, str(self.sys)))

    def test_toggle_writes_and_removes_the_user_override(self):
        self.install_system()
        d = str(self.sys)
        self.assertTrue(autostart.is_enabled(self.home, d))
        autostart.set_enabled(False, self.home)
        self.assertIn("Hidden=true", autostart.user_file(self.home).read_text())
        self.assertFalse(autostart.is_enabled(self.home, d))
        autostart.set_enabled(True, self.home)
        self.assertFalse(autostart.user_file(self.home).exists())
        self.assertTrue(autostart.is_enabled(self.home, d))

    def test_enable_leaves_a_users_own_entry_alone(self):
        self.install_system()
        u = autostart.user_file(self.home)
        u.parent.mkdir(parents=True)
        u.write_text("[Desktop Entry]\nType=Application\nExec=custom\n")
        autostart.set_enabled(True, self.home)
        self.assertTrue(u.exists())


if __name__ == "__main__":
    unittest.main()
