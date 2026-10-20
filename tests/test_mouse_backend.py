"""MouseController (Qt backend of the Mouse page) driven against the fake hidraw device."""
import tempfile
import unittest

from wheelguard.hw import mock, session

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui.mouse import MouseController
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class MouseControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self, mode="connected"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dev = mock.FakeHidraw(mode)
        s = session.MouseSession(lambda: (self.dev, self.dev.wired), tmp.name, lambda t: None)
        c = MouseController(s)
        self.addCleanup(c.shutdown)
        return c

    def wait(self, c, states, timeout=5000):
        loop = QEventLoop()
        c.stateChanged.connect(lambda: loop.quit() if c.state in states else None)
        QTimer.singleShot(timeout, loop.quit)
        if c.state not in states:
            loop.exec()
        self.assertIn(c.state, states)

    def test_read_stage_apply(self):
        c = self.make()
        self.assertEqual(c.state, "idle")
        c.ensureLoaded()
        self.assertEqual(c.state, "reading")
        self.wait(c, ("ready",))
        self.assertEqual((c.connection, c.battery, c.mouseFw, c.dongleFw, c.writable),
                         ("wireless", 82, "1.05", "1.23", True))
        c.setValues({"dpi_x1": 1200, "dpi_y1": 1200})
        c.setValue("polling", 0x04)
        self.assertEqual(c.changeCount, 2)
        self.assertEqual(c.changeSummary, "DPI stage 2, Polling rate")
        self.assertEqual(self.dev.sent[-1][1], 0x12)        # nothing written while editing
        c.discard()
        self.assertEqual(c.changeCount, 0)
        c.setValue("polling", 0x04)
        c.apply()
        self.assertEqual(c.state, "applying")
        self.wait(c, ("ready",))
        self.assertEqual(c.changeCount, 0)
        self.assertEqual(c.messageKind, "ok")
        self.assertTrue(c.hasBackup)
        self.assertEqual(self.dev.blob[0x05], 0x04)

    def test_couplings(self):
        c = self.make()
        c.ensureLoaded()
        self.wait(c, ("ready",))
        c.setValue("glass", True)
        self.assertEqual(c.values["lod"], 1)
        c.setValue("glass", False)
        self.assertEqual(c.values["lod"], 3)
        self.assertEqual(c.changeCount, 0)
        c.setValue("stage_count", 1)
        self.assertEqual(c.values["active_stage"], 0)

    def test_left_click_guard_blocks_apply(self):
        c = self.make()
        c.ensureLoaded()
        self.wait(c, ("ready",))
        c.setValue("button0", 0x0002)
        self.assertNotEqual(c.editProblem, "")
        c.apply()
        self.assertEqual(c.state, "ready")
        self.assertNotIn(0x15, [p[1] for p in self.dev.sent])

    def test_refused_apply_stays_on_the_page_with_edits_kept(self):
        c = self.make()
        c.ensureLoaded()
        self.wait(c, ("ready",))
        self.dev.blob[0x0E] = 0            # the mouse now returns an implausible block
        c.setValue("polling", 0x04)
        c.apply()
        self.wait(c, ("applying",))
        self.wait(c, ("ready",))
        self.assertIn("Refusing", c.message)
        self.assertEqual(c.changeCount, 1)
        self.assertNotIn(0x15, [p[1] for p in self.dev.sent])

    def test_states(self):
        for mode, state in (("asleep", "asleep"), ("unsupported", "unsupported")):
            c = self.make(mode)
            c.refresh()
            self.wait(c, (state,))
        c = self.make("unsupported")
        c.refresh()
        self.wait(c, ("unsupported",))
        c.setValue("polling", 0x04)          # read-only: ignored
        self.assertEqual(c.changeCount, 0)
        self.assertEqual(c.pidText, "0x1983")


if __name__ == "__main__":
    unittest.main()
