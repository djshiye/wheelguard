"""Auto-tune: synthetic tick streams in, a preset recommendation out."""
import random
import unittest

from wheelguard.gui import presets

try:
    from PySide6.QtCore import QCoreApplication
    from wheelguard.gui import autotune
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

LIGHT, BALANCED, STRONG, MAXIMUM = range(4)


def scroll(seconds, strays=(), every=40, start=1000.0, seed=1):
    """(sign, kind, held, t) ticks: wheel-down notches every `every` ms for `seconds`, with a stray
    (g1 ms after a notch, then the notch direction resumes g2 ms later) inserted every ~second."""
    rnd = random.Random(seed)
    out, t, sign, n = [], start, -1, 0
    end = start + seconds
    stray_at = 1
    while t < end:
        out.append((sign, "emit", False, t))
        n += 1
        if strays and n % 25 == 0:
            g1, g2 = strays[stray_at % len(strays)]
            stray_at += 1
            out.append((-sign, "drop", False, t + g1 / 1000))
            t += (g1 + g2) / 1000
            out.append((sign, "emit", False, t))
        t += every / 1000 * (0.9 + 0.2 * rnd.random())
    return out


def run(ticks):
    a = autotune.Analyzer()
    for tk in ticks:
        a.feed(*tk)
    return a.result()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AnalyzerTests(unittest.TestCase):
    def test_nothing_heard_yet(self):
        r = autotune.Analyzer().result()
        self.assertEqual((r.state, r.ticks, r.recommended, r.confidence), ("idle", 0, -1, "none"))

    def test_needs_about_a_minute_of_scrolling(self):
        r = run(scroll(20))
        self.assertEqual(r.state, "collecting")
        self.assertEqual(r.recommended, -1)
        self.assertTrue(0.1 < r.progress < 0.7)
        self.assertIn("more seconds", autotune.describe(r))
        self.assertEqual(run(scroll(70)).state, "ready")

    def test_pauses_do_not_count_as_scrolling(self):
        ticks = []
        for burst in range(8):                       # 8 bursts of 5 s with 10 s of nothing between: 40 s of scrolling
            ticks += scroll(5, start=1000.0 + burst * 15)
        r = run(ticks)
        self.assertEqual(r.state, "collecting")
        self.assertAlmostEqual(r.active_s, 40, delta=2)

    def test_a_clean_wheel_gets_light(self):
        r = run(scroll(70))
        self.assertEqual((r.state, r.strays, r.recommended, r.confidence), ("ready", 0, LIGHT, "medium"))
        self.assertIn("didn't bounce", autotune.describe(r))

    def test_the_lightest_preset_that_catches_the_strays(self):
        cases = {LIGHT: [(30, 10)], BALANCED: [(55, 40)], STRONG: [(85, 40)], MAXIMUM: [(120, 60)]}
        for want, strays in cases.items():
            r = run(scroll(70, strays))
            self.assertEqual((r.state, r.recommended), ("ready", want), strays)
            self.assertEqual(r.caught, r.strays)
            self.assertGreaterEqual(r.strays, 10)
            self.assertEqual(r.confidence, "high")

    def test_a_few_slow_strays_among_many_fast_ones_tip_it_up(self):
        mix = [(30, 10)] * 6 + [(85, 40)]            # 1 in 7 is slow: Light would miss 14%, over the 10% allowance
        self.assertEqual(run(scroll(70, mix)).recommended, STRONG)
        mostly = [(30, 10)] * 19 + [(85, 40)]        # 1 in 20 is slow: within the allowance
        r = run(scroll(70, mostly))
        self.assertEqual(r.recommended, LIGHT)

    def test_strays_nothing_can_catch_recommend_the_strongest_and_say_so(self):
        r = run(scroll(70, [(300, 100)]))
        self.assertEqual((r.recommended, r.caught), (MAXIMUM, 0))
        self.assertIn("too slow", autotune.describe(r))
        self.assertNotEqual(r.confidence, "high")

    def test_deliberate_reversals_are_not_strays(self):
        ticks, t = [], 1000.0
        for i in range(2000):                        # 10 notches down, 10 up, over and over, 30 ms apart; 200 ms to turn
            sign = -1 if (i // 10) % 2 == 0 else 1
            ticks.append((sign, "emit", False, t))
            t += 0.03 if i % 10 != 9 else 0.2
        r = run(ticks)
        self.assertEqual((r.state, r.strays, r.recommended), ("ready", 0, LIGHT))

    def test_held_releases_and_bad_ticks_are_ignored(self):
        a = autotune.Analyzer()
        self.assertTrue(a.feed(-1, "emit", False, 1.0))
        self.assertTrue(a.feed(1, "hold", False, 1.05))
        self.assertFalse(a.feed(1, "drop", True, 1.05))        # the held tick, released: counted already
        self.assertFalse(a.feed(0, "emit", False, 1.1))
        self.assertFalse(a.feed(1, "bogus", False, 1.1))
        self.assertFalse(a.feed(-1, "emit", False, 0.5))       # time going backwards
        self.assertEqual(a.result().ticks, 2)

    def test_held_tick_then_direction_resumes_counts_as_one_stray(self):
        a = autotune.Analyzer()
        for tk in ((-1, "emit", False, 10.000), (1, "hold", False, 10.050), (1, "drop", True, 10.050),
                   (-1, "emit", False, 10.060)):
            a.feed(*tk)
        self.assertEqual(a.result().strays, 1)

    def test_confidence_levels(self):
        c = autotune.confidence_of
        self.assertEqual(c(500, 12, 12), "high")
        self.assertEqual(c(500, 5, 5), "medium")
        self.assertEqual(c(500, 2, 2), "low")
        self.assertEqual(c(500, 0, 0), "medium")
        self.assertEqual(c(150, 0, 0), "low")
        self.assertEqual(c(500, 12, 6), "medium")                # a high count, but half unreachable

    def test_recommend_matches_the_real_filter(self):
        """The 'caught' rule is the daemon's: check it against core.WheelFilter for every preset."""
        from wheelguard.core import DROP, WheelFilter
        for p in presets.PRESETS:
            for g1, g2 in [(20, 5), (30, 12), (50, 20), (70, 30), (100, 40), (120, 45), (200, 100), (300, 140)]:
                f = WheelFilter(p.lock_ms, p.pre_ms, p.hold_ms)
                t0 = 100.0
                f.feed(t0, -1)
                dec = f.feed(t0 + g1 / 1000, 1)
                dec += f.feed(t0 + (g1 + g2) / 1000, -1)
                dropped = any(d.kind == DROP and d.sign == 1 for d in dec)
                self.assertEqual(autotune.catches(p, autotune.Stray(g1, g2)), dropped, (p.name, g1, g2))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class QObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_ignores_ticks_until_started_and_start_resets(self):
        at = autotune.AutoTune()
        at.feed(-1, "emit", False, 1.0)
        self.assertEqual((at.ticks, at.state, at.running), (0, "idle", False))
        at.start()
        self.assertEqual((at.running, at.state, at.needMore), (True, "collecting", True))
        for tk in scroll(10):
            at.feed(*tk)
        self.assertGreater(at.ticks, 100)
        at.start()
        self.assertEqual(at.ticks, 0)

    def test_full_session_to_a_recommendation(self):
        at = autotune.AutoTune()
        changes = []
        at.changed.connect(lambda: changes.append(1))
        at.start()
        for tk in scroll(70, [(55, 40)]):
            at.feed(*tk)
        self.assertEqual((at.state, at.recommendedIndex, at.recommendedName), ("ready", BALANCED, "Balanced"))
        self.assertEqual((at.confidence, at.needMore, at.progress), ("high", False, 1.0))
        self.assertIn("Balanced would have caught all of them", at.message)
        self.assertGreater(len(changes), 3)
        self.assertLess(len(changes), len(scroll(70, [(55, 40)])))      # not one signal per tick
        at.stop()
        self.assertEqual((at.running, at.state, at.recommendedName), (False, "ready", "Balanced"))   # result stays
        at.feed(-1, "emit", False, 99999.0)
        self.assertEqual(at.state, "ready")                              # stopped means stopped
        at.reset()
        self.assertEqual((at.state, at.recommendedIndex, at.recommendedName), ("idle", -1, ""))

    def test_the_card_is_registered(self):
        from pathlib import Path
        from wheelguard.gui import app
        qml = Path(app.QML_DIR) / "Wg"
        self.assertIn("AutoTuneCard 1.0 AutoTuneCard.qml", (qml / "qmldir").read_text())
        self.assertIn("AutoTuneCard", (qml / "ScrollProtectionPage.qml").read_text())


if __name__ == "__main__":
    unittest.main()
