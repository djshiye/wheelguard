"""Command-line flags of wheelguard-settings, and the first-run welcome wiring in the QML."""
import unittest
from pathlib import Path

from wheelguard.gui import app

QML = Path(app.QML_DIR)


class ArgsTests(unittest.TestCase):
    def test_shortcut_flags(self):
        a = app.parse_args(["--toggle-clicklock"])
        self.assertEqual((a.toggle_clicklock, a.next_dpi_stage), (True, False))
        a = app.parse_args(["--next-dpi-stage", "--socket", "/tmp/x.sock"])
        self.assertEqual((a.next_dpi_stage, a.socket), (True, "/tmp/x.sock"))

    def test_welcome_flag_for_screenshots(self):
        self.assertIsNone(app.parse_args([]).welcome)
        self.assertEqual(app.parse_args(["--welcome"]).welcome, 0)
        self.assertEqual(app.parse_args(["--welcome", "2"]).welcome, 2)


class WelcomeWiringTests(unittest.TestCase):
    """The overlay is registered, mounted once in the window, and re-openable from About."""

    def test_registered_and_mounted(self):
        self.assertIn("WelcomeOverlay 1.0 WelcomeOverlay.qml", (QML / "Wg" / "qmldir").read_text())
        self.assertEqual((QML / "Main.qml").read_text().count("WelcomeOverlay {"), 1)

    def test_it_follows_the_saved_flag_and_about_can_reopen_it(self):
        overlay = (QML / "Wg" / "WelcomeOverlay.qml").read_text()
        self.assertIn("appSettings.welcomeSeen", overlay)
        self.assertIn("appSettings.finishWelcome()", overlay)
        self.assertIn("appSettings.resetWelcome()", (QML / "Wg" / "AboutPage.qml").read_text())


if __name__ == "__main__":
    unittest.main()
