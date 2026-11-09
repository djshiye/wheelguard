import random
import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.clicklock import REPEAT
from wheelguard.control import Controller
from wheelguard.debounce import ClickDebounce

L, R, M, SIDE = e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE
SCAN = (e.EV_MSC, e.MSC_SCAN, 0x90001)


def k(code, v):
    return [(e.EV_KEY, code, v)]


def keys(frames_or_events):
    """(button, value) pairs of a flat event list."""
    return [(ev[1], ev[2]) for ev in frames_or_events if ev[0] == e.EV_KEY]


def run(deb, script, end=None):
    """script: (t, code, value). Returns the key events that came out (after a final flush at `end`,
    default far in the future) and the number of bounces merged."""
    out, dropped = [], 0
    for t, code, value in script:
        ev, n = deb.process(k(code, value), t)
        out += keys(ev)
        dropped += n
    for _t, ev in deb.flush(float("inf") if end is None else end):
        out += keys(ev)
    return out, dropped


class Engine(unittest.TestCase):
    def test_off_passes_everything(self):
        deb = ClickDebounce(0)
        out, dropped = run(deb, [(0, L, 1), (0.01, L, 0), (0.02, L, 1), (0.03, L, 0)])
        self.assertEqual(out, [(L, 1), (L, 0), (L, 1), (L, 0)])
        self.assertEqual(dropped, 0)

    def test_chatter_is_one_clean_click(self):
        deb = ClickDebounce(40)
        out, dropped = run(deb, [(1.000, L, 1), (1.080, L, 0), (1.090, L, 1), (1.100, L, 0)])
        self.assertEqual(out, [(L, 1), (L, 0)])
        self.assertEqual(dropped, 1)

    def test_a_chattering_drag_stays_one_drag(self):
        deb = ClickDebounce(40)
        out = []
        for t, v in ((1.000, 1), (1.100, 0), (1.102, 1)):       # press, bounce-release, press 2 ms later
            ev, _ = deb.process(k(L, v), t)
            out += keys(ev)
        ev, _ = deb.process([(e.EV_REL, e.REL_X, 5)], 1.5)       # the drag carries on with the button down
        self.assertEqual(out, [(L, 1)])
        self.assertEqual(deb.deadline, None)
        ev, _ = deb.process(k(L, 0), 2.0)                        # the real release
        self.assertEqual(keys(ev), [])
        self.assertAlmostEqual(deb.deadline, 2.04)
        self.assertEqual([keys(x) for _, x in deb.flush(2.05)], [[(L, 0)]])     # not lost: goes out when due

    def test_a_real_second_click_after_the_window_goes_through(self):
        deb = ClickDebounce(40)
        out, dropped = run(deb, [(1.0, L, 1), (1.05, L, 0), (1.10, L, 1), (1.15, L, 0)])
        self.assertEqual(out, [(L, 1), (L, 0), (L, 1), (L, 0)])
        self.assertEqual(dropped, 0)

    def test_the_release_comes_out_before_a_later_press(self):
        deb = ClickDebounce(40)
        out, _ = run(deb, [(1.0, L, 1), (1.05, L, 0), (1.10, L, 1)], end=1.11)
        self.assertEqual(out, [(L, 1), (L, 0), (L, 1)])

    def test_window_edge(self):
        deb = ClickDebounce(40)
        out, _ = run(deb, [(1.0, L, 1), (1.1, L, 0), (1.139, L, 1), (1.2, L, 0)])
        self.assertEqual(out, [(L, 1), (L, 0)])
        deb = ClickDebounce(40)
        out, _ = run(deb, [(1.0, L, 1), (1.1, L, 0), (1.141, L, 1), (1.2, L, 0)])
        self.assertEqual(out, [(L, 1), (L, 0), (L, 1), (L, 0)])

    def test_a_burst_of_chatter_is_one_click(self):
        deb = ClickDebounce(40)
        script = [(1.00, L, 1), (1.10, L, 0)]
        for i in range(1, 3):                           # two bounces 10 and 20 ms apart
            script += [(1.10 + 0.01 * i - 0.005, L, 1), (1.10 + 0.01 * i, L, 0)]
        script += [(1.25, L, 1), (1.30, L, 0)]          # a real click, well after
        out, dropped = run(deb, script)
        self.assertEqual(out, [(L, 1), (L, 0), (L, 1), (L, 0)])
        self.assertEqual(dropped, 2)

    def test_each_button_on_its_own(self):
        deb = ClickDebounce(40)
        out, dropped = run(deb, [
            (1.00, L, 1), (1.10, L, 0), (1.11, L, 1), (1.12, L, 0),     # left chatter: merged
            (1.13, R, 1), (1.20, R, 0), (1.21, R, 1), (1.22, R, 0),     # right likewise
            (1.23, M, 1), (1.30, M, 0), (1.31, M, 1), (1.32, M, 0)])    # middle likewise
        self.assertEqual(dropped, 3)
        self.assertEqual(sorted(out), sorted([(L, 1), (L, 0), (R, 1), (R, 0), (M, 1), (M, 0)]))
        self.assertEqual([c for c, v in out if v], [L, R, M])

    def test_other_buttons_are_never_touched(self):
        deb = ClickDebounce(150)
        out, dropped = run(deb, [(1.0, SIDE, 1), (1.01, SIDE, 0), (1.02, SIDE, 1), (1.03, SIDE, 0)])
        self.assertEqual(out, [(SIDE, 1), (SIDE, 0), (SIDE, 1), (SIDE, 0)])
        self.assertEqual(dropped, 0)

    def test_non_button_events_pass_and_scancode_goes_with_a_held_release(self):
        deb = ClickDebounce(40)
        ev, _ = deb.process([SCAN] + k(L, 1), 1.0)
        self.assertEqual(ev, [SCAN] + k(L, 1))
        motion = [(e.EV_REL, e.REL_X, 5), (e.EV_REL, e.REL_WHEEL, 1)]
        ev, n = deb.process([SCAN] + k(L, 0) + motion, 1.1)
        self.assertEqual((ev, n), (motion, 0))
        ev, n = deb.process([SCAN] + k(L, 1), 1.11)
        self.assertEqual((ev, n), ([], 1))

    def test_press_and_release_in_one_frame(self):
        deb = ClickDebounce(40)
        ev, _ = deb.process(k(L, 1) + k(L, 0), 1.0)
        self.assertEqual(keys(ev), [(L, 1)])
        ev, n = deb.process(k(L, 1) + k(L, 0), 1.01)    # bounce, then released again
        self.assertEqual((keys(ev), n), ([], 1))
        self.assertEqual([keys(x) for _, x in deb.flush(1.1)], [[(L, 0)]])

    def test_turning_it_off_with_a_release_held_still_delivers_it(self):
        deb = ClickDebounce(40)
        deb.process(k(L, 1), 1.0)
        deb.process(k(L, 0), 1.1)
        deb.configure(0)                                # switched off mid-click
        self.assertEqual([keys(x) for _, x in deb.drain()], [[(L, 0)]])
        ev, _ = deb.process(k(L, 1), 1.11)              # and the next click is unfiltered
        self.assertEqual(keys(ev), [(L, 1)])
        ev, _ = deb.process(k(L, 0), 1.12)
        self.assertEqual(keys(ev), [(L, 0)])

    def test_reset_forgets_everything(self):
        deb = ClickDebounce(40)
        run(deb, [(1.0, L, 1), (1.1, L, 0)], end=1.11)
        deb.reset()
        self.assertEqual((deb.held, deb.down, deb.deadline), ({}, set(), None))
        ev, _ = deb.process(k(L, 1), 1.12)
        self.assertEqual(keys(ev), [(L, 1)])

    def test_clock_going_backwards_never_loses_a_release(self):
        deb = ClickDebounce(40)
        deb.process(k(L, 1), 5.0)
        deb.process(k(L, 0), 5.1)
        self.assertEqual([keys(x) for _, x in deb.flush(1.0)], [[(L, 0)]])

    def test_random_streams_never_leave_a_button_stuck(self):
        rng = random.Random(7)
        for _ in range(300):
            deb = ClickDebounce(rng.choice((0, 10, 40, 150)))
            down, seen = set(), set()           # physical state / what the desktop has been told
            presses = releases = 0
            t = 0.0

            def take(ev):
                nonlocal presses, releases
                for _, c, v in ev:
                    if v:
                        self.assertNotIn(c, seen)       # never a press for a button already down
                        seen.add(c)
                        presses += 1
                    else:
                        self.assertIn(c, seen)          # never a release without its press
                        seen.discard(c)
                        releases += 1
            for _ in range(60):
                t += rng.choice((0.002, 0.01, 0.03, 0.08, 0.2))
                if rng.random() < 0.3:
                    take(sum((x for _, x in deb.flush(t)), []))
                code = rng.choice((L, R, M))
                value = 0 if code in down else 1
                down.symmetric_difference_update({code})
                if rng.random() < 0.1:
                    deb.configure(rng.choice((0, 40)))
                    take(sum((x for _, x in deb.drain()), []))
                ev, _ = deb.process(k(code, value), t)
                take(ev)
            for code in list(down):                     # the finger lets go of everything
                t += 1.0
                ev, _ = deb.process(k(code, 0), t)
                take(ev)
            take(sum((x for _, x in deb.flush(t + 10)), []))
            self.assertEqual(seen, set())               # nothing left pressed
            self.assertEqual(presses, releases)         # every passed press got exactly one release


class Config(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(C.DEFAULTS["debounce_ms"], 0)
        for v in (0, 10, 40, 150):
            self.assertEqual(C.normalize({"debounce_ms": v})["debounce_ms"], v)
        for v in (-5, 1, 9, 151, 5000, True, "40", None, float("nan")):
            with self.assertRaises(ValueError):
                C.normalize({"debounce_ms": v})

    def test_old_config_files_keep_working(self):
        self.assertEqual(C.normalize({"lock_ms": 300})["debounce_ms"], 0)


class Controlled(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.events, self.written = [], []
        self.ctl = Controller(config_path=Path(tmp.name) / "c.json", publish=self.events.append,
                              clock=lambda: 1000.0)

    def feed(self, t, code, value):
        self.ctl.handle_frame(k(code, value), t, self.written.extend)

    def flush(self, now):
        self.ctl.handle_flush(now, self.written.extend)

    def sent(self):
        return keys([ev for frame in self.written for ev in frame])

    def test_chatter_is_filtered_counted_and_published(self):
        self.assertIsNone(self.ctl.set_config({"debounce_ms": 40}))
        self.events.clear()
        for t, v in ((1.0, 1), (1.1, 0), (1.11, 1), (1.12, 0)):
            self.feed(t, L, v)
        self.assertEqual(self.sent(), [(L, 1)])
        self.flush(1.2)
        self.assertEqual(self.sent(), [(L, 1), (L, 0)])
        self.assertEqual(self.ctl.stats["debounced"], 1)
        status = [ev for ev in self.events if ev["type"] == "status"]
        self.assertEqual(status[-1]["stats"]["debounced"], 1)
        self.ctl.reset_stats()
        self.assertEqual(self.ctl.stats["debounced"], 0)

    def test_off_by_default(self):
        for t, v in ((1.0, 1), (1.1, 0), (1.11, 1), (1.12, 0)):
            self.feed(t, L, v)
        self.assertEqual(len(self.sent()), 4)
        self.assertEqual(self.ctl.stats["debounced"], 0)

    def test_the_wheel_filter_still_sees_wheel_ticks_in_the_same_frame(self):
        self.ctl.set_config({"debounce_ms": 40})
        self.feed(1.0, L, 1)
        self.feed(1.1, L, 0)
        tick = [(e.EV_REL, e.REL_WHEEL_HI_RES, 120), (e.EV_REL, e.REL_WHEEL, 1)] + k(L, 1)
        decisions = self.ctl.handle_frame(tick, 1.11, self.written.extend)
        self.assertEqual([d.kind for d in decisions], ["emit"])
        self.assertEqual(self.written[-1], tick[:2])        # the tick went out, the chatter press did not

    def test_chatter_never_reaches_click_lock(self):
        # worn switch right at the moment of a click that would end a lock: the lock must see one click
        self.ctl.set_config({"debounce_ms": 40, "clicklock": True, "clicklock_ms": 1000, "clicklock_mode": "hold"})
        self.feed(1.0, L, 1)
        self.ctl.handle_flush(2.1, self.written.extend)
        self.feed(2.2, L, 0)
        self.assertTrue(self.ctl.cl.active)
        self.feed(5.0, L, 1)                                # the click that ends the lock
        self.feed(5.05, L, 0)                               # its release lets go
        self.flush(5.1)
        self.assertFalse(self.ctl.cl.active)
        self.assertFalse(self.ctl.cl.virtual_down)
        n = len(self.sent())
        for t, v in ((5.2, 1), (5.25, 0), (5.26, 1), (5.27, 0)):    # a click with a bounce: one click
            self.feed(t, L, v)
        self.flush(5.4)
        self.assertEqual(self.sent()[n:], [(L, 1), (L, 0)])
        self.assertFalse(self.ctl.cl.virtual_down)

    def test_click_lock_own_clicks_are_not_debounced(self):
        self.ctl.set_config({"debounce_ms": 150, "clicklock": True, "clicklock_ms": 1000,
                             "clicklock_mode": REPEAT, "clicklock_rate": 30})
        self.feed(1.0, L, 1)
        self.ctl.handle_flush(2.1, self.written.extend)
        for i in range(1, 7):
            self.ctl.handle_flush(2.1 + i * 0.0167, self.written.extend)
        vals = [v for c, v in self.sent() if c == L]
        self.assertGreaterEqual(len(vals), 4)               # the repeat clicks (every ~17 ms) all went out
        self.assertEqual(self.ctl.stats["debounced"], 0)

    def test_session_reset_clears_the_state(self):
        self.ctl.set_config({"debounce_ms": 40})
        self.feed(1.0, L, 1)
        self.feed(1.1, L, 0)
        self.assertIsNotNone(self.ctl.debounce_deadline)
        self.ctl.reset_filter()
        self.assertIsNone(self.ctl.debounce_deadline)
        self.feed(1.12, L, 1)
        self.assertEqual(self.sent()[-1], (L, 1))

    def test_live_config_change(self):
        self.ctl.set_config({"debounce_ms": 40})
        self.ctl.set_config({"debounce_ms": 0})
        for t, v in ((1.0, 1), (1.1, 0), (1.11, 1)):
            self.feed(t, L, v)
        self.assertEqual(len(self.sent()), 3)

    def test_switching_it_off_with_a_release_held_delivers_it(self):
        self.ctl.set_config({"debounce_ms": 150})
        self.feed(1.0, L, 1)
        self.feed(1.1, L, 0)
        self.assertEqual(self.sent(), [(L, 1)])
        self.ctl.set_config({"debounce_ms": 0})
        self.flush(1.11)
        self.assertEqual(self.sent(), [(L, 1), (L, 0)])

    def test_the_release_has_a_deadline_the_daemon_services(self):
        self.ctl.set_config({"debounce_ms": 40})
        self.assertIsNone(self.ctl.debounce_deadline)
        self.feed(1.0, L, 1)
        self.feed(1.1, L, 0)
        self.assertAlmostEqual(self.ctl.debounce_deadline, 1.14)
        self.flush(1.13)
        self.assertEqual(self.sent(), [(L, 1)])
        self.flush(1.15)
        self.assertEqual(self.sent(), [(L, 1), (L, 0)])
        self.assertIsNone(self.ctl.debounce_deadline)

    def test_a_merged_bounce_does_not_cancel_the_click_lock_timer(self):
        self.ctl.set_config({"debounce_ms": 40, "clicklock": True, "clicklock_ms": 1000, "clicklock_mode": "hold"})
        self.feed(1.0, L, 1)
        self.feed(1.5, L, 0)                                # bounce in the middle of the hold ...
        self.feed(1.51, L, 1)                               # ... merged: Click-Lock never saw a release
        self.flush(2.1)
        self.assertTrue(self.ctl.cl.active)

    def test_a_real_release_cancels_the_click_lock_timer_at_its_own_time(self):
        self.ctl.set_config({"debounce_ms": 40, "clicklock": True, "clicklock_ms": 1000, "clicklock_mode": "hold"})
        self.feed(1.0, L, 1)
        self.feed(1.99, L, 0)                               # released just before the hold time
        self.flush(2.1)                                     # the release is delivered late, with its own time
        self.assertFalse(self.ctl.cl.active)
        self.assertEqual(self.sent(), [(L, 1), (L, 0)])


if __name__ == "__main__":
    unittest.main()
