"""Button state after the kernel drops events (SYN_DROPPED): no stuck, lost or swallowed clicks.

The FrameReader tests use a small model of the kernel's evdev client buffer that behaves like
drivers/input/evdev.c: reads return at most 64 events (python-evdev's device_read_many), and
EVIOCGKEY (dev.active_keys(), FrameReader.keys_now) FLUSHES every queued EV_KEY event from the
client buffer (evdev_handle_get_val -> __evdev_flush_queue). Events python-evdev has already read
into its batch are not flushed, but they are already part of the state EVIOCGKEY returns.

Each test_drift_* test failed on 1.6.0: it showed a way the button state drifted from the mouse.
"""
import random
import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard.control import Controller
from wheelguard.daemon import FrameReader, KeyState

SYN = (e.EV_SYN, e.SYN_REPORT, 0)
DROPPED = (e.EV_SYN, e.SYN_DROPPED, 0)
SCAN = (e.EV_MSC, e.MSC_SCAN, 0x90001)
LEFT_DOWN = (e.EV_KEY, e.BTN_LEFT, 1)
LEFT_UP = (e.EV_KEY, e.BTN_LEFT, 0)
MOVE = (e.EV_REL, e.REL_X, 3)


class FakeClient:
    """The daemon's evdev client buffer for the grabbed mouse."""

    def __init__(self, queue, phys=()):
        self.queue = list(queue)
        self.phys = set(phys)
        self.before_ioctl = None      # hardware events that land between read() and the ioctl

    def hw(self, *events):
        for ev in events:
            self.queue.append(ev)
            if ev[0] == e.EV_KEY:
                (self.phys.add if ev[2] else self.phys.discard)(ev[1])

    def read(self):
        batch, self.queue = self.queue[:64], self.queue[64:]
        return batch

    def active_keys(self):
        if self.before_ioctl:
            self.hw(*self.before_ioctl)
            self.before_ioctl = None
        # __evdev_flush_queue(client, EV_KEY): drop queued key events (and SYN groups left empty)
        kept, group = [], []
        for ev in self.queue:
            if ev[0] == e.EV_KEY:
                continue
            group.append(ev)
            if ev == SYN:
                if len(group) > 1 or not kept:
                    kept += group
                group = []
        self.queue = kept + group
        return sorted(self.phys)


def run(client, reader, out, emitted):
    """The read loop of run_session(): every batch, every event through FrameReader."""
    while client.queue:
        reader.next_batch()
        for ev in client.read():
            for frame in reader.feed(*ev):
                emitted.extend(frame)
                out.update(frame)


class ResyncDrift(unittest.TestCase):
    def setUp(self):
        self.out = KeyState()       # what the virtual mouse was told
        self.emitted = []

    def start(self, client, down=()):
        reader = FrameReader(client.active_keys)
        reader.state.down = set(down)
        self.out.down = set(down)
        return reader

    def test_drift_press_read_before_resync_whose_release_was_flushed_sticks(self):
        # Overflow, then the press is already in our batch; the user lets go before the re-read.
        # EVIOCGKEY says "up" and flushes the queued release, then the batch replays the press.
        client = FakeClient([DROPPED, MOVE, SYN, SCAN, LEFT_DOWN, SYN], phys={e.BTN_LEFT})
        client.before_ioctl = [SCAN, LEFT_UP, SYN]
        reader = self.start(client)
        run(client, reader, self.out, self.emitted)
        self.assertEqual(client.phys, set())
        self.assertEqual(self.out.down, set(), "virtual left button stuck down after the finger let go")

    def test_drift_stuck_state_swallows_the_next_press(self):
        # The consequence of the drift above in 1.6.0: the next real click lost its press,
        # only its release reached the desktop. That is "the first click on X does nothing".
        client = FakeClient([DROPPED, MOVE, SYN, SCAN, LEFT_DOWN, SYN], phys={e.BTN_LEFT})
        client.before_ioctl = [SCAN, LEFT_UP, SYN]
        reader = self.start(client)
        run(client, reader, self.out, self.emitted)
        self.emitted.clear()
        client.hw(SCAN, LEFT_DOWN, SYN, SCAN, LEFT_UP, SYN)     # a normal click on the X
        run(client, reader, self.out, self.emitted)
        keys = [ev for ev in self.emitted if ev[0] == e.EV_KEY]
        self.assertEqual(keys, [LEFT_DOWN, LEFT_UP], "the click's press was swallowed")

    def test_drift_release_read_before_resync_loses_the_new_press(self):
        # Button held before the overflow; the batch holds its release, the user presses again
        # before the re-read: EVIOCGKEY says "down" and flushes that press, the batch replays the
        # release. The new press is never sent although the finger is on the button.
        client = FakeClient([DROPPED, MOVE, SYN, SCAN, LEFT_UP, SYN], phys=set())
        client.before_ioctl = [SCAN, LEFT_DOWN, SYN]
        reader = self.start(client, down={e.BTN_LEFT})
        run(client, reader, self.out, self.emitted)
        self.assertEqual(client.phys, {e.BTN_LEFT})
        self.assertEqual(self.out.down, {e.BTN_LEFT}, "the second press never reached the desktop")

    def test_drift_press_in_a_frame_cut_by_the_overflow_is_never_sent(self):
        # A read() of 64 events can end inside a frame. 1.6.0 recorded the press in its state at
        # once, before the frame was complete. If the kernel then overflows, the
        # frame is thrown away, but the state keeps the press, so the re-read sees no difference.
        reader = FrameReader(lambda: [e.BTN_LEFT])      # the finger is still on the button
        for ev in (SCAN, LEFT_DOWN):                     # the end of one read: no SYN_REPORT yet
            for frame in reader.feed(*ev):
                self.emitted.extend(frame)
        for ev in (DROPPED, MOVE, SYN):                  # the next read starts with the overflow
            for frame in reader.feed(*ev):
                self.emitted.extend(frame)
        self.assertIn(LEFT_DOWN, self.emitted, "the press was never written to the virtual mouse")

    def test_ok_clean_overflow_recovers(self):
        # The case 1.6.0 was written for: the release itself was lost and nothing raced the re-read.
        client = FakeClient([DROPPED, MOVE, SYN], phys=set())
        reader = self.start(client, down={e.BTN_LEFT})
        run(client, reader, self.out, self.emitted)
        self.assertEqual(self.out.down, set())
        self.assertEqual(self.emitted, [LEFT_UP])


class FramesKeepOrder(unittest.TestCase):
    def test_no_overflow_frames_are_identical_and_in_order(self):
        rng = random.Random(7)
        stream, expected = [], []
        down = False
        for _ in range(3000):
            frame = [MOVE] * rng.randint(0, 2)
            if rng.random() < 0.2:
                down = not down
                frame = [SCAN, LEFT_DOWN if down else LEFT_UP] + frame
            if frame:
                expected.append(frame)
            stream += frame + [SYN]
        client = FakeClient(stream)
        reader = FrameReader(client.active_keys)
        got = []
        while client.queue:
            reader.next_batch()
            for ev in client.read():            # 64-event reads cut frames anywhere
                got += reader.feed(*ev)
        self.assertEqual(got, expected)


class PassthroughWhenOff(unittest.TestCase):
    """Double-click fix (debounce_ms 0) and Click-Lock off: buttons, MSC_SCAN and motion untouched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctl = Controller(config_path=Path(self.tmp.name) / "config.json", clock=lambda: 0.0)

    def check(self, frames, t0=1.0):
        written = []
        for i, frame in enumerate(frames):
            t = t0 + i * 0.004
            self.ctl.handle_frame(list(frame), t, written.extend)
            self.ctl.handle_flush(t + 0.002, written.extend)
        self.assertEqual(written, [list(f) for f in frames])

    def stream(self, seed, n=2000):
        rng = random.Random(seed)
        out, held = [], set()
        for _ in range(n):
            f = [MOVE] * rng.randint(0, 2)
            if rng.random() < 0.3:
                code = rng.choice((e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE))
                value = 0 if code in held else 1
                (held.discard if code in held else held.add)(code)
                f = [(e.EV_MSC, e.MSC_SCAN, code), (e.EV_KEY, code, value)] + f
            if f:
                out.append(f)
        return out

    def test_defaults_are_passthrough(self):
        self.assertEqual((self.ctl.cfg["debounce_ms"], self.ctl.cfg["clicklock"]), (0, False))
        self.check(self.stream(1))

    def test_long_hold_does_not_engage(self):
        self.check([[SCAN, LEFT_DOWN]] + [[MOVE]] * 3000 + [[SCAN, LEFT_UP]])   # ~12 s held

    def test_passthrough_after_reloads(self):
        self.assertIsNone(self.ctl.set_config({"clicklock": True}))
        self.assertIsNone(self.ctl.set_config({"clicklock": False}))
        self.assertIsNone(self.ctl.set_config({"debounce_ms": 40}))
        self.assertIsNone(self.ctl.set_config({"debounce_ms": 0}))
        self.assertIsNone(self.ctl.set_config({"lock_ms": 250}))
        self.check(self.stream(2))

    def test_suspend_or_screen_lock_stop_sends_nothing(self):
        written = []
        self.ctl.handle_frame([SCAN, LEFT_DOWN], 1.0, written.extend)
        self.ctl.stop_clicklock("the screen was locked")
        self.ctl.handle_flush(1.1, written.extend)
        self.ctl.handle_frame([SCAN, LEFT_UP], 1.2, written.extend)
        self.assertEqual(written, [[SCAN, LEFT_DOWN], [SCAN, LEFT_UP]])


if __name__ == "__main__":
    unittest.main()
