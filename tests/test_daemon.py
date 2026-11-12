import errno
import unittest
from unittest import mock

import evdev
from evdev import ecodes as e

from wheelguard import daemon
from wheelguard.daemon import FrameReader, describe

SYN = (e.EV_SYN, e.SYN_REPORT, 0)
DROPPED = (e.EV_SYN, e.SYN_DROPPED, 0)
LEFT_DOWN = (e.EV_KEY, e.BTN_LEFT, 1)
LEFT_UP = (e.EV_KEY, e.BTN_LEFT, 0)
MOVE = (e.EV_REL, e.REL_X, 3)


class Describe(unittest.TestCase):
    def test_uinput_not_writable(self):
        ex = evdev.UInputError('"/dev/uinput" cannot be opened for writing')
        self.assertEqual(describe(ex), "permission denied: /dev/uinput")

    def test_other_uinput_error(self):
        self.assertIn("not a character", describe(evdev.UInputError("not a character device")))

    def test_permission_denied_with_filename(self):
        ex = PermissionError(errno.EACCES, "Permission denied", "/dev/input/event2")
        self.assertEqual(describe(ex), "permission denied: /dev/input/event2")

    def test_device_gone(self):
        self.assertEqual(describe(OSError(errno.ENODEV, "No such device")), "mouse disconnected")


def feed(reader, *events):
    frames = []
    for ev in events:
        frames += reader.feed(*ev)
    return frames


class Reader(unittest.TestCase):
    def test_frames_split_on_syn_report(self):
        r = FrameReader(lambda: [])
        self.assertEqual(feed(r, MOVE, SYN, LEFT_DOWN, SYN), [[MOVE], [LEFT_DOWN]])

    def test_lost_release_is_replayed(self):
        # The kernel dropped events and the release was among them: the device says the button is
        # up, so a release is replayed. 1.4.0 left the button pressed here.
        device_keys = []
        r = FrameReader(lambda: device_keys)
        feed(r, LEFT_DOWN, SYN)
        frames = feed(r, DROPPED, LEFT_UP, MOVE, SYN)
        self.assertEqual(frames, [[LEFT_UP]])
        self.assertEqual(r.state.down, set())

    def test_lost_press_is_replayed(self):
        r = FrameReader(lambda: [e.BTN_LEFT])
        self.assertEqual(feed(r, DROPPED, MOVE, SYN), [[LEFT_DOWN]])
        # The real press, read in the same batch, is part of the re-read and isn't sent a second time.
        self.assertEqual(feed(r, LEFT_DOWN, SYN), [])
        r.next_batch()
        self.assertEqual(feed(r, LEFT_UP, SYN), [[LEFT_UP]])

    def test_nothing_lost_nothing_replayed(self):
        r = FrameReader(lambda: [])
        self.assertEqual(feed(r, DROPPED, MOVE, SYN), [])
        self.assertEqual(feed(r, MOVE, SYN), [[MOVE]])

    def test_device_gone_during_reread(self):
        def gone():
            raise OSError(errno.ENODEV, "No such device")
        r = FrameReader(gone)
        self.assertEqual(feed(r, DROPPED, SYN), [])


class FakeDevice:
    """Reports the left button held for the first `held_polls` checks."""

    def __init__(self, held_polls):
        self.held_polls = held_polls
        self.fd = 99
        self.grabbed = False

    def active_keys(self):
        if self.held_polls > 0:
            self.held_polls -= 1
            return [e.BTN_LEFT]
        return []

    def read(self):
        return []

    def grab(self):
        if self.held_polls:
            raise AssertionError("grabbed while a button was held")
        self.grabbed = True


class GrabWhileHeld(unittest.TestCase):
    def test_waits_for_the_release_before_grabbing(self):
        # The mouse woke up with a click: the desktop saw the press on the real device. Grabbing now
        # would swallow the release and leave the desktop's button stuck (Dino's 9 Nov report).
        dev = FakeDevice(held_polls=3)
        with mock.patch.object(daemon, "service", return_value=[dev.fd]) as service:
            daemon.wait_for_buttons_up(dev, ctl=None, server=None)
        self.assertEqual(service.call_count, 3)          # IPC kept being answered while waiting
        dev.grab()
        self.assertTrue(dev.grabbed)

    def test_no_wait_when_nothing_is_held(self):
        dev = FakeDevice(held_polls=0)
        with mock.patch.object(daemon, "service") as service:
            daemon.wait_for_buttons_up(dev, ctl=None, server=None)
        service.assert_not_called()

    def test_keyboard_keys_count_too(self):
        dev = FakeDevice(held_polls=0)
        dev.active_keys = lambda: [e.KEY_A]
        self.assertEqual(daemon.buttons_down(dev), [e.KEY_A])

    def test_waits_for_a_held_key(self):
        keys = iter([[e.KEY_A], [e.KEY_A], []])

        class Dev(FakeDevice):
            def active_keys(self):
                return next(keys)
        dev = Dev(held_polls=0)
        with mock.patch.object(daemon, "service", return_value=[]) as service:
            daemon.wait_for_buttons_up(dev, ctl=None, server=None)
        self.assertEqual(service.call_count, 2)

    def test_key_held_at_the_grab_lets_go_again(self):
        keys = iter([[], [e.KEY_A], [e.KEY_A], [], []])
        calls = []

        class Dev:
            fd = 99

            def active_keys(self):
                return next(keys)

            def read(self):
                raise BlockingIOError(errno.EAGAIN, "empty")

            def grab(self):
                calls.append("grab")

            def ungrab(self):
                calls.append("ungrab")
        with mock.patch.object(daemon, "service", return_value=[]):
            daemon.grab_when_buttons_up(Dev(), ctl=None, server=None)
        self.assertEqual(calls, ["grab", "ungrab", "grab"])


class GrabRace(unittest.TestCase):
    def test_press_right_at_the_grab_lets_go_and_waits(self):
        # The button went down after the last check but before the grab: the desktop saw that press,
        # so the grab must not keep the mouse until the release has reached the desktop.
        keys = iter([[], [e.BTN_LEFT], [e.BTN_LEFT], [], []])
        calls = []

        class Dev:
            fd = 99

            def active_keys(self):
                return next(keys)

            def read(self):
                raise BlockingIOError(errno.EAGAIN, "empty")

            def grab(self):
                calls.append("grab")

            def ungrab(self):
                calls.append("ungrab")

        with mock.patch.object(daemon, "service", return_value=[]):
            daemon.grab_when_buttons_up(Dev(), ctl=None, server=None)
        self.assertEqual(calls, ["grab", "ungrab", "grab"])

    def test_events_queued_before_the_grab_are_not_replayed(self):
        # The desktop already had them; sending them again through the virtual mouse would double a
        # click, a scroll tick or a cursor jump.
        dev = FakeDevice(held_polls=0)
        queued = [[Event(*LEFT_DOWN), Event(*SYN), Event(*LEFT_UP), Event(*SYN)]]
        after = [[Event(*MOVE), Event(*SYN)]]

        def read():
            if queued:
                return queued.pop(0)
            if dev.grabbed and after:
                return after.pop(0)
            if dev.grabbed:
                raise OSError(errno.ENODEV, "No such device")
            raise BlockingIOError(errno.EAGAIN, "empty")
        dev.read = read
        dev.name, dev.path = "fake", "/dev/input/event9"
        dev.ungrab = dev.close = lambda: None
        ui = FakeUInput()
        ctl = mock.MagicMock()
        ctl.deadline = ctl.clicklock_deadline = ctl.debounce_deadline = ctl.next_deadline = None
        ctl.cfg = {"lock_ms": 300, "pre_ms": 60, "hold_ms": 25, "clicklock": False,
                   "clicklock_ms": 5000, "clicklock_mode": "repeat"}
        ctl.handle_frame.side_effect = lambda frame, t, write: write([frame]) or []
        ctl.handle_flush.return_value = []
        with mock.patch.object(daemon.UInput, "from_device", return_value=ui), \
                mock.patch.object(daemon, "service", return_value=[dev.fd]):
            with self.assertRaises(OSError):
                daemon.run_session(dev, ctl, server=None, tick_log=lambda d: None)
        self.assertEqual(ui.log, [MOVE, "syn", "close"])


class Event:
    def __init__(self, etype, code, value):
        self.type, self.code, self.value = etype, code, value

    def timestamp(self):
        return 1.0


class FakeUInput:
    def __init__(self):
        self.log = []

    def write(self, etype, code, value):
        self.log.append((etype, code, value))

    def syn(self):
        self.log.append("syn")

    def close(self):
        self.log.append("close")


class SessionEnd(unittest.TestCase):
    def test_pressed_button_is_released_when_the_session_ends(self):
        dev = FakeDevice(held_polls=0)
        batches = [[Event(*LEFT_DOWN), Event(*SYN)]]

        def read():
            if not dev.grabbed:
                raise BlockingIOError(errno.EAGAIN, "nothing queued yet")
            if batches:
                return batches.pop(0)
            raise OSError(errno.ENODEV, "No such device")    # unplugged with the button down
        dev.read = read
        dev.name, dev.path = "fake", "/dev/input/event9"
        dev.ungrab = dev.close = lambda: None
        ui = FakeUInput()
        ctl = mock.MagicMock()
        ctl.deadline = ctl.clicklock_deadline = ctl.debounce_deadline = ctl.next_deadline = None
        ctl.cfg = {"lock_ms": 300, "pre_ms": 60, "hold_ms": 25, "clicklock": False,
                   "clicklock_ms": 5000, "clicklock_mode": "repeat"}
        ctl.handle_frame.side_effect = lambda frame, t, write: write([frame]) or []
        ctl.handle_flush.return_value = []
        with mock.patch.object(daemon.UInput, "from_device", return_value=ui), \
                mock.patch.object(daemon, "service", return_value=[dev.fd]):
            with self.assertRaises(OSError):
                daemon.run_session(dev, ctl, server=None, tick_log=lambda d: None)
        self.assertEqual(ui.log, [LEFT_DOWN, "syn", LEFT_UP, "syn", "close"])


if __name__ == "__main__":
    unittest.main()
