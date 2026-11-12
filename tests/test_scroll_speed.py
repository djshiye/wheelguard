"""1.10.0: scroll speed, Click-Lock on the right button (left-handed), thumb buttons in the Double-click fix."""
import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.clicklock import ClickLock
from wheelguard.control import Controller
from wheelguard.scrollspeed import ScrollSpeed

W, H = e.REL_WHEEL, e.REL_WHEEL_HI_RES
L, R = e.BTN_LEFT, e.BTN_RIGHT


def notch(sign=1):
    return [(e.EV_REL, W, sign), (e.EV_REL, H, 120 * sign)]


def totals(frames):
    """(sum of high-resolution units, sum of whole notches) in a list of frames."""
    hires = sum(v for f in frames for t, c, v in f if (t, c) == (e.EV_REL, H))
    whole = sum(v for f in frames for t, c, v in f if (t, c) == (e.EV_REL, W))
    return hires, whole


class Scale(unittest.TestCase):
    def test_100_percent_touches_nothing(self):
        s = ScrollSpeed(100)
        frames = [notch(), [(e.EV_REL, e.REL_X, 4)]]
        self.assertFalse(s.active)
        self.assertIs(s.frames(frames), frames)

    def test_faster(self):
        s = ScrollSpeed(150)
        self.assertEqual(s.frame(notch()), [(e.EV_REL, H, 180), (e.EV_REL, W, 1)])
        self.assertEqual(s.frame(notch()), [(e.EV_REL, H, 180), (e.EV_REL, W, 2)])     # 360 units = 3 notches so far
        self.assertEqual(totals([ScrollSpeed(200).frame(notch())]), (240, 2))
        self.assertEqual(totals([ScrollSpeed(300).frame(notch(-1))]), (-360, -3))

    def test_slower_sends_a_whole_notch_every_other_time(self):
        s = ScrollSpeed(50)
        self.assertEqual(s.frame(notch()), [(e.EV_REL, H, 60)])
        self.assertEqual(s.frame(notch()), [(e.EV_REL, H, 60), (e.EV_REL, W, 1)])
        self.assertEqual(s.frame(notch()), [(e.EV_REL, H, 60)])

    def test_nothing_is_lost_over_a_long_scroll(self):
        for percent in (50, 75, 125, 150, 175, 225, 300):
            for sign in (1, -1):
                s = ScrollSpeed(percent)
                out = [s.frame(notch(sign)) for _ in range(40)]
                self.assertEqual(totals(out), (sign * 48 * percent, sign * 48 * percent // 120 if sign > 0
                                               else -(48 * percent // 120)), (percent, sign))

    def test_high_resolution_wheel_keeps_its_fractions(self):
        s = ScrollSpeed(125)                        # a free-spinning wheel sends 15 units at a time
        out = [s.frame([(e.EV_REL, H, 15)]) for _ in range(16)]
        self.assertEqual(totals(out), (300, 2))     # 240 units in, 300 out, two whole notches
        self.assertTrue(all(f for f in out))        # every partial step still moves the page

    def test_direction_change_starts_clean(self):
        s = ScrollSpeed(50)
        s.frame(notch())                            # half a notch up is pending
        self.assertEqual(s.frame(notch(-1)), [(e.EV_REL, H, -60)])
        self.assertEqual(s.frame(notch(-1)), [(e.EV_REL, H, -60), (e.EV_REL, W, -1)])

    def test_only_the_vertical_wheel(self):
        s = ScrollSpeed(200)
        tilt = [(e.EV_REL, e.REL_HWHEEL, 1), (e.EV_REL, e.REL_HWHEEL_HI_RES, 120)]
        move = [(e.EV_REL, e.REL_X, 3), (e.EV_KEY, L, 1)]
        self.assertEqual(s.frame(tilt), tilt)
        self.assertEqual(s.frame(move), move)
        mixed = s.frame([(e.EV_REL, e.REL_X, 3)] + notch())
        self.assertEqual(mixed, [(e.EV_REL, e.REL_X, 3), (e.EV_REL, H, 240), (e.EV_REL, W, 2)])

    def test_a_mouse_without_high_resolution_events(self):
        s = ScrollSpeed(200)
        self.assertEqual(s.frame([(e.EV_REL, W, -1)]), [(e.EV_REL, H, -240), (e.EV_REL, W, -2)])

    def test_changing_the_speed_forgets_the_remainder(self):
        s = ScrollSpeed(50)
        s.frame(notch())
        s.configure(100)
        s.configure(50)
        self.assertEqual(s.frame(notch()), [(e.EV_REL, H, 60)])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.ctl = Controller(config_path=self.path)
        self.out = []

    def write(self, frames):
        self.out += frames

    def set(self, **cfg):
        self.assertIsNone(self.ctl.set_config(cfg))


class InTheService(Base):
    def test_config_key(self):
        self.assertEqual(C.DEFAULTS["scroll_speed"], 100)
        self.assertEqual(C.normalize({"scroll_speed": 250})["scroll_speed"], 250)
        for bad in (49, 301, 0, -100, 120.5, "150", True, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                C.normalize({"scroll_speed": bad})
        self.assertEqual(C.load(self.path)["scroll_speed"], 100)       # an older file without the key

    def test_wheel_ticks_leave_at_the_chosen_speed(self):
        self.set(scroll_speed=200)
        self.ctl.handle_frame(notch(), 1.0, self.write)
        self.ctl.handle_frame([(e.EV_REL, e.REL_X, 5)], 1.01, self.write)
        self.assertEqual(self.out, [[(e.EV_REL, H, 240), (e.EV_REL, W, 2)], [(e.EV_REL, e.REL_X, 5)]])
        self.assertEqual(self.ctl.stats["ticks"], 1)                    # counted as the one notch it was

    def test_a_blocked_bounce_stays_blocked(self):
        self.set(scroll_speed=300)
        self.ctl.handle_frame(notch(), 1.0, self.write)
        self.ctl.handle_frame(notch(-1), 1.005, self.write)             # 5 ms later: a bounce
        self.assertEqual(totals(self.out), (360, 3))
        self.assertEqual(self.ctl.stats["dropped"], 1)

    def test_a_held_tick_is_scaled_when_it_is_released(self):
        self.set(scroll_speed=200, pre_ms=0)
        self.ctl.handle_frame(notch(), 1.0, self.write)
        self.ctl.handle_frame(notch(-1), 1.010, self.write)             # held
        self.assertEqual(totals(self.out), (240, 2))
        self.ctl.handle_flush(1.040, self.write)                        # let through, at the chosen speed
        self.assertEqual(totals(self.out[1:]), (-240, -2))

    def test_also_with_protection_switched_off(self):
        self.set(scroll_speed=150, enabled=False)
        self.ctl.handle_frame(notch(), 1.0, self.write)
        self.assertEqual(totals(self.out), (180, 1))

    def test_back_to_normal(self):
        self.set(scroll_speed=50)
        self.ctl.handle_frame(notch(), 1.0, self.write)
        self.set(scroll_speed=100)
        self.ctl.handle_frame(notch(), 1.5, self.write)
        self.assertEqual(self.out[-1], notch())

    def test_not_a_profile_setting(self):
        self.assertIsNotNone(self.ctl.set_override({"scroll_speed": 200}))


class RightButton(Base):
    def key(self, code, v):
        return [(e.EV_KEY, code, v)]

    def test_lock_engages_on_the_chosen_button_only(self):
        cl = ClickLock(True, 1000, "hold", 10, button="right")
        cl.process(self.key(L, 1), 0.0)
        self.assertEqual(cl.flush(5.0), ([], False))                    # the left button never engages it now
        cl.process(self.key(L, 0), 5.1)
        _f, rest, _c = cl.process(self.key(R, 1), 6.0)
        self.assertEqual(rest, self.key(R, 1))
        frames, changed = cl.flush(7.0)
        self.assertTrue(changed and cl.active)
        _f, rest, _c = cl.process(self.key(R, 0), 7.5)                  # letting go: the button stays down
        self.assertEqual(rest, [])
        _f, rest, changed = cl.process(self.key(R, 1), 9.0)             # click again: swallowed, lock ends
        self.assertEqual((rest, cl.active), ([], False))
        _f, rest, _c = cl.process(self.key(R, 0), 9.1)
        self.assertEqual(rest, self.key(R, 0))

    def test_repeat_clicks_use_the_chosen_button(self):
        cl = ClickLock(True, 1000, "repeat", 10, button="right")
        cl.process(self.key(R, 1), 0.0)
        frames, _ = cl.flush(1.0)
        frames += cl.flush(1.05)[0] + cl.flush(1.10)[0]
        self.assertEqual(frames, [self.key(R, 0), self.key(R, 1), self.key(R, 0)])

    def test_the_other_button_is_the_way_out(self):
        cl = ClickLock(True, 1000, "hold", 10, button="right")
        cl.process(self.key(R, 1), 0.0)
        cl.flush(1.0)
        cl.process(self.key(R, 0), 1.5)
        frames, rest, changed = cl.process(self.key(L, 1), 2.0)         # the left button is now "any other button"
        self.assertEqual((frames, rest, cl.active), ([self.key(R, 0)], self.key(L, 1), False))

    def test_config_key(self):
        self.assertEqual(C.DEFAULTS["clicklock_button"], "left")
        self.assertEqual(C.normalize({"clicklock_button": "right"})["clicklock_button"], "right")
        for bad in ("middle", "", "LEFT", 1, None, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                C.normalize({"clicklock_button": bad})

    def test_switching_hands_ends_a_running_lock_and_frees_the_old_button(self):
        self.set(clicklock=True, clicklock_ms=500, clicklock_mode="hold")
        self.ctl.handle_frame(self.key(L, 1), 1.0, self.write)
        self.ctl.handle_flush(1.6, self.write)
        self.ctl.handle_frame(self.key(L, 0), 1.7, self.write)          # held by the lock
        self.assertTrue(self.ctl.cl.active)
        self.out = []
        self.set(clicklock_button="right")
        self.ctl.handle_flush(1.8, self.write)
        self.assertFalse(self.ctl.cl.active)
        self.assertEqual(self.out, [self.key(L, 0)])                    # the left button is let go of
        self.out = []
        self.ctl.handle_frame(self.key(L, 1), 2.0, self.write)          # and is an ordinary button from now on
        self.ctl.handle_flush(3.0, self.write)
        self.ctl.handle_frame(self.key(L, 0), 3.1, self.write)
        self.assertEqual(self.out, [self.key(L, 1), self.key(L, 0)])
        self.assertFalse(self.ctl.cl.active)

    def test_right_button_lock_through_the_service(self):
        self.set(clicklock=True, clicklock_ms=500, clicklock_mode="hold", clicklock_button="right")
        self.ctl.handle_frame(self.key(R, 1), 1.0, self.write)
        self.ctl.handle_flush(1.6, self.write)
        self.assertTrue(self.ctl.cl.active)


class ThumbButtons(Base):
    def test_counted_by_name(self):
        self.set(debounce_ms=40)
        for code in (e.BTN_SIDE, e.BTN_SIDE, e.BTN_EXTRA):
            self.ctl.handle_frame([(e.EV_KEY, code, 1)], 1.0, self.write)
            self.ctl.handle_frame([(e.EV_KEY, code, 0)], 1.1, self.write)
            self.ctl.handle_frame([(e.EV_KEY, code, 1)], 1.11, self.write)      # 10 ms later: a bounce
            self.ctl.handle_frame([(e.EV_KEY, code, 0)], 1.3, self.write)
            self.ctl.handle_flush(2.0, self.write)
        by = self.ctl.status()["stats"]["debounced_by"]
        self.assertEqual((by["back"], by["forward"], by["left"]), (2, 1, 0))


if __name__ == "__main__":
    unittest.main()
