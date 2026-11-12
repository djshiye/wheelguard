"""The app side of 1.10.0: the Scroll speed slider's setter and Click-Lock's button following GNOME's
left-handed switch."""
import unittest

from wheelguard.gui import protocol

try:
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    from wheelguard.gui import backend
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class DefaultsTests(unittest.TestCase):
    def test_the_app_and_the_service_agree(self):
        from wheelguard import clicklock, config, debounce
        from wheelguard.control import BUTTON_NAMES
        for key in ("scroll_speed", "clicklock_button"):
            self.assertEqual(protocol.CONFIG_DEFAULTS[key], config.DEFAULTS[key])
        self.assertEqual(protocol.SCROLL_SPEED_RANGE, config.SCROLL_SPEED)
        self.assertEqual([n for n, _ in protocol.DEBOUNCE_BUTTONS], [BUTTON_NAMES[b] for b in debounce.BUTTONS])
        self.assertEqual(set(clicklock.BUTTONS), {"left", "right"})


if HAVE_QT:
    class Daemon(QObject):
        stateChanged = Signal()
        configChanged = Signal()

        def __init__(self, button="left", refuses=False):
            super().__init__()
            self.configKnown, self.clickLockButton, self.refuses, self.asked = True, button, refuses, []

        def set_clicklock_button(self, button):
            self.asked.append(button)
            if not self.refuses:
                self.clickLockButton = button
            if len(self.asked) < 50:
                self.configChanged.emit()

    class Pointer(QObject):
        changed = Signal()
        loadedChanged = Signal()
        loaded, leftHanded = False, False

        def read(self, left_handed):
            self.leftHanded, self.loaded = left_handed, True
            self.loadedChanged.emit()
            self.changed.emit()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class HandFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_nothing_is_asked_before_gnome_has_answered(self):
        d, p = Daemon(button="right"), Pointer()
        follow = backend.HandFollow(d, p)
        self.assertEqual(d.asked, [])               # the left-handed switch isn't known yet: "right" stays
        p.read(True)
        self.assertEqual(d.asked, [])               # and it was right all along
        del follow

    def test_the_button_follows_the_switch_both_ways(self):
        d, p = Daemon(), Pointer()
        follow = backend.HandFollow(d, p)
        p.read(False)
        self.assertEqual(d.asked, [])
        p.read(True)
        self.assertEqual(d.asked, ["right"])
        p.read(False)
        self.assertEqual(d.asked, ["right", "left"])
        del follow

    def test_a_refusal_is_not_asked_again(self):
        d, p = Daemon(refuses=True), Pointer()
        follow = backend.HandFollow(d, p)
        p.read(True)
        d.stateChanged.emit()
        self.assertEqual(d.asked, ["right"])
        del follow

    def test_without_the_service_nothing_is_asked(self):
        d, p = Daemon(), Pointer()
        d.configKnown = False
        follow = backend.HandFollow(d, p)
        p.read(True)
        self.assertEqual(d.asked, [])
        d.configKnown = True
        d.stateChanged.emit()
        self.assertEqual(d.asked, ["right"])
        del follow


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.c = backend.DaemonClient("/nonexistent/wheelguard.sock")
        self.c._state = "running"
        self.sent = []
        self.c._send = lambda msg, track=None: self.sent.append(msg) or True

    def status(self, **config):
        self.c._on_status({"connected": True, "device": "Mouse", "config": config})

    def test_scroll_speed_is_clamped_to_the_slider(self):
        self.status(scroll_speed=100)
        self.assertEqual(self.c.scrollSpeed, 100)
        for asked, staged in ((150, 150), (20, 50), (900, 300)):
            self.c.setScrollSpeed(asked)
            self.assertEqual(self.c.scrollSpeed, staged)
        self.c._send_staged()
        self.assertEqual(self.sent, [{"cmd": "set", "config": {"scroll_speed": 300}}])

    def test_an_older_service_is_not_asked_for_the_button(self):
        self.status(clicklock=True)                 # 1.9: no clicklock_button in its config
        self.c.set_clicklock_button("right")
        self.assertEqual(self.sent, [])
        self.status(clicklock=True, clicklock_button="left")
        self.c.set_clicklock_button("middle")
        self.assertEqual(self.sent, [])
        self.c.set_clicklock_button("right")
        self.assertEqual(self.sent, [{"cmd": "set", "config": {"clicklock_button": "right"}}])
        self.assertEqual(self.c.clickLockButton, "right")


if __name__ == "__main__":
    unittest.main()
