"""Per-game profiles, Qt side: the controller applies the engine's decisions through the guarded paths, the
service turns the D-Bus input into a validated app id, and the master switch lives in app.json."""
import tempfile
import unittest
from pathlib import Path

try:
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    from wheelguard.gui import appsettings, profilectl, profiles, service
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


if HAVE_QT:
    class FakeDaemon(QObject):
        configChanged = Signal()
        stateChanged = Signal()
        statsChanged = Signal()
        clickLockActiveChanged = Signal()

        def __init__(self):
            super().__init__()
            self.state, self.enabled, self.presetName, self.dropped = "running", True, "Balanced", 0
            self.preMs, self.holdMs, self.lockMs = 60, 25, 300
            self.clickLock, self.clickLockMs, self.clickLockActive, self.clickLockMode = False, 5000, False, "repeat"
            self.calls = []
            self.flushed = 0

        def setProtectionValues(self, pre, hold, lock):
            self.calls.append(("protection", pre, hold, lock))
            self.preMs, self.holdMs, self.lockMs = pre, hold, lock
            self.configChanged.emit()

        def setClickLock(self, on):
            self.calls.append(("clicklock", on))
            self.clickLock = on
            self.configChanged.emit()

        def flushSocket(self):
            self.flushed += 1

    class FakeMouse(QObject):
        trayChanged = Signal()

        def __init__(self):
            super().__init__()
            self.online, self.battery, self.charging = True, 80, False
            self.stageLabels, self.activeStage, self.stageReason = ["400", "800", "1600"], 2, ""
            self.stagesEnabled = True
            self.calls = []

        def setActiveStage(self, i):
            self.calls.append(("stage", i))
            self.activeStage = i
            self.trayChanged.emit()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.d, self.m = FakeDaemon(), FakeMouse()
        self.settings = appsettings.AppSettings(self.home / "wheelguard" / "app.json")
        self.store = profiles.ProfileStore(self.home / "wheelguard" / profiles.FILE)
        self.store.save(profiles.Profile("game.desktop", "Strong", True, 0))
        self.ctl = self.make()

    def make(self):
        return profilectl.ProfileController(self.d, self.m, self.settings, None, config_home=self.home)

    def test_the_switch_is_off_by_default_and_focus_is_ignored(self):
        self.assertFalse(self.settings.profilesEnabled)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(self.d.calls + self.m.calls, [])
        self.assertEqual(self.ctl.activeId, "")

    def test_engage_and_restore_through_the_guarded_paths(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(self.d.calls, [("protection", 90, 35, 400), ("clicklock", True)])
        self.assertEqual(self.m.calls, [("stage", 0)])
        self.assertEqual((self.ctl.activeId, self.ctl.activeName), ("game.desktop", "game"))
        self.assertTrue((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())
        self.d.calls.clear()
        self.m.calls.clear()
        self.ctl.focusChanged("")
        self.assertEqual(self.d.calls, [("protection", 60, 25, 300), ("clicklock", False)])
        self.assertEqual(self.m.calls, [("stage", 2)])
        self.assertEqual(self.ctl.activeId, "")
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def test_nothing_is_sent_twice(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        n = len(self.d.calls) + len(self.m.calls)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(len(self.d.calls) + len(self.m.calls), n)

    def test_a_busy_mouse_skips_the_dpi_switch_and_it_is_tried_again_when_ready(self):
        self.m.stagesEnabled = False
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(self.m.calls, [])
        self.assertEqual(len(self.d.calls), 2)
        self.m.stagesEnabled = True
        self.m.trayChanged.emit()
        self.assertEqual(self.m.calls, [("stage", 0)])

    def test_a_hand_change_while_engaged_is_not_undone(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.d.clickLock = False                    # the user switches Click-Lock off by hand
        self.d.configChanged.emit()
        self.d.calls.clear()
        self.ctl.focusChanged("")
        self.assertNotIn(("clicklock", False), self.d.calls)
        self.assertIn(("protection", 60, 25, 300), self.d.calls)

    def test_switching_the_master_off_hands_the_settings_back(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.d.calls.clear()
        self.settings.setProfilesEnabled(False)
        self.assertEqual(self.d.calls, [("protection", 60, 25, 300), ("clicklock", False)])
        self.assertEqual(self.ctl.activeId, "")

    def test_switching_the_master_on_picks_up_the_focused_app(self):
        self.ctl.focusChanged("game.desktop")
        self.settings.setProfilesEnabled(True)
        self.assertEqual(self.ctl.activeId, "game.desktop")

    def test_shutdown_restores_and_flushes(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.d.calls.clear()
        self.ctl.shutdown()
        self.assertEqual(self.d.calls[0], ("protection", 60, 25, 300))
        self.assertEqual(self.d.flushed, 1)
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def test_a_crashed_run_is_put_right_at_the_next_start(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")       # ... and then the app dies (no shutdown)
        self.assertTrue((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())
        self.d.calls.clear()
        self.m.calls.clear()
        again = self.make()                         # the daemon still holds the game's values
        self.assertEqual(self.d.calls, [("protection", 60, 25, 300), ("clicklock", False)])
        self.assertEqual(self.m.calls, [("stage", 2)])
        self.assertEqual(again.activeId, "")
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def test_saving_and_deleting_profiles(self):
        self.assertEqual(self.ctl.saveProfile("new.desktop", "keep", "keep", -1), "Choose at least one change for this app.")
        self.assertEqual(self.ctl.saveProfile("new.desktop", "Light", "keep", 1), "")
        self.assertEqual(self.ctl.count, 2)
        item = next(i for i in self.ctl.items if i["appId"] == "new.desktop")
        self.assertEqual((item["summary"], item["stage"], item["clicklock"]), ("Light protection · DPI stage 2", 1, "keep"))
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("new.desktop")
        self.assertEqual(self.m.calls, [("stage", 1)])
        self.m.calls.clear()
        self.ctl.removeProfile("new.desktop")       # deleting the engaged profile hands settings back
        self.assertEqual(self.m.calls, [("stage", 2)])
        self.assertEqual(self.ctl.count, 1)

    def test_editing_the_engaged_profile_applies_at_once(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.d.calls.clear()
        self.ctl.saveProfile("game.desktop", "Maximum", "keep", -1)
        self.assertIn(("protection", 130, 50, 500), self.d.calls)
        self.assertIn(("clicklock", False), self.d.calls)       # no longer part of the profile: back to the user's

    def test_the_app_list_leaves_out_apps_that_already_have_a_profile(self):
        self.ctl._apps = [{"id": "game.desktop", "name": "Game", "icon": ""},
                          {"id": "other.desktop", "name": "Other", "icon": ""}]
        self.assertEqual([a["id"] for a in self.ctl.installedApps("")], ["other.desktop"])
        self.assertEqual(self.ctl.installedApps("zzz"), [])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ServiceFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_dbus_input_is_validated_before_it_reaches_the_controller(self):
        svc = service.Service()
        got = []
        svc.focusChanged.connect(got.append)
        for value in ("steam_app_1.desktop", "", "x" * 5000, "has space.desktop", "../../etc/passwd", "a\x00b"):
            svc.focus_changed(value)
        self.assertEqual(got, ["steam_app_1.desktop", "", "", "", "", ""])

    def test_the_popup_state_carries_the_profile_flags(self):
        svc = service.Service()
        self.assertEqual((svc.current()["ProfilesEnabled"], svc.current()["ActiveProfile"]), (False, ""))
        self.assertIn("ProfilesEnabled", service.PROPERTIES)
        self.assertIn("ActiveProfile", service.PROPERTIES)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SwitchTests(unittest.TestCase):
    def test_master_switch_defaults_off_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app.json"
            s = appsettings.AppSettings(p)
            self.assertFalse(s.profilesEnabled)
            s.setProfilesEnabled(True)
            self.assertTrue(appsettings.AppSettings(p).profilesEnabled)
            p.write_text('{"profiles_enabled": "yes"}')
            self.assertFalse(appsettings.AppSettings(p).profilesEnabled)


if __name__ == "__main__":
    unittest.main()
