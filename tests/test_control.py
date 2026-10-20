import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.control import POLL_S, Controller
from wheelguard.core import DROP, EMIT, HOLD, PASS


def tick(sign, extra=()):
    return [(e.EV_REL, e.REL_WHEEL_HI_RES, 120 * sign), (e.EV_REL, e.REL_WHEEL, sign), *extra]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.now = 1000.0
        self.events = []
        self.written = []
        self.ctl = self.make()

    def make(self, **kw):
        return Controller(config_path=self.path, publish=self.events.append,
                          clock=lambda: self.now, **kw)

    def frame(self, t, sign, extra=()):
        return self.ctl.handle_frame(tick(sign, extra), t, self.written.extend)

    def kinds(self):
        return [(ev["kind"], ev["held"]) for ev in self.events if ev["type"] == "tick"]


class Counters(Base):
    def test_emit_drop_hold(self):
        # 1.0+ 1.01+ (lock down) ; 1.02- inside pre window -> drop ; 1.2- held, then 1.21+ cancels it
        for t, s in [(1.0, 1), (1.01, 1), (1.02, -1), (1.2, -1), (1.21, 1)]:
            self.frame(t, s)
        st = self.ctl.stats
        self.assertEqual(st["ticks"], 5)
        self.assertEqual(st["passed"], 3)
        self.assertEqual(st["dropped"], 2)
        self.assertEqual(self.kinds(), [
            ("emit", False), ("emit", False), ("drop", False), ("hold", False),
            ("drop", True), ("emit", False)])

    def test_released_hold_counts_once(self):
        self.frame(1.0, 1)
        self.frame(1.2, -1)
        self.assertEqual(self.ctl.stats["ticks"], 2)
        self.assertEqual(self.ctl.stats["passed"], 1)       # the hold is still pending
        self.ctl.handle_flush(1.3, self.written.extend)
        self.assertEqual((self.ctl.stats["ticks"], self.ctl.stats["passed"]), (2, 2))

    def test_non_wheel_frames_are_not_counted_or_published(self):
        motion = [(e.EV_REL, e.REL_X, 3), (e.EV_REL, e.REL_Y, -1)]
        self.ctl.handle_frame(motion, 1.0, self.written.extend)
        self.assertEqual(self.written, [motion])
        self.assertEqual(self.ctl.stats["ticks"], 0)
        self.assertEqual(self.events, [])

    def test_reset_stats(self):
        self.frame(1.0, 1)
        self.now = 2000.0
        self.ctl.reset_stats()
        self.assertEqual(self.ctl.stats, {"ticks": 0, "passed": 0, "dropped": 0, "since": 2000.0})
        self.assertEqual(self.events[-1]["type"], "status")

    def test_tick_event_shape(self):
        self.frame(1.0, 1)
        self.frame(1.0125, -1)
        first, second = [ev for ev in self.events if ev["type"] == "tick"]
        self.assertEqual(first, {"type": "tick", "t": 1.0, "sign": 1, "kind": "emit",
                                 "held": False, "gap_ms": None})
        self.assertEqual((second["kind"], second["sign"], second["gap_ms"]), ("drop", -1, 12.5))


class Disabled(Base):
    def setUp(self):
        super().setUp()
        self.assertIsNone(self.ctl.set_config({"enabled": False}))
        self.events.clear()

    def test_everything_passes_untouched(self):
        # a sequence the filter would have trimmed
        seq = [(1.0, 1), (1.01, 1), (1.02, -1), (1.03, 1), (1.2, -1)]
        for t, s in seq:
            self.frame(t, s, extra=[(e.EV_REL, e.REL_X, 1)])
        self.assertEqual(len(self.written), len(seq))
        for (t, s), fr in zip(seq, self.written):
            self.assertEqual(fr, tick(s, [(e.EV_REL, e.REL_X, 1)]))
        self.assertEqual(self.kinds(), [(PASS, False)] * len(seq))
        st = self.ctl.stats
        self.assertEqual((st["ticks"], st["passed"], st["dropped"]), (5, 5, 0))
        self.assertIsNone(self.ctl.deadline)

    def test_gap_ms_reported(self):
        self.frame(1.0, 1)
        self.frame(1.05, -1)
        self.assertEqual([ev["gap_ms"] for ev in self.events], [None, 50.0])

    def test_flush_does_nothing(self):
        self.assertEqual(self.ctl.handle_flush(1e9, self.written.extend), [])
        self.assertEqual(self.written, [])

    def test_toggle_back_filters_again(self):
        self.assertIsNone(self.ctl.set_config({"enabled": True}))
        self.frame(1.0, 1)
        self.frame(1.01, -1)
        self.assertEqual(self.kinds(), [(EMIT, False), (DROP, False)])

    def test_disabling_releases_pending_hold(self):
        self.ctl.set_config({"enabled": True})
        self.frame(1.0, 1)
        self.frame(1.2, -1)                   # held
        self.written.clear()
        self.ctl.set_config({"enabled": False})
        self.ctl.handle_flush(1.2, self.written.extend)
        self.assertEqual(self.written, [[(e.EV_REL, e.REL_WHEEL_HI_RES, -120), (e.EV_REL, e.REL_WHEEL, -1)]])


class LiveConfig(Base):
    def test_set_rebuilds_filter_with_new_values(self):
        self.frame(1.0, 1)
        self.frame(1.07, -1)                  # >60ms after: held
        self.assertEqual(self.kinds()[-1], (HOLD, False))
        self.assertIsNone(self.ctl.set_config({"pre_ms": 100, "hold_ms": 40}))
        self.assertEqual((self.ctl.wf.pre, self.ctl.wf.hold), (0.1, 0.04))
        self.assertIsNone(self.ctl.wf.held)
        self.written.clear()
        self.ctl.handle_flush(1.07, self.written.extend)      # the loop does this right after
        self.assertEqual(self.written, [[(e.EV_REL, e.REL_WHEEL_HI_RES, -120), (e.EV_REL, e.REL_WHEEL, -1)]])
        self.assertEqual(self.kinds()[-1], (EMIT, True), "pending hold is released, not lost")

    def test_set_invalid_changes_nothing(self):
        before = dict(self.ctl.cfg)
        self.assertIn("hold_ms", self.ctl.set_config({"hold_ms": 9999}))
        self.assertEqual(self.ctl.cfg, before)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.events, [])

    def test_set_writes_file_and_publishes_status(self):
        self.assertIsNone(self.ctl.set_config({"lock_ms": 350}))
        self.assertEqual(C.load(self.path)["lock_ms"], 350)
        self.assertEqual(self.events[-1]["type"], "status")
        self.assertEqual(self.events[-1]["config"]["lock_ms"], 350)

    def test_hand_edit_applied_by_poll(self):
        C.save(C.normalize({"lock_ms": 500}), self.path)
        self.ctl.poll_config()                # too early
        self.assertEqual(self.ctl.cfg["lock_ms"], 300)
        self.now += POLL_S + 0.1
        self.ctl.poll_config()
        self.assertEqual(self.ctl.cfg["lock_ms"], 500)
        self.assertEqual(self.events[-1]["type"], "status")

    def test_own_write_not_reloaded(self):
        self.ctl.set_config({"lock_ms": 350})
        n = len(self.events)
        self.now += POLL_S + 0.1
        self.ctl.poll_config()
        self.assertEqual(len(self.events), n)

    def test_bad_hand_edit_keeps_running_config(self):
        self.path.write_text('{"hold_ms": 5000}')
        self.now += POLL_S + 0.1
        self.ctl.poll_config()
        self.assertEqual(self.ctl.cfg["hold_ms"], 25)
        self.assertIn("hold_ms", self.ctl.reload())

    def test_overrides_win_over_file(self):
        C.save(C.normalize({"lock_ms": 500}), self.path)
        ctl = self.make(overrides={"lock_ms": 450})
        self.assertEqual(ctl.cfg["lock_ms"], 450)
        self.assertIsNone(ctl.set_config({"lock_ms": 600}))
        self.assertEqual(ctl.cfg["lock_ms"], 450)
        self.assertEqual(C.load(self.path)["lock_ms"], 600)


class Status(Base):
    def test_status_fields(self):
        st = self.ctl.status()
        self.assertEqual(st["type"], "status")
        self.assertFalse(st["connected"])
        self.assertEqual(st["error"], "mouse not found")
        self.assertEqual(st["config"]["vendor"], "3367")
        self.assertEqual(set(st["stats"]), {"ticks", "passed", "dropped", "since"})

    def test_set_device_publishes_on_change_only(self):
        self.ctl.set_device(True, "Mouse", None)
        self.ctl.set_device(True, "Mouse", None)
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0]["connected"])


if __name__ == "__main__":
    unittest.main()
