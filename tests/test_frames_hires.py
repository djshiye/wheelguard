"""A high-resolution wheel sends partial steps (REL_WHEEL_HI_RES only). A held partial step that is let
through must come out as the same partial step, not as a whole notch."""
import unittest

from evdev import ecodes as e

from wheelguard.core import EMIT, HOLD, WheelFilter
from wheelguard.frames import flush_frames, process_frame, release_frame


def hires(v):
    return [(e.EV_REL, e.REL_WHEEL_HI_RES, v)]


class HeldPartialStep(unittest.TestCase):
    def test_expired_hold_releases_the_partial_step(self):
        wf = WheelFilter(300, 60, 25)
        process_frame(wf, hires(-15), 1.000)
        frames, d = process_frame(wf, hires(15), 1.100)            # a reversal after the pre window: held
        self.assertEqual((frames, d[-1].kind), ([], HOLD))
        frames, d = flush_frames(wf, 1.200)
        self.assertEqual(d[0].kind, EMIT)
        self.assertEqual(frames, [hires(15)])

    def test_confirmed_by_second_reversal(self):
        wf = WheelFilter(300, 60, 25)
        process_frame(wf, hires(-15), 1.000)
        process_frame(wf, hires(15), 1.100)
        frames, _ = process_frame(wf, hires(30), 1.110)
        self.assertEqual(frames, [hires(15), hires(30)])

    def test_dropped_hold_leaves_nothing_behind(self):
        wf = WheelFilter(300, 60, 25)
        process_frame(wf, hires(-15), 1.000)
        process_frame(wf, hires(15), 1.100)
        process_frame(wf, hires(-15), 1.110)                       # the bounce is dropped
        process_frame(wf, [(e.EV_REL, e.REL_WHEEL, 1), (e.EV_REL, e.REL_WHEEL_HI_RES, 120)], 1.300)   # held again
        frames, _ = flush_frames(wf, 1.400)
        self.assertEqual(frames, [[(e.EV_REL, e.REL_WHEEL, 1), (e.EV_REL, e.REL_WHEEL_HI_RES, 120)]])

    def test_a_whole_notch_keeps_both_events(self):
        self.assertEqual(sorted(release_frame(1)), sorted([(e.EV_REL, e.REL_WHEEL, 1), (e.EV_REL, e.REL_WHEEL_HI_RES, 120)]))


if __name__ == "__main__":
    unittest.main()
