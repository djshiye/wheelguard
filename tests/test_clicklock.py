import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.clicklock import HOLD, REPEAT, ClickLock
from wheelguard.control import Controller

SCAN = (e.EV_MSC, e.MSC_SCAN, 0x90001)


def press(v):
    return [SCAN, (e.EV_KEY, e.BTN_LEFT, v)]


def key(v):
    return [(e.EV_KEY, e.BTN_LEFT, v)]


def values(frames):
    """The left-button values in a list of frames, in order."""
    return [ev[2] for f in frames for ev in f if ev[:2] == (e.EV_KEY, e.BTN_LEFT)]


class Engine(unittest.TestCase):
    def make(self, **kw):
        args = dict(enabled=True, hold_ms=5000, mode=REPEAT, rate=10)
        args.update(kw)
        return ClickLock(**args)

    def test_short_click_passes_untouched(self):
        cl = self.make()
        frames, rest, changed = cl.process(press(1), 1.0)
        self.assertEqual((frames, rest, changed), ([], press(1), False))
        self.assertEqual(cl.deadline, 6.0)
        frames, rest, changed = cl.process(press(0), 1.2)
        self.assertEqual((frames, rest, changed), ([], press(0), False))
        self.assertIsNone(cl.deadline)
        self.assertEqual(cl.flush(10.0), ([], False))

    def test_other_events_pass(self):
        cl = self.make()
        motion = [(e.EV_REL, e.REL_X, 3)]
        self.assertEqual(cl.process(motion, 1.0), ([], motion, False))
        right = [(e.EV_KEY, e.BTN_RIGHT, 1)]
        self.assertEqual(cl.process(right, 1.0), ([], right, False))

    def test_disabled_never_engages(self):
        cl = self.make(enabled=False)
        cl.process(press(1), 1.0)
        self.assertIsNone(cl.deadline)
        self.assertEqual(cl.flush(100.0), ([], False))
        self.assertEqual(cl.process(press(0), 100.0)[1], press(0))

    def test_not_yet(self):
        cl = self.make()
        cl.process(press(1), 1.0)
        self.assertEqual(cl.flush(5.99), ([], False))
        self.assertFalse(cl.active)

    def test_repeat_mode_full_cycle(self):
        cl = self.make()
        cl.process(press(1), 1.0)
        frames, changed = cl.flush(6.0)                 # held 5 s: lock engages, the held click completes
        self.assertTrue(changed and cl.active)
        self.assertEqual(values(frames), [0])
        self.assertAlmostEqual(cl.deadline, 6.05)
        frames, rest, changed = cl.process(press(0), 6.02)   # letting go is ignored
        self.assertEqual((values(frames), rest, changed), ([], [], False))
        clicks = []
        t = 6.05
        for _ in range(6):                              # 10 clicks/s: flips every 50 ms
            frames, changed = cl.flush(t)
            self.assertFalse(changed)
            clicks += values(frames)
            t += 0.05
        self.assertEqual(clicks, [1, 0, 1, 0, 1, 0])
        t -= 0.025                                      # between flips, with the button up
        frames, rest, changed = cl.process(press(1), t)     # click again: the lock ends
        self.assertTrue(changed)
        self.assertFalse(cl.active)
        self.assertEqual(values([rest]), [])            # button already up: the press is swallowed
        self.assertNotIn(SCAN, rest)
        frames, rest, changed = cl.process(press(0), t + 0.1)
        self.assertEqual((values([rest]), changed), ([], False))   # and so is its release
        self.assertEqual(cl.flush(t + 5), ([], False))
        self.assertEqual(cl.process(press(1), t + 6)[1], press(1))  # normal again

    def test_repeat_stop_while_virtually_down_releases(self):
        cl = self.make()
        cl.process(press(1), 1.0)
        cl.flush(6.0)
        cl.process(press(0), 6.01)
        cl.flush(6.05)                                  # virtual button is down now
        _, rest, _ = cl.process(press(1), 6.07)
        self.assertEqual(values([rest]), [0])           # the stopping press becomes the release
        _, rest, _ = cl.process(press(0), 6.2)
        self.assertEqual(values([rest]), [])

    def test_hold_mode_keeps_button_down(self):
        cl = self.make(mode=HOLD)
        cl.process(press(1), 1.0)
        frames, changed = cl.flush(6.0)
        self.assertEqual((values(frames), changed, cl.active), ([], True, True))
        self.assertIsNone(cl.deadline)                  # nothing to drive
        _, rest, _ = cl.process(press(0), 7.0)
        self.assertEqual(values([rest]), [])            # still held
        _, rest, changed = cl.process(press(1), 9.0)
        self.assertEqual((values([rest]), changed, cl.active), ([], True, False))
        _, rest, _ = cl.process(press(0), 9.1)
        self.assertEqual(values([rest]), [0])           # this click lets go

    def test_stall_does_not_burst(self):
        cl = self.make()
        cl.process(press(1), 1.0)
        cl.flush(6.0)
        frames, _ = cl.flush(9.0)                       # 3 s late
        self.assertEqual(values(frames), [1])
        self.assertAlmostEqual(cl.deadline, 9.05)

    def test_stop_releases_virtual_button(self):
        cl = self.make()
        cl.process(press(1), 1.0)
        cl.flush(6.0)
        cl.process(press(0), 6.01)
        cl.flush(6.05)                                  # down
        frames, was = cl.stop()
        self.assertTrue(was)
        self.assertEqual(values(frames), [0])
        self.assertFalse(cl.active)
        self.assertIsNone(cl.deadline)
        self.assertEqual(cl.process(press(1), 7.0)[1], press(1))

    def test_stop_while_finger_still_down_hands_it_back(self):
        cl = self.make(mode=HOLD)
        cl.process(press(1), 1.0)
        cl.flush(6.0)
        frames, was = cl.stop()                         # finger and virtual button both down
        self.assertEqual((frames, was), ([], True))
        self.assertEqual(values([cl.process(press(0), 7.0)[1]]), [0])

    def test_stop_when_idle_does_nothing(self):
        cl = self.make()
        self.assertEqual(cl.stop(), ([], False))


    def test_other_button_ends_repeat_lock(self):
        cl = self.make(hold_ms=1000)
        cl.process(press(1), 1.0)
        cl.flush(2.0)                                   # engaged, button released
        cl.process(press(0), 2.02)                      # finger off; the lock carries on
        cl.flush(2.05)                                  # virtual down (repeat click)
        self.assertTrue(cl.virtual_down)
        right = [(e.EV_KEY, e.BTN_RIGHT, 1)]
        frames, rest, changed = cl.process(right, 2.06)
        self.assertTrue(changed)
        self.assertFalse(cl.active)
        self.assertEqual(values(frames), [0])           # left let go
        self.assertEqual(rest, right)                   # the right click itself still lands
        self.assertEqual(cl.flush(10.0), ([], False))   # and nothing clicks any more

    def test_other_button_ends_hold_lock(self):
        cl = self.make(hold_ms=1000, mode=HOLD)
        cl.process(press(1), 1.0)
        cl.flush(2.0)
        cl.process(press(0), 2.5)                       # finger off, button still held for the user
        frames, rest, changed = cl.process([(e.EV_KEY, e.BTN_MIDDLE, 1)], 3.0)
        self.assertEqual(values(frames), [0])
        self.assertFalse(cl.active)
        self.assertFalse(cl.virtual_down)

    def test_other_button_when_not_locked_is_untouched(self):
        cl = self.make()
        right = [(e.EV_KEY, e.BTN_RIGHT, 1)]
        self.assertEqual(cl.process(right, 1.0), ([], right, False))


class Config(unittest.TestCase):
    def test_defaults(self):
        cfg = C.normalize({})
        self.assertEqual((cfg["clicklock"], cfg["clicklock_ms"], cfg["clicklock_mode"], cfg["clicklock_rate"]),
                         (False, 5000, "repeat", 10))

    def test_validation(self):
        for bad in ({"clicklock": 1}, {"clicklock_ms": 100}, {"clicklock_ms": 60000},
                    {"clicklock_mode": "turbo"}, {"clicklock_rate": 0}, {"clicklock_rate": 100},
                    {"clicklock_ms": "5000"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                C.normalize(bad)
        self.assertEqual(C.normalize({"clicklock_ms": 7500, "clicklock_mode": "hold"})["clicklock_ms"], 7500)


class Daemon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events = []
        self.written = []
        self.ctl = Controller(config_path=Path(self.tmp.name) / "config.json", publish=self.events.append,
                              clock=lambda: 1000.0)

    def statuses(self):
        return [ev["clicklock_active"] for ev in self.events if ev["type"] == "status"]

    def test_off_by_default(self):
        self.assertFalse(self.ctl.status()["clicklock_active"])
        self.ctl.handle_frame(press(1), 1.0, self.written.extend)
        self.assertIsNone(self.ctl.clicklock_deadline)

    def test_engage_publish_and_switch_off(self):
        self.assertIsNone(self.ctl.set_config({"clicklock": True, "clicklock_ms": 2000}))
        self.ctl.handle_frame(press(1), 1.0, self.written.extend)
        self.assertEqual(self.ctl.clicklock_deadline, 3.0)
        self.ctl.handle_flush(3.0, self.written.extend)
        self.assertTrue(self.ctl.status()["clicklock_active"])
        self.assertEqual(self.statuses()[-1], True)
        self.ctl.handle_frame(press(0), 3.01, self.written.extend)
        self.ctl.handle_flush(3.05, self.written.extend)    # virtual down
        self.written.clear()
        self.assertIsNone(self.ctl.set_config({"clicklock": False}))
        self.assertEqual(self.statuses()[-1], False)
        self.ctl.handle_flush(3.06, self.written.extend)    # the release goes out on the next pass
        self.assertEqual(values(self.written), [0])
        self.assertIsNone(self.ctl.clicklock_deadline)

    def test_switching_mode_ends_a_running_lock(self):
        self.ctl.set_config({"clicklock": True, "clicklock_ms": 1000, "clicklock_mode": "hold"})
        self.ctl.handle_frame(press(1), 1.0, self.written.extend)
        self.ctl.handle_flush(2.0, self.written.extend)
        self.ctl.handle_frame(press(0), 2.5, self.written.extend)      # held for the user
        self.assertTrue(self.ctl.status()["clicklock_active"])
        self.written.clear()
        self.assertIsNone(self.ctl.set_config({"clicklock_mode": "repeat"}))
        self.assertFalse(self.ctl.status()["clicklock_active"])
        self.ctl.handle_flush(2.6, self.written.extend)
        self.assertEqual(values(self.written), [0])                  # the button is let go
        self.assertTrue(self.ctl.cfg["clicklock"])                   # still on, in the new mode

    def test_wheel_still_filtered_with_buttons(self):
        self.ctl.set_config({"clicklock": True})
        wheel = [(e.EV_REL, e.REL_WHEEL_HI_RES, 120), (e.EV_REL, e.REL_WHEEL, 1)]
        self.ctl.handle_frame(wheel + press(1), 1.0, self.written.extend)
        self.assertEqual(self.written, [wheel + press(1)])

    def test_disconnect_resets(self):
        self.ctl.set_config({"clicklock": True, "clicklock_ms": 1000})
        self.ctl.handle_frame(press(1), 1.0, self.written.extend)
        self.ctl.handle_flush(2.0, self.written.extend)
        self.ctl.set_device(False, None, "mouse disconnected")
        self.assertFalse(self.ctl.status()["clicklock_active"])
        self.assertIsNone(self.ctl.clicklock_deadline)


    def test_switch_off_release_survives_a_second_config_change(self):
        # Hold mode locked; Click-Lock switched off and the scroll filter retuned in the same round,
        # while a scroll tick is held back. The release must still go out.
        self.ctl.set_config({"clicklock": True, "clicklock_ms": 1000, "clicklock_mode": "hold"})
        self.ctl.handle_frame(press(1), 1.0, self.written.extend)
        self.ctl.handle_flush(2.0, self.written.extend)
        self.ctl.handle_frame(press(0), 2.5, self.written.extend)
        up = [(e.EV_REL, e.REL_WHEEL, 1)]
        down = [(e.EV_REL, e.REL_WHEEL, -1)]
        self.ctl.handle_frame(up, 2.6, self.written.extend)
        self.ctl.handle_frame(up, 2.61, self.written.extend)
        self.ctl.handle_frame(down, 2.7, self.written.extend)      # a reversal: may be held
        self.written.clear()
        self.assertIsNone(self.ctl.set_config({"clicklock": False}))
        self.assertIsNone(self.ctl.set_config({"lock_ms": 400}))
        self.ctl.handle_flush(2.71, self.written.extend)
        self.assertEqual(values(self.written), [0])


if __name__ == "__main__":
    unittest.main()
