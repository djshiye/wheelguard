"""Low-battery alerts: the once-per-discharge logic, the app settings file and the panel dot."""
import tempfile
import unittest
from pathlib import Path

from wheelguard.gui import traymodel

try:
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    from wheelguard.gui import appsettings, batteryalert
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class NotifyTests(unittest.TestCase):
    def test_returns_the_started_flag_not_the_pyside_tuple(self):
        from unittest import mock
        with mock.patch.object(batteryalert.shutil, "which", return_value="/usr/bin/gdbus"):
            with mock.patch.object(batteryalert.QProcess, "startDetached", return_value=(False, 0)):
                self.assertIs(batteryalert.notify("Low"), False)        # a tuple would be truthy
            with mock.patch.object(batteryalert.QProcess, "startDetached", return_value=(True, 4242)):
                self.assertIs(batteryalert.notify("Low"), True)
        with mock.patch.object(batteryalert.shutil, "which", return_value=None):
            self.assertIs(batteryalert.notify("Low"), False)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = batteryalert.AlertTracker()

    def feed(self, *readings):
        return [self.t.update(True, p, c) for p, c in readings]

    def test_fires_once_at_20_then_once_at_5(self):
        self.assertEqual(self.feed((30, False), (20, False), (19, False), (12, False), (5, False), (4, False)),
                         [None, 20, None, None, 5, None])

    def test_starting_below_a_level_announces_only_the_lower_one(self):
        self.assertEqual(self.feed((4, False), (3, False)), [5, None])

    def test_never_while_charging_and_charging_rearms(self):
        self.assertEqual(self.feed((15, True), (10, True)), [None, None])
        self.assertEqual(self.feed((15, False)), [20])
        self.assertEqual(self.feed((14, True), (15, False)), [None, 20])

    def test_rearms_above_threshold_plus_hysteresis(self):
        self.assertEqual(self.feed((18, False), (24, False), (19, False)), [20, None, None])   # 24 is inside the band
        self.assertEqual(self.feed((26, False), (19, False)), [None, 20])

    def test_offline_or_unknown_battery_is_ignored(self):
        self.assertIsNone(self.t.update(False, 10, False))
        self.assertIsNone(self.t.update(True, -1, False))
        self.assertEqual(self.t.update(True, 10, False), 20)

    def test_text_is_plain(self):
        self.assertEqual(batteryalert.message(18), "Mouse battery low: 18%. Charge it soon.")
        self.assertIn("very low", batteryalert.message(5))

    def test_notification_arguments(self):
        a = batteryalert.notification_args("hi")
        self.assertIn("hi", a)
        self.assertIn("org.freedesktop.Notifications.Notify", a)
        self.assertEqual(a[-1], "-1")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AlertsWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self, alerts=True):
        class FakeMouse(QObject):
            infoChanged = Signal()
            stateChanged = Signal()
            online, battery, charging = True, 50, False

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.mouse = FakeMouse()
        self.settings = appsettings.AppSettings(Path(tmp.name) / "app.json")
        self.settings.setBatteryAlerts(alerts)
        self.sent = []
        self.alerts = batteryalert.BatteryAlerts(self.mouse, self.settings, send=self.sent.append)

    def test_notifies_on_a_battery_change(self):
        self.make()
        self.mouse.battery = 19
        self.mouse.infoChanged.emit()
        self.mouse.infoChanged.emit()
        self.assertEqual(self.sent, ["Mouse battery low: 19%. Charge it soon."])

    def test_switch_off_is_silent(self):
        self.make(alerts=False)
        self.mouse.battery = 4
        self.mouse.infoChanged.emit()
        self.assertEqual(self.sent, [])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AppSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_defaults_persist_and_survive_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "app.json"
            s = appsettings.AppSettings(p)
            self.assertEqual((s.batteryAlerts, s.welcomeSeen), (True, False))
            s.setBatteryAlerts(False)
            s.finishWelcome()
            s2 = appsettings.AppSettings(p)
            self.assertEqual((s2.batteryAlerts, s2.welcomeSeen), (False, True))
            s2.resetWelcome()
            self.assertFalse(appsettings.AppSettings(p).welcomeSeen)
            p.write_text('{"battery_alerts": "no", "welcome_seen": 3}')
            self.assertEqual(appsettings.load(p), appsettings.DEFAULTS)
            p.write_text("not json")
            self.assertEqual(appsettings.load(p), appsettings.DEFAULTS)

    def test_unwritable_folder_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("x")
            s = appsettings.AppSettings(blocker / "app.json")
            s.setBatteryAlerts(False)
            self.assertFalse(s.batteryAlerts)

    def test_welcome_can_be_disabled_for_screenshots(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(appsettings.AppSettings(Path(d) / "a.json", welcome_enabled=False).welcomeSeen)


class DotTests(unittest.TestCase):
    def test_low_dot_rules(self):
        self.assertTrue(traymodel.is_low(True, 20, False))
        self.assertFalse(traymodel.is_low(True, 21, False))
        self.assertFalse(traymodel.is_low(True, 10, True))       # charging
        self.assertFalse(traymodel.is_low(False, 10, False))     # asleep
        self.assertFalse(traymodel.is_low(True, -1, False))      # unknown

    def test_state_dict_carries_it(self):
        s = traymodel.TrayState(online=True, battery=12, stages=("800",), active=0)
        self.assertTrue(traymodel.state_dict(s)["BatteryLow"])
        self.assertFalse(traymodel.state_dict(traymodel.TrayState())["BatteryLow"])


if __name__ == "__main__":
    unittest.main()
