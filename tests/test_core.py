import unittest

from wheelguard.core import DROP, EMIT, HOLD, WheelFilter


def parse(seq):
    """'1.253+,1.266-' -> [(1.253, +1), (1.266, -1)]"""
    return [(float(tok[:-1]), 1 if tok[-1] == "+" else -1) for tok in seq.split(",")]


def run(ticks, scale=1, **kw):
    """Feed ticks (sign multiplied by scale); returns {t: final kind} and the full decision list."""
    wf = WheelFilter(**kw)
    decisions = []
    for t, s in ticks:
        decisions += wf.feed(t, s * scale)
    decisions += wf.flush(float("inf"))
    final = {d.t: d.kind for d in decisions if d.kind != HOLD}
    return final, decisions


class BounceSequences(unittest.TestCase):
    SEQUENCES = [
        "1.253+,1.266+,1.283+,1.290+,1.303-,1.307+,1.322-,1.332+,1.350+,1.359+",
        "3.528+,3.539+,3.542+,3.556-,3.558-,3.561+,3.573+",
        "4.579+,4.602+,4.799-,4.810+,4.822-,4.828+,4.840+,4.844-,4.860+",
        "6.289+,6.291-,6.319+,6.322+,6.336+,6.339+,6.380-,6.529+,6.547-,6.560+",
        "16.966+,16.974+,16.992-,17.007-,17.019+,17.054+",
        "5.106+,5.112+,5.126+,5.130-,5.141-,5.147+,5.156-,5.163+,5.176+,5.180-,5.187+",
    ]

    def check(self, seq, scale):
        ticks = parse(seq)
        final, _ = run(ticks, scale)
        self.assertEqual(len(final), len(ticks), "every tick needs exactly one final decision")
        for t, s in ticks:
            self.assertEqual(final[t], EMIT if s > 0 else DROP, f"{seq}: tick {t}{'+' if s > 0 else '-'}")

    def test_sequences_scrolling_down(self):
        for seq in self.SEQUENCES:
            with self.subTest(seq=seq):
                self.check(seq, 1)

    def test_sequences_scrolling_up(self):
        for seq in self.SEQUENCES:
            with self.subTest(seq=seq):
                self.check(seq, -1)

    def test_held_tick_cancelled_by_lock_direction(self):
        _, decisions = run(parse("4.579+,4.602+,4.799-,4.810+"))
        kinds = [(d.kind, d.t, d.held) for d in decisions]
        self.assertIn((HOLD, 4.799, False), kinds)
        self.assertIn((DROP, 4.799, True), kinds)

    def test_same_direction_has_no_latency(self):
        wf = WheelFilter()
        for t in (1.0, 1.01, 1.02):
            d = wf.feed(t, 1)
            self.assertEqual([(x.kind, x.held) for x in d], [(EMIT, False)])
        self.assertIsNone(wf.deadline)


class RealIntent(unittest.TestCase):
    def down_ticks(self, start=1.0, n=6, step=0.02):
        return [(round(start + i * step, 6), -1) for i in range(n)]

    def test_pause_then_single_reversal_is_immediate(self):
        ticks = self.down_ticks()
        last = ticks[-1][0]
        wf = WheelFilter()
        for t, s in ticks:
            wf.feed(t, s)
        d = wf.feed(last + 0.4, 1)
        self.assertEqual([(x.kind, x.t, x.held) for x in d], [(EMIT, last + 0.4, False)])
        self.assertEqual(wf.lock_dir, 1)

    def test_lone_reversal_150ms_later_is_emitted_after_hold(self):
        ticks = self.down_ticks()
        last = ticks[-1][0]
        wf = WheelFilter(hold_ms=25)
        for t, s in ticks:
            wf.feed(t, s)
        t_up = last + 0.150
        d = wf.feed(t_up, 1)
        self.assertEqual([x.kind for x in d], [HOLD])
        self.assertAlmostEqual(wf.deadline, t_up + 0.025)
        self.assertEqual(wf.flush(t_up + 0.024), [])
        d = wf.flush(t_up + 0.025)
        self.assertEqual([(x.kind, x.t) for x in d], [(EMIT, t_up)])
        self.assertEqual(wf.lock_dir, 1)
        self.assertIsNone(wf.deadline)
        # lock now follows the new direction: a down tick right after is a stray
        self.assertEqual([x.kind for x in wf.feed(t_up + 0.03, -1)], [DROP])

    def test_burst_of_reversals_switches_lock(self):
        ticks = self.down_ticks()
        last = ticks[-1][0]
        ups = [(last + 0.100 + i * 0.015, 1) for i in range(3)]
        final, _ = run(ticks + ups)
        for t, s in ticks + ups:
            self.assertEqual(final[t], EMIT, t)
        wf = WheelFilter()
        for t, s in ticks + ups:
            wf.feed(t, s)
        self.assertEqual(wf.lock_dir, 1)

    def test_reversal_30ms_after_last_tick_is_dropped(self):
        ticks = self.down_ticks()
        last = ticks[-1][0]
        final, _ = run(ticks + [(last + 0.030, 1)])
        self.assertEqual(final[last + 0.030], DROP)

    def test_late_feed_flushes_expired_hold_first(self):
        wf = WheelFilter()
        wf.feed(1.0, -1)
        wf.feed(1.2, 1)  # held
        d = wf.feed(1.5, 1)  # arrives after the hold expired (timer was late)
        self.assertEqual([(x.kind, x.held, x.t) for x in d],
                         [(EMIT, True, 1.2), (EMIT, False, 1.5)])

    def test_second_reversal_confirms_even_if_lock_expires_during_hold(self):
        wf = WheelFilter()
        wf.feed(1.0, -1)
        self.assertEqual([x.kind for x in wf.feed(1.285, 1)], [HOLD])
        d = wf.feed(1.302, 1)  # lock (300 ms) expired, hold (25 ms) still pending
        self.assertEqual([(x.kind, x.t, x.held) for x in d], [(EMIT, 1.285, True), (EMIT, 1.302, False)])
        self.assertEqual(wf.lock_dir, 1)

    def test_gap_reported(self):
        wf = WheelFilter()
        wf.feed(1.000, -1)
        d = wf.feed(1.040, 1)
        self.assertAlmostEqual(d[0].gap_ms, 40.0)


if __name__ == "__main__":
    unittest.main()
