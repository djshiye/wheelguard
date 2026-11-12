"""1.9.1: the session loop does less for every report of the mouse. Same events out, in the same order."""
import errno
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import evdev
from evdev import InputEvent, ecodes as e

from wheelguard import config as C
from wheelguard import daemon
from wheelguard.control import HOUSEKEEPING_S, Controller
from wheelguard.ipc import Server

EV = struct.Struct("llHHi")
SYN = (e.EV_SYN, e.SYN_REPORT, 0)


def packed(events, t=0.0):
    sec, usec = int(t), int(round((t - int(t)) * 1e6))
    return b"".join(EV.pack(sec, usec, *ev) for ev in events)


class PipeMouse(evdev.InputDevice):
    """A real InputDevice as far as the daemon can tell (so its bytes are read directly), fed from a pipe."""
    name, path, info = "Pipe Mouse", "/dev/input/event99", None

    def __init__(self):                             # not super().__init__(): that opens a device node
        self.fd, self.feed_fd = os.pipe()
        os.set_blocking(self.fd, False)
        self.grabbed = False
        self.on_grab = None             # what the mouse does once the daemon has taken it over

    def __del__(self):
        pass

    def active_keys(self):
        return []

    def grab(self):
        self.grabbed = True
        if self.on_grab:
            self.on_grab()

    def ungrab(self):
        self.grabbed = False

    def close(self):
        for fd in (self.fd, self.feed_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    def put(self, events, t=0.0):
        os.write(self.feed_fd, packed(events, t))

    def unplug(self):
        os.close(self.feed_fd)


class StandIn:
    """The same pipe read the way python-evdev reads a device: one InputEvent per event."""
    name, path, info = PipeMouse.name, PipeMouse.path, None

    def __init__(self, real):
        self._real, self.fd = real, real.fd

    def read(self):
        data = os.read(self.fd, 64 * EV.size)
        if not data:
            raise OSError(errno.ENODEV, "gone")
        for sec, usec, etype, code, value in EV.iter_unpack(data):
            yield InputEvent(sec, usec, etype, code, value)

    def __getattr__(self, name):
        return getattr(self._real, name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.now = [100.0]
        self.power = mock.Mock()
        self.power.poll.return_value = False
        self.ctl = Controller(config_path=self.path, clock=lambda: self.now[0], power=self.power)

    def write(self, frames):
        self.out = getattr(self, "out", []) + [ev for f in frames for ev in f]


class ReadEvents(unittest.TestCase):
    def setUp(self):
        self.r, self.w = os.pipe()
        os.set_blocking(self.r, False)
        self.addCleanup(lambda: [os.close(fd) for fd in (self.r,)])

    def test_whole_events_as_bytes(self):
        data = packed([(e.EV_REL, e.REL_X, 5), SYN, (e.EV_KEY, e.BTN_LEFT, 1), SYN], 12.5)
        os.write(self.w, data)
        got = daemon.read_events(self.r)
        self.assertEqual(got, data)
        self.assertEqual([ev[2:] for ev in EV.iter_unpack(got)],
                         [(e.EV_REL, e.REL_X, 5), SYN, (e.EV_KEY, e.BTN_LEFT, 1), SYN])
        os.close(self.w)

    def test_nothing_waiting(self):
        self.assertEqual(daemon.read_events(self.r), b"")
        os.close(self.w)

    def test_half_an_event_is_never_handed_out(self):
        os.write(self.w, packed([(e.EV_REL, e.REL_X, 5)]) + b"\x01\x02\x03")
        self.assertEqual(daemon.read_events(self.r), packed([(e.EV_REL, e.REL_X, 5)]))
        os.close(self.w)

    def test_a_gone_mouse_raises_like_evdev(self):
        os.close(self.w)
        with self.assertRaises(OSError) as cm:
            daemon.read_events(self.r)
        self.assertEqual(cm.exception.errno, errno.ENODEV)
        self.assertEqual(daemon.describe(cm.exception), "mouse disconnected")

    def test_takes_more_than_one_evdev_read(self):
        many = [(e.EV_REL, e.REL_X, 1), SYN] * 100             # python-evdev stops at 64 events
        os.write(self.w, packed(many))
        self.assertEqual(len(daemon.read_events(self.r)), 200 * EV.size)
        os.close(self.w)

    def test_event_size_is_the_kernels(self):
        self.assertEqual(daemon.READ_BYTES % EV.size, 0)
        self.assertEqual(daemon._EVENT.size, EV.size)


class Session(Base):
    """run_session() on a pipe: the direct read and the python-evdev way must write exactly the same."""
    STORY = [
        ([(e.EV_REL, e.REL_X, 3), (e.EV_REL, e.REL_Y, -2), SYN], 1.000),
        ([(e.EV_KEY, e.BTN_LEFT, 1), SYN, (e.EV_KEY, e.BTN_LEFT, 0), SYN], 1.010),
        ([(e.EV_REL, e.REL_WHEEL, -1), (e.EV_REL, e.REL_WHEEL_HI_RES, -120), SYN], 1.100),
        ([(e.EV_REL, e.REL_WHEEL, 1), (e.EV_REL, e.REL_WHEEL_HI_RES, 120), SYN], 1.105),        # a bounce: dropped
        ([(e.EV_REL, e.REL_WHEEL, -1), (e.EV_REL, e.REL_WHEEL_HI_RES, -120), SYN], 1.150),
        ([(e.EV_REL, e.REL_X, 1), SYN, (e.EV_REL, e.REL_X, 1), SYN, (e.EV_REL, e.REL_X, 1)], 1.200),   # ends mid-frame
        ([SYN, (e.EV_KEY, e.BTN_RIGHT, 1), SYN], 1.210),
    ]

    def play(self, direct, stamps):
        mouse = PipeMouse()
        self.addCleanup(mouse.close)
        dev = mouse if direct else StandIn(mouse)
        mouse.put([(e.EV_REL, e.REL_X, 99), SYN])       # before the grab: the desktop had it, never replayed

        def story():
            for events, t in self.STORY:
                mouse.put(events, t)
            mouse.unplug()

        mouse.on_grab = story
        ui = mock.MagicMock()
        server = Server(self.ctl, str(Path(self.tmp.name) / f"s{direct}{stamps}.sock"))
        seen = []
        real = self.ctl.handle_frame

        def handle_frame(frame, t, write):
            seen.append((list(frame), round(t, 6)))
            return real(frame, t, write)

        self.ctl.handle_frame = handle_frame
        with mock.patch.object(daemon.UInput, "from_device", return_value=ui), \
                mock.patch.object(daemon, "use_monotonic_clock", return_value=stamps), \
                mock.patch.object(daemon.time, "monotonic", return_value=1.3):
            with self.assertRaises(OSError) as cm:
                daemon.run_session(dev, self.ctl, server, lambda d: None)
        self.assertEqual(cm.exception.errno, errno.ENODEV)
        self.assertFalse(mouse.grabbed)                         # let go of on the way out
        written = [c.args for c in ui.write.call_args_list]
        return written, seen

    def test_direct_read_writes_what_the_evdev_read_writes(self):
        for stamps in (True, False):
            with self.subTest(kernel_stamps=stamps):
                self.setUp()
                direct = self.play(True, stamps)
                self.setUp()
                standin = self.play(False, stamps)
                self.assertEqual(direct, standin)
                self.assertGreater(len(direct[0]), 10)

    def test_what_comes_out(self):
        written, seen = self.play(True, True)
        self.assertEqual(seen[0], ([(e.EV_REL, e.REL_X, 3), (e.EV_REL, e.REL_Y, -2)], 1.0))
        self.assertNotIn((e.EV_REL, e.REL_X, 99), written)
        self.assertEqual([t for _f, t in seen], [1.0, 1.01, 1.01, 1.1, 1.105, 1.15, 1.2, 1.2, 1.21, 1.21])
        self.assertIn((e.EV_KEY, e.BTN_LEFT, 1), written)
        self.assertIn((e.EV_KEY, e.BTN_LEFT, 0), written)
        wheel = [w for w in written if w[:2] == (e.EV_REL, e.REL_WHEEL)]
        self.assertEqual(wheel, [(e.EV_REL, e.REL_WHEEL, -1)] * 2)             # the bounce never got out
        # the right button was still down when the mouse went: released on the way out
        self.assertEqual([w for w in written if w[1] == e.BTN_RIGHT][-1], (e.EV_KEY, e.BTN_RIGHT, 0))

    def test_our_own_stamp_when_the_kernel_refuses(self):
        _written, seen = self.play(True, False)
        self.assertEqual({t for _f, t in seen}, {1.3})


class Housekeeping(Base):
    def test_slow_checks_are_spaced_out_when_nothing_is_pending(self):
        for _ in range(50):                                     # 50 reports within a few milliseconds
            self.now[0] += 0.0001
            self.assertEqual(len(self.ctl.handle_flush(self.now[0], self.write)), 0)
        self.assertEqual(self.power.poll.call_count, 1)
        self.now[0] += HOUSEKEEPING_S
        self.ctl.handle_flush(self.now[0], self.write)
        self.assertEqual(self.power.poll.call_count, 2)

    def test_a_held_tick_is_released_on_time(self):
        C.save(C.normalize({"pre_ms": 0, "hold_ms": 25, "lock_ms": 300}), self.path)
        self.ctl.reload()
        self.ctl.handle_flush(100.0, self.write)                # the slow checks were just made
        self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, 1)], 100.001, self.write)
        self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, -1)], 100.010, self.write)      # a reversal: held
        self.assertEqual(self.ctl.next_deadline, 100.010 + 0.025)
        self.assertEqual(len(self.ctl.handle_flush(100.020, self.write)), 0)
        decisions = self.ctl.handle_flush(100.036, self.write)
        self.assertEqual([d.kind for d in decisions], ["emit"])
        self.assertIsNone(self.ctl.next_deadline)

    def test_a_suspend_still_ends_click_lock_at_once(self):
        C.save(C.normalize({"clicklock": True, "clicklock_ms": 500, "clicklock_mode": "hold"}), self.path)
        self.ctl.reload()
        self.ctl.handle_flush(100.0, self.write)
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_LEFT, 1)], 100.001, self.write)
        self.ctl.handle_flush(100.6, self.write)
        self.assertTrue(self.ctl.cl.active)
        self.power.poll.return_value = True                     # the lid was closed and opened
        self.ctl.handle_flush(100.601, self.write)              # the very next round, not 0.25 s later
        self.assertFalse(self.ctl.cl.active)

    def test_what_a_client_changed_goes_out_at_once(self):
        C.save(C.normalize({"clicklock": True, "clicklock_ms": 500, "clicklock_mode": "hold"}), self.path)
        self.ctl.reload()
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_LEFT, 1)], 100.0, self.write)
        self.ctl.handle_flush(100.6, self.write)
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_LEFT, 0)], 100.7, self.write)          # finger off, button stays down
        self.out = []
        self.ctl.stop_clicklock("asked to stop")
        self.ctl.handle_flush(100.701, self.write)
        self.assertEqual(self.out, [(e.EV_KEY, e.BTN_LEFT, 0)])


class NextDeadline(Base):
    def both(self):
        three = [d for d in (self.ctl.deadline, self.ctl.clicklock_deadline, self.ctl.debounce_deadline) if d is not None]
        self.assertEqual(self.ctl.next_deadline, min(three) if three else None)
        return self.ctl.next_deadline

    def test_is_the_earliest_of_the_three(self):
        C.save(C.normalize({"pre_ms": 0, "clicklock": True, "clicklock_ms": 500, "debounce_ms": 40}), self.path)
        self.ctl.reload()
        self.assertIsNone(self.both())
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_LEFT, 1)], 100.0, self.write)          # Click-Lock starts timing
        self.assertEqual(self.both(), 100.5)
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_RIGHT, 1)], 100.1, self.write)
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_RIGHT, 0)], 100.2, self.write)         # held back by the Double-click fix
        self.assertEqual(self.both(), 100.2 + 0.04)
        self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, 1)], 100.21, self.write)
        self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, -1)], 100.215, self.write)      # a held reversal
        self.assertEqual(self.both(), 100.215 + 0.025)
        self.ctl.handle_flush(100.3, self.write)
        self.assertEqual(self.both(), 100.5)
        self.ctl.handle_frame([(e.EV_KEY, e.BTN_LEFT, 0)], 100.31, self.write)
        self.ctl.handle_flush(100.4, self.write)
        self.assertIsNone(self.both())


class ServerFds(Base):
    def test_same_list_until_a_client_comes_or_goes(self):
        import socket
        server = Server(self.ctl, str(Path(self.tmp.name) / "s.sock"))
        server.start()
        self.addCleanup(server.close)
        first = server.fds()
        self.assertIs(server.fds(), first)
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        client = server.add_client(b)
        second = server.fds()
        self.assertIsNot(second, first)
        self.assertEqual(sorted(second), sorted([server.listener.fileno(), b.fileno()]))
        self.assertIs(server.fds(), second)
        server._drop(client)
        self.assertEqual(server.fds(), [server.listener.fileno()])

    def test_an_event_is_encoded_once_for_all_listeners(self):
        import socket
        server = Server(self.ctl, str(Path(self.tmp.name) / "s.sock"))
        pairs = [socket.socketpair() for _ in range(3)]
        for a, b in pairs:
            self.addCleanup(a.close)
            self.addCleanup(b.close)
            server.add_client(b).subscribed = True
        with mock.patch("wheelguard.ipc.encode", wraps=__import__("wheelguard.ipc").ipc.encode) as enc:
            server.publish({"type": "tick", "sign": 1})
        self.assertEqual(enc.call_count, 1)
        for a, _b in pairs:
            self.assertEqual(a.recv(100), b'{"type":"tick","sign":1}\n')


if __name__ == "__main__":
    unittest.main()
