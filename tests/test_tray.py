"""Tray/panel model (menu rows, tooltip, D-Bus state, DPI rules), extension helpers and the autostart override."""
import tempfile
import unittest
from pathlib import Path

from wheelguard.gui import autostart, traymodel
from wheelguard.gui.traymodel import Row, TrayState

try:
    from wheelguard.gui import shellext
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
            ("separator", ""), ("action", "Open Wheelguard"), ("action", "Quit")])
        self.assertFalse(rows[0].enabled)
        self.assertTrue(rows[1].checked and rows[1].enabled)
        self.assertEqual([r.checked for r in rows if r.kind == "stage"], [False, True, False, False])
        self.assertEqual([r.index for r in rows if r.kind == "stage"], [0, 1, 2, 3])
        self.assertTrue(all(r.enabled for r in rows[3:]))
        self.assertNotIn("Quick view", [r.text for r in rows])
        self.assertEqual([r.key for r in rows if r.kind == "action"], ["open", "quit"])

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


class StateDictTests(unittest.TestCase):
    def test_keys_and_values(self):
        d = traymodel.state_dict(state(preset="Balanced", dropped=82, charging=True))
        for k, v in {"Online": True, "Battery": 83, "Charging": True, "StageLabels": list(STAGES),
                     "ActiveStage": 1, "StageReason": "", "ProtectionEnabled": True, "DaemonState": "running",
                     "Preset": "Balanced", "Dropped": 82}.items():
            self.assertEqual(d[k], v, k)
        self.assertEqual(d["StatusLabel"], "Protecting")
        self.assertEqual(d["StatusTone"], "good")
        self.assertEqual(d["ProtectionDetail"], "Balanced · 82 dropped")
        self.assertEqual(d["BatteryNote"], "Charging")

    def test_offline_mouse_hides_battery(self):
        d = traymodel.state_dict(state(online=False, battery=55, charging=True, stages=(), active=-1,
                                       stage_reason=traymodel.REASON_OFFLINE))
        self.assertEqual((d["Online"], d["Battery"], d["Charging"], d["StageLabels"]), (False, -1, False, []))
        self.assertEqual((d["StageReason"], d["BatteryNote"]), ("Mouse not connected", "Mouse not connected"))

    def test_values_are_plain_dbus_types(self):
        d = traymodel.state_dict(state(preset="Balanced"))
        self.assertTrue(all(isinstance(v, (bool, int, str, list)) for v in d.values()))
        self.assertIsInstance(d["StageLabels"], list)

    def test_status_pill(self):
        pill = traymodel.status_pill
        self.assertEqual(pill("running", True), ("Protecting", "good"))
        self.assertEqual(pill("running", False), ("Paused", "amber"))
        self.assertEqual(pill("waiting", True), ("No mouse", "amber"))
        self.assertEqual(pill("permission", True), ("No access", "coral"))
        self.assertEqual(pill("stopped", True), ("Stopped", "coral"))
        self.assertEqual(pill("connecting", True), ("Connecting…", "muted"))

    def test_protection_detail(self):
        detail = traymodel.protection_detail
        self.assertEqual(detail("running", True, "Balanced", 0), "Balanced")
        self.assertEqual(detail("running", False, "Balanced", 3), "Paused · Balanced · 3 dropped")
        self.assertEqual(detail("stopped", True, "Balanced", 3), "Service not running")

    def test_snapshot_reads_the_backends(self):
        class D:
            state, enabled, presetName, dropped = "running", False, "Gentle", 4

        class M:
            online, battery, charging = True, 40, False
            stageLabels, activeStage, stageReason = ["800"], 0, ""
        s = traymodel.snapshot(D, M)
        self.assertEqual((s.daemon_state, s.protecting, s.preset, s.dropped, s.stages, s.battery),
                         ("running", False, "Gentle", 4, ("800",), 40))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ExtensionTests(unittest.TestCase):
    ACTIVE = {"state": 1.0, "enabled": True}
    OFF = {"state": 7.0, "enabled": False}

    def test_active(self):
        self.assertTrue(shellext.is_active(self.ACTIVE))
        self.assertFalse(shellext.is_active(self.OFF))
        self.assertFalse(shellext.is_active({}))
        self.assertFalse(shellext.is_active({"state": 1.0, "enabled": False}))

    def test_enable_only_once_from_a_tray_start(self):
        self.assertTrue(shellext.should_enable(self.OFF, False, True))
        self.assertTrue(shellext.should_enable({"state": 6.0}, False, True))      # installed, not loaded yet
        self.assertFalse(shellext.should_enable(self.OFF, True, True))            # we did it before: user's choice
        self.assertFalse(shellext.should_enable(self.OFF, False, False))          # a normal launch never does
        self.assertFalse(shellext.should_enable(self.ACTIVE, False, True))
        self.assertFalse(shellext.should_enable({}, False, True))                 # the shell doesn't know it yet
        self.assertFalse(shellext.should_enable({"state": 3.0}, False, True))     # broken

    def test_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = shellext.marker_path(tmp)
            self.assertEqual(p, Path(tmp) / "wheelguard" / shellext.MARKER)
            self.assertFalse(p.exists())
            shellext.write_marker(p)
            self.assertTrue(p.exists())


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
