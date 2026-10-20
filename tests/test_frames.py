import unittest

from evdev import ecodes as e

from wheelguard.core import WheelFilter
from wheelguard.frames import flush_frames, process_frame, release_frame, tick_sign

REL = e.EV_REL


def tick(sign, x=None, y=None, extra=()):
    ev = [(REL, e.REL_WHEEL_HI_RES, 120 * sign), (REL, e.REL_WHEEL, sign)]
    if x is not None:
        ev.insert(0, (REL, e.REL_X, x))
    if y is not None:
        ev.insert(1, (REL, e.REL_Y, y))
    return ev + list(extra)


def locked_down(t=1.0):
    wf = WheelFilter()
    wf.feed(t, -1)
    return wf


class FrameTests(unittest.TestCase):
    def test_non_wheel_frame_passes_through(self):
        wf = locked_down()
        frame = [(REL, e.REL_X, 3), (REL, e.REL_Y, -1), (e.EV_KEY, e.BTN_LEFT, 1)]
        frames, decisions = process_frame(wf, frame, 1.01)
        self.assertEqual(frames, [frame])
        self.assertEqual(decisions, [])

    def test_horizontal_wheel_unfiltered(self):
        wf = locked_down()
        frame = [(REL, e.REL_HWHEEL, 1), (REL, e.REL_HWHEEL_HI_RES, 120)]
        frames, _ = process_frame(wf, frame, 1.01)
        self.assertEqual(frames, [frame])
        self.assertEqual(wf.lock_dir, -1)

    def test_tick_sign(self):
        self.assertEqual(tick_sign(tick(1)), 1)
        self.assertEqual(tick_sign(tick(-1)), -1)
        self.assertEqual(tick_sign([(REL, e.REL_WHEEL_HI_RES, -120)]), -1)
        self.assertEqual(tick_sign([(REL, e.REL_X, 5)]), 0)
        self.assertEqual(tick_sign([(REL, e.REL_HWHEEL, 1)]), 0)

    def test_same_direction_tick_untouched(self):
        wf = locked_down()
        frame = tick(-1, x=2, y=1)
        frames, _ = process_frame(wf, frame, 1.01)
        self.assertEqual(frames, [frame])

    def test_dropped_tick_strips_wheel_keeps_motion_and_buttons(self):
        wf = locked_down()
        frame = tick(1, x=4, y=-2, extra=[(e.EV_KEY, e.BTN_RIGHT, 1)])
        frames, decisions = process_frame(wf, frame, 1.02)  # 20 ms after: dropped
        self.assertEqual(frames, [[(REL, e.REL_X, 4), (REL, e.REL_Y, -2), (e.EV_KEY, e.BTN_RIGHT, 1)]])
        self.assertEqual(decisions[0].kind, "drop")

    def test_dropped_tick_with_nothing_else_writes_no_frame(self):
        wf = locked_down()
        frames, _ = process_frame(wf, tick(1), 1.02)
        self.assertEqual(frames, [])

    def test_held_tick_stripped_then_released_as_own_frame(self):
        wf = locked_down()
        frame = tick(1, x=1)
        frames, decisions = process_frame(wf, frame, 1.2)
        self.assertEqual(frames, [[(REL, e.REL_X, 1)]])
        self.assertEqual(decisions[0].kind, "hold")
        self.assertEqual(flush_frames(wf, 1.21), ([], []))
        frames, decisions = flush_frames(wf, 1.225)
        self.assertEqual(frames, [[(REL, e.REL_WHEEL_HI_RES, 120), (REL, e.REL_WHEEL, 1)]])
        self.assertEqual(frames[0], release_frame(1))
        self.assertEqual(decisions[0].kind, "emit")

    def test_released_down_tick_values(self):
        self.assertEqual(release_frame(-1), [(REL, e.REL_WHEEL_HI_RES, -120), (REL, e.REL_WHEEL, -1)])

    def test_held_cancelled_by_lock_direction_tick(self):
        wf = locked_down()
        process_frame(wf, tick(1), 1.2)  # held
        frame = tick(-1, x=1)
        frames, _ = process_frame(wf, frame, 1.21)
        self.assertEqual(frames, [frame])  # held one vanished, new one passes as-is
        self.assertEqual(flush_frames(wf, 2.0), ([], []))

    def test_second_reversal_releases_held_first(self):
        wf = locked_down()
        process_frame(wf, tick(1), 1.2)
        frame = tick(1, y=3)
        frames, _ = process_frame(wf, frame, 1.215)
        self.assertEqual(frames, [release_frame(1), frame])

    def test_wheel_hires_only_frame_decides_by_hires(self):
        wf = locked_down()
        frames, _ = process_frame(wf, [(REL, e.REL_WHEEL_HI_RES, 120), (REL, e.REL_X, 1)], 1.02)
        self.assertEqual(frames, [[(REL, e.REL_X, 1)]])


if __name__ == "__main__":
    unittest.main()
