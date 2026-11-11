"""Per-game profiles, Qt side: the controller applies the engine's decisions through the guarded paths, the
service turns the D-Bus input into a validated app id, and the master switch lives in app.json."""
import tempfile
import unittest
from unittest import mock
from pathlib import Path

try:
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    from wheelguard.gui import appsettings, desktopapps, profilectl, profiles, service
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


if HAVE_QT:
    class FakeDaemon(QObject):
        """Stands in for the service: saved settings, plus the temporary ones a profile puts on top."""
        configChanged = Signal()
        stateChanged = Signal()
        statsChanged = Signal()
        clickLockActiveChanged = Signal()
        reconnected = Signal()

        def __init__(self):
            super().__init__()
            self.state, self.enabled, self.presetName, self.dropped = "running", True, "Balanced", 0
            self.saved = {"pre_ms": 60, "hold_ms": 25, "lock_ms": 300, "clicklock": False}
            self.override = {}
            self.clickLockMs, self.clickLockActive, self.clickLockMode = 5000, False, "repeat"
            self.sets = []          # what was written to the saved settings
            self.overrides = []     # every change of the temporary settings
            self.flushed = 0
            self._show()

        def _show(self):
            eff = {**self.saved, **self.override}
            self.preMs, self.holdMs, self.lockMs, self.clickLock = (eff["pre_ms"], eff["hold_ms"], eff["lock_ms"],
                                                                    eff["clicklock"])

        @property
        def protection(self):
            return (self.preMs, self.holdMs, self.lockMs)

        def setValues(self, patch):
            self.sets.append(dict(patch))
            self.saved.update(patch)
            for k in patch:                 # like the service: a hand change ends the temporary value
                self.override.pop(k, None)
            self._show()
            self.configChanged.emit()

        def setOverride(self, config):
            if dict(config) == self.override:
                return
            self.override = dict(config)
            self.overrides.append(dict(config))
            self._show()
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
        # Keep the tests independent of the apps installed on the machine running them.
        apps = mock.patch.object(desktopapps, "list_apps", return_value=[])
        apps.start()
        self.addCleanup(apps.stop)
        self.d, self.m = FakeDaemon(), FakeMouse()
        self.settings = appsettings.AppSettings(self.home / "wheelguard" / "app.json")
        self.store = profiles.ProfileStore(self.home / "wheelguard" / profiles.FILE)
        self.store.save(profiles.Profile("game.desktop", "Strong", True, 0))
        self.ctl = self.make()

    def make(self):
        return profilectl.ProfileController(self.d, self.m, self.settings, None, config_home=self.home)

    GAME = {"pre_ms": 90, "hold_ms": 35, "lock_ms": 400, "clicklock": True}

    def test_the_switch_is_off_by_default_and_focus_is_ignored(self):
        self.assertFalse(self.settings.profilesEnabled)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(self.d.overrides + self.d.sets + self.m.calls, [])
        self.assertEqual(self.ctl.activeId, "")

    def test_engage_and_restore(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(self.d.overrides, [self.GAME])
        self.assertEqual((self.d.protection, self.d.clickLock), ((90, 35, 400), True))
        self.assertEqual(self.m.calls, [("stage", 0)])
        self.assertEqual((self.ctl.activeId, self.ctl.activeName), ("game.desktop", "game"))
        self.m.calls.clear()
        self.ctl.focusChanged("")
        self.assertEqual(self.d.overrides, [self.GAME, {}])
        self.assertEqual((self.d.protection, self.d.clickLock), ((60, 25, 300), False))
        self.assertEqual(self.m.calls, [("stage", 2)])
        self.assertEqual(self.ctl.activeId, "")
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def test_a_profile_never_writes_the_saved_settings(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.ctl.focusChanged("")
        self.ctl.focusChanged("game.desktop")
        self.ctl.shutdown()
        self.assertEqual(self.d.sets, [])
        self.assertEqual(self.d.saved, {"pre_ms": 60, "hold_ms": 25, "lock_ms": 300, "clicklock": False})

    def test_nothing_is_sent_twice(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        n = len(self.d.overrides) + len(self.m.calls)
        self.ctl.focusChanged("game.desktop")
        self.d.configChanged.emit()
        self.assertEqual(len(self.d.overrides) + len(self.m.calls), n)

    def test_a_busy_mouse_skips_the_dpi_switch_and_it_is_tried_again_when_ready(self):
        self.m.stagesEnabled = False
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.assertEqual(self.m.calls, [])
        self.assertEqual(self.d.overrides, [self.GAME])
        self.m.stagesEnabled = True
        self.m.trayChanged.emit()
        self.assertEqual(self.m.calls, [("stage", 0)])

    def test_a_hand_change_while_engaged_is_kept(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.d.setValues({"clicklock": False})      # the user switches Click-Lock off by hand
        self.assertFalse(self.d.clickLock)
        self.assertEqual(self.d.override, {"pre_ms": 90, "hold_ms": 35, "lock_ms": 400})
        self.ctl.focusChanged("")
        self.assertEqual((self.d.protection, self.d.clickLock), ((60, 25, 300), False))
        self.assertEqual(self.d.sets, [{"clicklock": False}])     # only the user's own change was saved

    def test_switching_the_master_off_hands_the_settings_back(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.settings.setProfilesEnabled(False)
        self.assertEqual(self.d.override, {})
        self.assertEqual((self.d.protection, self.d.clickLock), ((60, 25, 300), False))
        self.assertEqual(self.ctl.activeId, "")

    def test_switching_the_master_on_picks_up_the_focused_app(self):
        self.ctl.focusChanged("game.desktop")
        self.settings.setProfilesEnabled(True)
        self.assertEqual(self.ctl.activeId, "game.desktop")

    def test_shutdown_restores_and_flushes(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.ctl.shutdown()
        self.assertEqual(self.d.override, {})
        self.assertEqual(self.m.calls[-1], ("stage", 2))
        self.assertEqual(self.d.flushed, 1)
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def journal(self):
        return profiles.journal_load(self.home / "wheelguard" / profiles.JOURNAL_FILE)

    def test_only_the_dpi_stage_needs_a_crash_journal(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")       # ... and then the app dies (no shutdown)
        self.assertEqual(self.journal(), {"stage": 2})
        self.d.override = {}                        # the service drops temporary settings when the app goes away
        self.d._show()
        self.m.calls.clear()
        again = self.make()
        self.assertEqual(self.m.calls, [("stage", 2)])
        self.assertEqual(self.d.sets, [])
        self.assertEqual(again.activeId, "")
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def test_a_journal_from_before_temporary_settings_is_still_put_right(self):
        """Up to 1.6.2 a profile wrote its values into the saved settings, and the journal held the user's own."""
        profiles.journal_save(self.home / "wheelguard" / profiles.JOURNAL_FILE,
                              {"protection": (60, 25, 300), "clicklock": False, "stage": 2})
        self.d.saved.update(self.GAME)
        self.d._show()
        self.m.activeStage = 0
        again = self.make()
        self.assertEqual(self.d.sets, [{"pre_ms": 60, "hold_ms": 25, "lock_ms": 300, "clicklock": False}])
        self.assertEqual(self.m.calls, [("stage", 2)])
        self.assertEqual(again.activeId, "")
        self.assertFalse((self.home / "wheelguard" / profiles.JOURNAL_FILE).exists())

    def test_the_temporary_settings_come_back_after_the_service_restarts(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.d.reconnected.emit()                   # the real client sends the override again by itself here
        self.d.override = {}                        # ... and for a moment the service shows the saved values
        self.d._show()
        self.d.configChanged.emit()
        self.assertEqual(self.d.override, self.GAME)        # not mistaken for a change by hand
        self.assertEqual(self.ctl.activeId, "game.desktop")

    def test_a_dpi_stage_that_could_not_be_handed_back_stays_in_the_journal_at_shutdown(self):
        self.settings.setProfilesEnabled(True)
        self.ctl.focusChanged("game.desktop")
        self.m.stagesEnabled = False                # the mouse went to sleep: the stage can't be set now
        self.ctl.shutdown()
        self.assertEqual(self.journal(), {"stage": 2})

    def test_recent_apps_without_a_launcher_can_get_a_profile(self):
        self.ctl._apps = [{"id": "known.desktop", "name": "Known", "icon": ""}]
        for app in ("heroic-hades.exe", "known.desktop", "game.desktop", "io.github.djshiye.Wheelguard.desktop",
                    "lutris-witcher", "heroic-hades.exe"):
            self.ctl.focusChanged(app)
        self.assertEqual([a["id"] for a in self.ctl.recentApps()], ["heroic-hades.exe", "lutris-witcher"])
        self.assertEqual(self.ctl.saveProfile("lutris-witcher", "Strong", "keep", -1), "")
        self.assertEqual([a["id"] for a in self.ctl.recentApps()], ["heroic-hades.exe"])

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
        self.ctl.saveProfile("game.desktop", "Maximum", "keep", -1)
        self.assertEqual(self.d.protection, (130, 50, 500))
        self.assertFalse(self.d.clickLock)          # no longer part of the profile: back to the user's

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
        for value in ("steam_app_1.desktop", "", "x" * 5000, "Portal 2.desktop", " lead", "../../etc/passwd", "a\x00b"):
            svc.focus_changed(value)
        self.assertEqual(got, ["steam_app_1.desktop", "", "", "Portal 2.desktop", "", "", ""])

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
