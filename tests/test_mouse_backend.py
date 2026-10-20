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

    def make(self, mode="connected", clock=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dev = mock.FakeHidraw(mode)
        s = session.MouseSession(lambda: (self.dev, self.dev.wired), tmp.name, lambda t: None)
        c = MouseController(s, clock=clock) if clock else MouseController(s)
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

    # ---- tray: quick DPI stage switch and quiet refreshes
    def loaded(self, **kw):
        c = self.make(**kw)
        c.ensureLoaded()
        self.wait(c, ("ready",))
        return c

    def writes(self):
        return [p[1] for p in self.dev.sent if p[1] in (0x14, 0x15, 0x16)]

    def test_set_active_stage_writes_only_the_dpi_group(self):
        c = self.loaded()
        self.assertEqual((c.stageCount, c.activeStage, list(c.stageLabels)), (4, 1, ["400", "800", "1600", "3200"]))
        before = bytes(self.dev.blob)
        c.setActiveStage(2)
        self.assertEqual((c.state, c.activeStage), ("applying", 2))      # shown at once
        self.wait(c, ("ready",))
        self.assertEqual(self.writes(), [0x14])
        self.assertEqual(self.dev.blob[0x0D], 2)
        diff = [i for i in range(len(before)) if before[i] != self.dev.blob[i]]
        self.assertEqual(diff, [0x0D])
        self.assertEqual((c.activeStage, c.message, c.changeCount), (2, "", 0))
        self.assertTrue(c.hasBackup)                                     # backup-before-first-write still applies
        c.setActiveStage(2)                                              # already active: nothing is sent
        self.assertEqual(c.state, "ready")
        self.assertEqual(self.writes(), [0x14])

    def test_set_active_stage_ignores_bad_index(self):
        c = self.loaded()
        for i in (-1, 4, 9):
            c.setActiveStage(i)
        self.assertEqual((c.state, self.writes()), ("ready", []))

    def test_staged_edits_block_stage_switch_and_are_not_sent(self):
        c = self.loaded()
        c.setValue("polling", 0x04)
        self.assertEqual(c.stageReason, "Unsaved changes on the Mouse page")
        self.assertFalse(c.stagesEnabled)
        c.setActiveStage(3)
        self.assertEqual((c.state, c.changeCount, self.writes()), ("ready", 1, []))
        c.discard()
        self.assertTrue(c.stagesEnabled)

    def test_read_only_mouse_and_offline_block_stage_switch(self):
        c = self.make("unsupported")
        c.refresh()
        self.wait(c, ("unsupported",))
        self.assertEqual((c.online, c.stageCount, c.stagesEnabled), (False, 0, False))
        c.setActiveStage(1)
        self.assertEqual(self.writes(), [])
        c = self.make("asleep")
        c.refresh()
        self.wait(c, ("asleep",))
        self.assertEqual(c.stageReason, "Mouse not connected")

    def test_battery_refresh_is_quiet_and_reads_only_the_battery(self):
        now = [100.0]
        c = self.loaded(clock=lambda: now[0])
        c.setValue("polling", 0x04)               # staged edit: only the battery may be read
        self.dev.battery = 61
        self.dev.sent.clear()
        states = []
        c.stateChanged.connect(lambda: states.append(c.state))
        c.refreshQuietly()
        loop = QEventLoop()
        c.infoChanged.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        self.assertEqual((c.battery, c.state, states), (61, "ready", []))
        self.assertEqual([p[1] for p in self.dev.sent], [0x0F, 0xB4])
        self.assertEqual(c.changeCount, 1)
        c.refreshQuietly()                         # rate-limited: 20 s
        self.assertEqual([p[1] for p in self.dev.sent], [0x0F, 0xB4])

    def test_menu_and_timer_refresh_reads_only_the_battery(self):
        now = [100.0]
        c = self.loaded(clock=lambda: now[0])
        self.dev.battery = 47
        self.dev.sent.clear()
        c.refreshBattery()
        loop = QEventLoop()
        c.infoChanged.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        self.assertEqual((c.battery, c.state), (47, "ready"))
        self.assertEqual([p[1] for p in self.dev.sent], [0x0F, 0xB4])      # no 0x12 blob read, nothing written

    def test_quiet_poll_picks_up_a_stage_changed_on_the_mouse(self):
        now = [100.0]
        c = self.loaded(clock=lambda: now[0])
        self.dev.blob[0x0D] = 3                    # the mouse's own DPI button
        now[0] += 30
        states = []
        c.stateChanged.connect(lambda: states.append(c.state))
        c.refreshQuietly()
        loop = QEventLoop()
        c.trayChanged.connect(lambda: loop.quit() if c.activeStage == 3 else None)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        self.assertEqual((c.activeStage, states, self.writes()), (3, [], []))

    def test_quiet_poll_recovers_from_a_missing_mouse(self):
        now = [100.0]
        c = self.make("asleep", clock=lambda: now[0])
        c.refresh()
        self.wait(c, ("asleep",))
        self.dev.mode = "connected"
        now[0] += 30
        c.refreshQuietly()
        self.wait(c, ("ready",))
        self.assertTrue(c.online)

    def test_unknown_worker_job_is_an_error_not_a_restore(self):
        c = self.loaded()
        res = []
        loop = QEventLoop()
        c._worker.finished.connect(lambda k, r: (res.append((k, r)), loop.quit()))
        QTimer.singleShot(3000, loop.quit)
        c._submit("bogus")
        loop.exec()
        self.assertIsInstance(res[0][1], ValueError)
        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
