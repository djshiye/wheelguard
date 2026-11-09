import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.clicklock import DRAG_COUNTS, HOLD, REPEAT, ClickLock
from wheelguard.control import Controller

LEFT = e.BTN_LEFT


def key(v):
    return [(e.EV_KEY, LEFT, v)]


def move(dx=0, dy=0):
    return [(e.EV_REL, e.REL_X, dx), (e.EV_REL, e.REL_Y, dy)]


def values(frames):
    return [ev[2] for f in frames for ev in f if ev[:2] == (e.EV_KEY, LEFT)]


class Drag(unittest.TestCase):
    def make(self, **kw):
        args = dict(enabled=True, hold_ms=2000, mode=REPEAT, rate=10, max_s=0)
        args.update(kw)
        return ClickLock(**args)

    def test_holding_still_engages(self):
        cl = self.make()
        cl.process(key(1), 0.0)
        for i in range(1, 20):                          # hand tremor: a few counts back and forth
            cl.process(move(2 * (-1) ** i, 1), i * 0.05)
        _, changed = cl.flush(2.0)
        self.assertTrue(changed and cl.active)

    def test_a_drag_stops_this_press_from_engaging(self):
        cl = self.make()
        cl.process(key(1), 0.0)
        cl.process(move(60, 0), 0.5)
        cl.process(move(60, 0), 0.6)                    # 120 counts away from where it was pressed
        self.assertIsNone(cl.deadline)
        self.assertEqual(cl.flush(10.0), ([], False))
        self.assertFalse(cl.active)
        _, rest, _ = cl.process(key(0), 10.5)           # the release goes through: nothing stuck
        self.assertEqual(values([rest]), [0])
        self.assertFalse(cl.virtual_down)

    def test_still_afterwards_does_not_bring_it_back(self):
        cl = self.make()
        cl.process(key(1), 0.0)
        cl.process(move(DRAG_COUNTS + 1), 0.1)
        cl.process(move(-(DRAG_COUNTS + 1)), 0.2)       # dragged away and back: still a drag
        self.assertEqual(cl.flush(5.0), ([], False))

    def test_diagonal_motion_counts_as_distance(self):
        cl = self.make()
        cl.process(key(1), 0.0)
        cl.process(move(75, 75), 0.1)                   # ~106 counts
        self.assertIsNone(cl.deadline)

    def test_next_press_starts_fresh(self):
        cl = self.make()
        cl.process(key(1), 0.0)
        cl.process(move(500), 0.1)
        cl.process(key(0), 0.2)
        cl.process(move(500), 0.3)                      # moving with the button up is no business of ours
        cl.process(key(1), 1.0)
        self.assertEqual(cl.deadline, 3.0)
        _, changed = cl.flush(3.0)
        self.assertTrue(changed)

    def test_motion_in_the_press_frame_does_not_count(self):
        cl = self.make()
        cl.process(move(500) + key(1), 0.0)
        self.assertEqual(cl.deadline, 2.0)

    def test_hold_mode_keeps_todays_behaviour(self):
        cl = self.make(mode=HOLD)
        cl.process(key(1), 0.0)
        cl.process(move(5000), 0.5)
        _, changed = cl.flush(2.0)
        self.assertTrue(changed and cl.active)

    def test_option_off_engages_even_when_dragging(self):
        cl = self.make(ignore_drag=False)
        cl.process(key(1), 0.0)
        cl.process(move(5000), 0.5)
        _, changed = cl.flush(2.0)
        self.assertTrue(changed and cl.active)

    def test_motion_after_engaging_is_ignored(self):
        cl = self.make()
        cl.process(key(1), 0.0)
        cl.flush(2.0)
        cl.process(move(5000), 3.0)
        self.assertTrue(cl.active)


class Config(unittest.TestCase):
    def test_default_validation_and_old_files(self):
        self.assertTrue(C.DEFAULTS["clicklock_ignore_drag"])
        self.assertFalse(C.normalize({"clicklock_ignore_drag": False})["clicklock_ignore_drag"])
        for bad in (1, "yes", None):
            with self.assertRaises(ValueError):
                C.normalize({"clicklock_ignore_drag": bad})

    def test_controller_applies_live(self):
        with tempfile.TemporaryDirectory() as d:
            ctl = Controller(config_path=Path(d) / "c.json", clock=lambda: 0.0)
            out = []
            ctl.set_config({"clicklock": True, "clicklock_ms": 1000})
            ctl.handle_frame(key(1), 1.0, out.extend)
            ctl.handle_frame(move(300), 1.2, out.extend)
            ctl.handle_flush(3.0, out.extend)
            self.assertFalse(ctl.cl.active)
            ctl.set_config({"clicklock_ignore_drag": False})
            ctl.handle_frame(key(0), 3.1, out.extend)
            ctl.handle_frame(key(1), 4.0, out.extend)
            ctl.handle_frame(move(300), 4.2, out.extend)
            ctl.handle_flush(5.0, out.extend)
            self.assertTrue(ctl.cl.active)


if __name__ == "__main__":
    unittest.main()
