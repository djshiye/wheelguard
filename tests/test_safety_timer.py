import json
import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.clicklock import HOLD, REPEAT, ClickLock
from wheelguard.control import Controller
from wheelguard.power import STALL_S, SuspendWatch

LEFT = e.BTN_LEFT


def key(v):
    return [(e.EV_KEY, LEFT, v)]


def values(frames):
    return [ev[2] for f in frames for ev in f if ev[:2] == (e.EV_KEY, LEFT)]


class Timer(unittest.TestCase):
    def locked(self, mode, max_s=60):
        cl = ClickLock(True, 1000, mode, 10, max_s)
        cl.process(key(1), 0.0)
        frames, changed = cl.flush(1.0)
        self.assertTrue(changed and cl.active)
        return cl

    def test_hold_mode_lock_stops_and_releases_the_button(self):
        cl = self.locked(HOLD)
        self.assertEqual(cl.deadline, 61.0)
        cl.process(key(0), 2.0)                          # finger lifts, button stays down virtually
        self.assertTrue(cl.virtual_down)
        frames, changed = cl.flush(61.0)
        self.assertTrue(changed)
        self.assertFalse(cl.active)
        self.assertEqual(values(frames), [0])
        self.assertFalse(cl.virtual_down)
        self.assertTrue(cl.timed_out)
        self.assertEqual(cl.flush(70.0), ([], False))

    def test_repeat_mode_lock_stops_with_button_up(self):
        cl = self.locked(REPEAT)
        cl.process(key(0), 1.5)
        for t in (1.05, 1.1, 1.15, 1.2):                 # clicking away
            cl.flush(t)
        frames, changed = cl.flush(61.0)
        self.assertTrue(changed)
        self.assertFalse(cl.virtual_down)
        self.assertFalse(cl.active)
        self.assertIsNone(cl.deadline)

    def test_finger_still_down_when_timer_fires(self):
        cl = self.locked(REPEAT)                          # finger never lifted
        frames, _ = cl.flush(61.0)
        del frames
        self.assertTrue(cl.virtual_down)                  # the button follows the finger again
        _, rest, _ = cl.process(key(0), 62.0)
        self.assertEqual(values([rest]), [0])             # and its release goes through: nothing stuck
        self.assertFalse(cl.virtual_down)

    def test_zero_means_never(self):
        cl = self.locked(HOLD, max_s=0)
        self.assertIsNone(cl.deadline)
        self.assertEqual(cl.flush(10 ** 6), ([], False))
        self.assertTrue(cl.active)

    def test_deadline_is_the_earlier_of_click_and_limit(self):
        cl = self.locked(REPEAT, max_s=0.01)
        self.assertEqual(cl.deadline, 1.01)

    def test_engaging_again_restarts_the_timer(self):
        cl = self.locked(HOLD, max_s=10)
        cl.flush(11.0)
        cl.process(key(0), 12.0)
        cl.process(key(1), 20.0)
        cl.flush(21.0)
        self.assertTrue(cl.active)
        self.assertEqual(cl.deadline, 31.0)
        self.assertFalse(cl.timed_out)


class ConfigKey(unittest.TestCase):
    def test_default_and_limits(self):
        self.assertEqual(C.DEFAULTS["clicklock_max_s"], 600)
        for v in (0, 10, 60, 3600):
            self.assertEqual(C.normalize({"clicklock_max_s": v})["clicklock_max_s"], v)
        for v in (-1, 5, 3601, True, "10", None):
            with self.assertRaises(ValueError):
                C.normalize({"clicklock_max_s": v})

    def test_old_config_files_keep_working(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"clicklock": True, "clicklock_ms": 3000}))
            cfg = C.load(p)
        self.assertEqual(cfg["clicklock_max_s"], 600)


class FakePower:
    def __init__(self):
        self.slept = False

    def poll(self):
        slept, self.slept = self.slept, False
        return slept


class Controlled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events, self.written = [], []
        self.now = 1000.0
        self.power = FakePower()
        self.ctl = Controller(config_path=Path(self.tmp.name) / "config.json", publish=self.events.append,
                              clock=lambda: self.now, power=self.power)

    def engage(self, mode="hold", max_s=600):
        self.assertIsNone(self.ctl.set_config({"clicklock": True, "clicklock_mode": mode,
                                               "clicklock_ms": 1000, "clicklock_max_s": max_s}))
        self.ctl.handle_frame(key(1), 10.0, self.written.extend)
        self.ctl.handle_flush(11.0, self.written.extend)
        self.assertTrue(self.ctl.cl.active)
        self.ctl.handle_frame(key(0), 12.0, self.written.extend)
        self.written.clear()
        self.events.clear()

    def statuses(self):
        return [ev["clicklock_active"] for ev in self.events if ev["type"] == "status"]

    def test_timer_fires_through_the_controller(self):
        self.engage(max_s=30)
        self.assertEqual(self.ctl.clicklock_deadline, 41.0)
        self.ctl.handle_flush(41.0, self.written.extend)
        self.assertEqual(values(self.written), [0])
        self.assertEqual(self.statuses(), [False])

    def test_suspend_stops_the_lock_and_releases_the_button(self):
        self.engage()
        self.power.slept = True
        self.ctl.handle_flush(20.0, self.written.extend)
        self.assertEqual(values(self.written), [0])
        self.assertFalse(self.ctl.cl.active)
        self.assertEqual(self.statuses(), [False])

    def test_clicklock_stop_command_releases_the_button(self):
        self.engage()
        self.assertTrue(self.ctl.stop_clicklock("test"))
        self.ctl.handle_flush(13.0, self.written.extend)
        self.assertEqual(values(self.written), [0])
        self.assertFalse(self.ctl.stop_clicklock("again"))      # nothing running: harmless
        self.ctl.handle_flush(14.0, self.written.extend)
        self.assertEqual(values(self.written), [0])

    def test_stop_while_finger_down_in_repeat_mode(self):
        self.assertIsNone(self.ctl.set_config({"clicklock": True, "clicklock_ms": 1000}))
        self.ctl.handle_frame(key(1), 10.0, self.written.extend)
        self.ctl.handle_flush(11.0, self.written.extend)
        self.assertTrue(self.ctl.stop_clicklock("test"))
        self.ctl.handle_flush(11.1, self.written.extend)
        self.ctl.handle_frame(key(0), 12.0, self.written.extend)
        self.assertEqual(values(self.written)[-1], 0)            # the last thing the desktop heard is a release

    def test_changing_the_timer_setting_applies_live(self):
        self.engage(max_s=600)
        self.assertIsNone(self.ctl.set_config({"clicklock_max_s": 20}))
        self.assertEqual(self.ctl.clicklock_deadline, 31.0)


class Suspend(unittest.TestCase):
    def make(self):
        self.mono = self.boot = 100.0
        return SuspendWatch(boottime=lambda: self.boot, monotonic=lambda: self.mono)

    def test_normal_time_is_not_a_suspend(self):
        w = self.make()
        for _ in range(5):
            self.mono += 2.0
            self.boot += 2.0
            self.assertFalse(w.poll())

    def test_boottime_jump_is_a_suspend_once(self):
        w = self.make()
        self.mono += 2.0
        self.boot += 2.0 + 3600
        self.assertTrue(w.poll())
        self.mono += 2.0
        self.boot += 2.0
        self.assertFalse(w.poll())

    def test_earlier_sleep_before_start_is_ignored(self):
        self.mono, self.boot = 100.0, 5000.0
        w = SuspendWatch(boottime=lambda: self.boot, monotonic=lambda: self.mono)
        self.mono += 2.0
        self.boot += 2.0
        self.assertFalse(w.poll())

    def test_long_stall_counts(self):
        w = self.make()
        self.mono += STALL_S + 1
        self.boot += STALL_S + 1
        self.assertTrue(w.poll())


class Command(unittest.TestCase):
    def test_ipc_command(self):
        from wheelguard.ipc import Server
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctl = Controller(config_path=Path(tmp.name) / "c.json", power=FakePower())
        ctl.set_config({"clicklock": True, "clicklock_ms": 1000, "clicklock_mode": "hold"})
        out = []
        ctl.handle_frame(key(1), 1.0, out.extend)
        ctl.handle_flush(2.0, out.extend)
        ctl.handle_frame(key(0), 2.5, out.extend)
        self.assertTrue(ctl.cl.active)
        srv = Server(ctl, path=str(Path(tmp.name) / "s"))
        self.assertEqual(srv.dispatch(None, {"cmd": "clicklock_stop"}), {"type": "ok"})
        self.assertFalse(ctl.cl.active)
        ctl.handle_flush(3.0, out.extend)
        self.assertEqual(values(out)[-1], 0)


if __name__ == "__main__":
    unittest.main()
