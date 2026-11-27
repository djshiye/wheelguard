"""Random sessions with Click-Lock ON: presses on three buttons, time steps, setting changes, per-game profile
overrides coming and going, and stops. Whatever happens, once every finger is up and the timers have run out,
the desktop must see no button held, no lock may run while Click-Lock is off, and no button is pressed twice."""
import random
import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.control import Controller

BUTTONS = (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE)
SEEDS = 200
STEPS = 150


def session(seed, path):
    r = random.Random(seed)
    C.save({**C.DEFAULTS, "clicklock": True, "clicklock_ms": 500, "clicklock_max_s": 0,
            "debounce_ms": r.choice([0, 40]), "clicklock_mode": r.choice(["repeat", "hold"])}, path)
    now = [100.0]
    ctl = Controller(config_path=path, publish=lambda m: None, clock=lambda: now[0])
    virtual, finger, problems = set(), set(), []

    def write(frames):
        for frame in frames:
            for etype, code, value in frame:
                if etype != e.EV_KEY:
                    continue
                if value == 1 and code in virtual:
                    problems.append(("pressed twice", code))
                (virtual.add if value else virtual.discard)(code)

    owner = object()
    for _ in range(STEPS):
        a = r.random()
        if a < 0.4:
            b = r.choice(BUTTONS)
            v = 0 if b in finger else 1
            (finger.add if v else finger.discard)(b)
            ctl.handle_frame([(e.EV_KEY, b, v)], now[0], write)
        elif a < 0.7:
            now[0] += r.choice((0.005, 0.02, 0.05, 0.3, 0.6, 1.0))
            ctl.handle_flush(now[0], write)
        elif a < 0.8:
            ctl.set_config(r.choice(({"clicklock_button": r.choice(("left", "right"))},
                                     {"clicklock": r.random() < .5},
                                     {"clicklock_mode": r.choice(("repeat", "hold"))},
                                     {"debounce_ms": r.choice((0, 40, 100))},
                                     {"clicklock_ms": r.choice((500, 1000))},
                                     {"enabled": r.random() < .5})))
            ctl.handle_flush(now[0], write)
        elif a < 0.9:
            ctl.set_override(r.choice(({}, {"clicklock": False}, {"clicklock": True}, {"enabled": False})), owner)
            ctl.handle_flush(now[0], write)
        elif a < 0.95:
            ctl.client_gone(owner)
            ctl.handle_flush(now[0], write)
        else:
            ctl.stop_clicklock("test")
            ctl.handle_flush(now[0], write)
        if not ctl.cfg["clicklock"] and ctl.cl.locked:
            problems.append(("locked while off",))
    for b in sorted(finger):
        ctl.handle_frame([(e.EV_KEY, b, 0)], now[0], write)
    now[0] += 1.0
    ctl.handle_flush(now[0], write)
    ctl.stop_clicklock("end")
    now[0] += 1.0
    ctl.handle_flush(now[0], write)
    if virtual:
        problems.append(("left held", sorted(virtual)))
    return problems


class ClickLockFuzz(unittest.TestCase):
    def test_random_sessions_leave_nothing_held(self):
        with tempfile.TemporaryDirectory() as d:
            for seed in range(SEEDS):
                with self.subTest(seed=seed):
                    self.assertEqual(session(seed, Path(d) / f"{seed}.json"), [])


if __name__ == "__main__":
    unittest.main()
