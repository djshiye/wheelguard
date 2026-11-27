"""Edits made for one mouse must never be written to another one that answers instead (the first was
unplugged, or the preferred mouse changed between reading the page and pressing Apply)."""
import tempfile
import unittest

from wheelguard.hw import mock
from wheelguard.hw.session import MouseChanged, MouseSession


class SwappedMouse(unittest.TestCase):
    def setUp(self):
        self.endgame, self.razer = mock.FakeHidraw("connected-wired"), mock.FakeRazer("razer-wired")
        self.current = "endgame"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = MouseSession(opener=self.open, backup_dir=tmp.name, sleep=lambda _s: None)
        self.shown = self.s.read().model             # what the page shows

    def open(self):
        if self.current == "endgame":
            return self.endgame, True
        return self.razer, True, "razer", self.razer.pid

    def test_apply_refuses_another_mouse(self):
        before = (list(self.razer.stages), self.razer.active)
        self.current = "razer"
        with self.assertRaises(MouseChanged) as cm:
            self.s.apply({"dpi_x0": 1200, "dpi_y0": 1200, "active_stage": 0}, expect=self.shown)
        self.assertEqual((list(self.razer.stages), self.razer.active), before)
        self.assertNotEqual(cm.exception.snapshot.model, self.shown)

    def test_restore_refuses_another_mouse(self):
        self.current = "razer"
        with self.assertRaises(MouseChanged):
            self.s.restore(expect=self.shown)

    def test_same_mouse_still_applies(self):
        res = self.s.apply({"dpi_x0": 1200, "dpi_y0": 1200}, expect=self.shown)
        self.assertEqual(res.snapshot.model, self.shown)


if __name__ == "__main__":
    unittest.main()
