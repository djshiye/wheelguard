"""1.7.0 service changes: monotonic timing, the motion fast path, whole-frame writes, temporary settings
(override), pause, per-button Double-click fix counts, the DPI-aware drag threshold, the screen-lock watch."""
import errno
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from evdev import UInput, ecodes as e

from wheelguard import config as C
from wheelguard import daemon
from wheelguard.clicklock import ClickLock
from wheelguard.control import Controller
from wheelguard.ipc import Server
from wheelguard.screenwatch import ScreenWatch

MOVE = [(e.EV_REL, e.REL_X, 3), (e.EV_REL, e.REL_Y, -1)]
LEFT_DOWN = (e.EV_KEY, e.BTN_LEFT, 1)
LEFT_UP = (e.EV_KEY, e.BTN_LEFT, 0)


def wheel(sign):
    return [(e.EV_REL, e.REL_WHEEL, sign), (e.EV_REL, e.REL_WHEEL_HI_RES, 120 * sign)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.events = []
        self.written = []
        self.ctl = Controller(config_path=self.path, publish=self.events.append)

    def frame(self, events, t):
        return self.ctl.handle_frame(events, t, self.written.extend)


class Clock(Base):
    def test_the_filter_runs_on_the_monotonic_clock(self):
        self.assertIs(self.ctl.clock, time.monotonic)
        self.assertIs(self.ctl.wall, time.time)
        self.assertAlmostEqual(self.ctl.stats["since"], time.time(), delta=5)

    def test_a_wall_clock_step_never_reaches_the_filter(self):
        """What L1 was: with the wall clock set back 30 s, deliberate scrolling the other way was dropped as
        bounce. The session now never reads the wall clock."""
        frames = []
        ctl = mock.MagicMock()
        ctl.deadline = ctl.clicklock_deadline = ctl.debounce_deadline = None
        ctl.paused = False
        ctl.cfg = dict(C.DEFAULTS)
        ctl.handle_frame.side_effect = lambda frame, t, write: frames.append(t) or []
        ctl.handle_flush.side_effect = lambda now, write: frames.append(now) or []

        class Ev:
            type, code, value = e.EV_REL, e.REL_X, 1

            def timestamp(self):
                return 5.0

        class Syn(Ev):
            type, code, value = e.EV_SYN, e.SYN_REPORT, 0

        class Dev:
            fd, name, path, info = 99, "fake", "/dev/input/event9", None
            reads = 0

            def active_keys(self):
                return []

            def grab(self):
                pass

            ungrab = close = grab

            def read(self):
                Dev.reads += 1
                if Dev.reads == 1:
                    raise BlockingIOError(errno.EAGAIN, "empty")      # the drain before the grab
                if Dev.reads == 2:
                    return [Ev(), Syn()]
                raise OSError(errno.ENODEV, "gone")

        ui = mock.MagicMock()
        with mock.patch.object(daemon.UInput, "from_device", return_value=ui), \
                mock.patch.object(daemon, "service", return_value=[99]), \
                mock.patch.object(daemon, "use_monotonic_clock", return_value=True), \
                mock.patch.object(daemon.time, "time", side_effect=AssertionError("wall clock read")), \
                mock.patch.object(daemon.time, "monotonic", return_value=7.0):
            with self.assertRaises(OSError):
                daemon.run_session(Dev(), ctl, server=None, tick_log=lambda d: None)
        self.assertEqual(frames, [5.0, 7.0])        # the kernel's (monotonic) stamp, then our monotonic clock

    def test_our_own_stamp_when_the_kernel_refuses(self):
        class Dev:
            fd = -1
        self.assertFalse(daemon.use_monotonic_clock(Dev()))

    def test_monotonic_ioctl_request(self):
        dev = mock.Mock(fd=5)
        with mock.patch.object(daemon.fcntl, "ioctl") as ioctl:
            self.assertTrue(daemon.use_monotonic_clock(dev))
        fd, req, arg = ioctl.call_args.args
        self.assertEqual((fd, req), (5, 0x400445A0))
        self.assertEqual(int.from_bytes(arg, sys.byteorder), time.CLOCK_MONOTONIC)


class FastPath(Base):
    def test_motion_goes_straight_through(self):
        with mock.patch.object(self.ctl.deb, "process", side_effect=AssertionError("slow path")):
            self.assertEqual(len(self.frame(MOVE, 1.0)), 0)
        self.assertEqual(self.written, [MOVE])
        self.assertEqual(self.events, [])

    def test_not_while_a_press_is_being_timed(self):
        """Click-Lock's "ignore drags" has to see the motion of a held press."""
        self.ctl.set_config({"clicklock": True, "clicklock_ms": 1000})
        self.frame([LEFT_DOWN], 1.0)
        self.assertIsNotNone(self.ctl.cl.pressed_at)
        for i in range(5):
            self.frame([(e.EV_REL, e.REL_X, 100)], 1.01 + i / 100)
        self.assertIsNone(self.ctl.cl.pressed_at)            # 500 counts: a drag, the hold timer is off

    def test_not_while_a_release_is_held_back(self):
        self.ctl.set_config({"debounce_ms": 40})
        self.frame([LEFT_DOWN], 1.0)
        self.frame([LEFT_UP], 1.1)
        self.assertTrue(self.ctl.deb.held)
        self.written.clear()
        self.frame(MOVE, 1.2)                               # the window is over: the release goes out first
        self.assertEqual(self.written, [[LEFT_UP], MOVE])

    def test_not_while_click_lock_runs(self):
        self.ctl.set_config({"clicklock": True, "clicklock_ms": 500})
        self.frame([LEFT_DOWN], 1.0)
        self.ctl.handle_flush(1.6, self.written.extend)
        self.assertTrue(self.ctl.cl.locked)
        self.written.clear()
        self.frame(MOVE, 1.7)                               # a repeat click is due and comes out with it
        self.assertEqual(self.written[-1], MOVE)
        self.assertGreater(len(self.written), 1)

    def test_wheel_and_buttons_take_the_full_path(self):
        self.frame(wheel(1), 1.0)
        self.assertEqual(self.ctl.stats["ticks"], 1)
        self.frame([(e.EV_REL, e.REL_HWHEEL, 1)], 1.1)
        self.assertEqual(self.written[-1], [(e.EV_REL, e.REL_HWHEEL, 1)])

    def test_pending_outbox_goes_first(self):
        self.ctl._outbox.append([LEFT_UP])
        self.frame(MOVE, 1.0)
        self.assertEqual(self.written, [[LEFT_UP], MOVE])


class WholeFrameWrites(unittest.TestCase):
    def ui(self, fd):
        ui = UInput.__new__(UInput)         # a real UInput object without opening /dev/uinput
        ui.fd = fd
        return ui

    def test_one_write_for_all_frames(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        writer = daemon.FrameWriter(self.ui(w))
        self.assertTrue(writer.batched)
        with mock.patch.object(daemon.os, "write", wraps=os.write) as write:
            writer([MOVE, [LEFT_DOWN]])
        self.assertEqual(write.call_count, 1)
        data = os.read(r, 4096)
        size = daemon._EVENT.size
        got = [daemon._EVENT.unpack_from(data, i)[2:] for i in range(0, len(data), size)]
        syn = (e.EV_SYN, e.SYN_REPORT, 0)
        self.assertEqual(got, [*MOVE, syn, LEFT_DOWN, syn])

    def test_event_layout_matches_the_kernel(self):
        self.assertEqual(daemon._EVENT.size, 24 if sys.maxsize > 2 ** 32 else 16)

    def test_falls_back_for_good_if_refused(self):
        ui = self.ui(7)
        ui.write, ui.syn = mock.Mock(), mock.Mock()
        writer = daemon.FrameWriter(ui)
        with mock.patch.object(daemon.os, "write", side_effect=OSError(errno.EINVAL, "no")):
            writer([MOVE])
            self.assertFalse(writer.batched)
            ui.write.assert_not_called()            # the refused frame is not sent twice
            writer([MOVE])
        self.assertEqual(ui.write.call_count, 2)
        ui.syn.assert_called_once()

    def test_a_gone_device_still_raises(self):
        writer = daemon.FrameWriter(self.ui(7))
        with mock.patch.object(daemon.os, "write", side_effect=OSError(errno.ENODEV, "gone")):
            with self.assertRaises(OSError):
                writer([MOVE])

    def test_test_doubles_are_written_event_by_event(self):
        ui = mock.Mock(fd=3)
        writer = daemon.FrameWriter(ui)
        self.assertFalse(writer.batched)
        writer([MOVE])
        self.assertEqual(ui.write.call_count, 2)


class Override(Base):
    def test_applies_without_touching_the_file(self):
        self.ctl.set_config({"lock_ms": 400})
        before = self.path.read_text()
        self.assertIsNone(self.ctl.set_override({"enabled": False, "clicklock": True}, owner="app"))
        self.assertFalse(self.ctl.cfg["enabled"])
        self.assertTrue(self.ctl.cfg["clicklock"])
        self.assertEqual(self.path.read_text(), before)
        st = self.events[-1]
        self.assertEqual(st["override"], {"enabled": False, "clicklock": True})
        self.assertFalse(st["config"]["enabled"])

    def test_empty_ends_it(self):
        self.ctl.set_override({"enabled": False}, owner="app")
        self.ctl.set_override({}, owner="app")
        self.assertTrue(self.ctl.cfg["enabled"])
        self.assertEqual(self.ctl.override, {})
        self.assertIsNone(self.ctl.override_owner)

    def test_a_new_one_replaces_the_old(self):
        self.ctl.set_override({"enabled": False, "clicklock": True}, owner="app")
        self.ctl.set_override({"pre_ms": 80}, owner="app")
        self.assertTrue(self.ctl.cfg["enabled"])
        self.assertFalse(self.ctl.cfg["clicklock"])
        self.assertEqual(self.ctl.cfg["pre_ms"], 80)

    def test_only_the_profile_settings(self):
        for bad in ({"mouse": "046d:c08b"}, {"debounce_ms": 40}, {"vendor": "1234"}, {"nope": 1}):
            self.assertIn("cannot be set temporarily", self.ctl.set_override(bad, owner="app"))
        self.assertEqual(self.ctl.set_override([], owner="app"), "config must be a JSON object")
        self.assertIn("true or false", self.ctl.set_override({"enabled": 1}, owner="app"))
        self.assertIn("hold_ms", self.ctl.set_override({"hold_ms": 900}, owner="app"))
        self.assertEqual(self.ctl.override, {})

    def test_a_hand_change_wins(self):
        self.ctl.set_override({"enabled": False, "clicklock": True}, owner="app")
        self.assertIsNone(self.ctl.set_config({"enabled": True}))
        self.assertTrue(self.ctl.cfg["enabled"])
        self.assertTrue(self.ctl.cfg["clicklock"])             # the rest of the override stays
        self.assertEqual(self.ctl.override, {"clicklock": True})
        self.assertTrue(json.loads(self.path.read_text())["enabled"])
        self.assertFalse(json.loads(self.path.read_text())["clicklock"])

    def test_survives_a_reload_and_follows_the_file_underneath(self):
        self.ctl.set_override({"enabled": False}, owner="app")
        self.path.write_text(json.dumps({"lock_ms": 500}))
        self.assertIsNone(self.ctl.reload())
        self.assertFalse(self.ctl.cfg["enabled"])
        self.assertEqual(self.ctl.cfg["lock_ms"], 500)

    def test_dropped_when_it_no_longer_fits_the_file(self):
        self.ctl.set_override({"hold_ms": 200}, owner="app")
        self.assertIsNone(self.ctl.set_config({"lock_ms": 100}))       # hold 200 can't sit under lock 100
        self.assertEqual(self.ctl.override, {})
        self.assertEqual(self.ctl.cfg["hold_ms"], C.DEFAULTS["hold_ms"])

    def test_switching_click_lock_off_ends_a_running_lock(self):
        self.ctl.set_override({"clicklock": True}, owner="app")
        self.ctl.set_config({"clicklock_ms": 500})
        self.frame([LEFT_DOWN], 1.0)
        self.ctl.handle_flush(1.6, self.written.extend)
        self.assertTrue(self.ctl.cl.locked)
        self.ctl.set_override({}, owner="app")
        self.assertFalse(self.ctl.cl.locked)

    def test_command_line_flags_still_win(self):
        ctl = Controller({"lock_ms": 700}, config_path=self.path)
        ctl.set_override({"lock_ms": 400}, owner="app")
        self.assertEqual(ctl.cfg["lock_ms"], 700)


class OverIpc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctl = Controller(config_path=Path(self.tmp.name) / "config.json")
        self.server = Server(self.ctl, os.path.join(self.tmp.name, "w.sock"))
        self.addCleanup(self.server.close)

    def connect(self):
        a, b = socket.socketpair()
        a.settimeout(1)
        self.server.add_client(b)
        self.addCleanup(a.close)
        return a

    def ask(self, sock, msg):
        sock.sendall(json.dumps(msg).encode() + b"\n")
        self.server.service(self.server.fds())
        return json.loads(sock.recv(65536).split(b"\n")[0])

    def test_override_ends_when_its_client_disconnects(self):
        app, other = self.connect(), self.connect()
        self.assertEqual(self.ask(app, {"cmd": "override", "config": {"enabled": False}}), {"type": "ok"})
        self.assertFalse(self.ask(other, {"cmd": "status"})["config"]["enabled"])
        self.assertEqual(self.ask(other, {"cmd": "status"})["override"], {"enabled": False})
        app.close()
        self.server.service(self.server.fds())
        st = self.ask(other, {"cmd": "status"})
        self.assertTrue(st["config"]["enabled"])
        self.assertEqual(st["override"], {})

    def test_another_client_leaving_changes_nothing(self):
        app, other = self.connect(), self.connect()
        self.ask(app, {"cmd": "override", "config": {"enabled": False}})
        other.close()
        self.server.service(self.server.fds())
        self.assertFalse(self.ctl.cfg["enabled"])

    def test_override_errors(self):
        app = self.connect()
        self.assertEqual(self.ask(app, {"cmd": "override", "config": {"mouse": "auto"}})["type"], "error")
        self.assertEqual(self.ask(app, {"cmd": "override"})["type"], "error")

    def test_pause_and_resume(self):
        app = self.connect()
        self.assertEqual(self.ask(app, {"cmd": "pause", "on": True}), {"type": "ok"})
        self.assertTrue(self.ctl.paused)
        self.assertTrue(self.ctl.wake)
        self.assertTrue(self.ask(app, {"cmd": "status"})["paused"])
        self.assertEqual(self.ask(app, {"cmd": "pause", "on": False}), {"type": "ok"})
        self.assertFalse(self.ctl.paused)
        self.assertEqual(self.ask(app, {"cmd": "pause", "on": "yes"})["type"], "error")
        self.assertEqual(self.ask(app, {"cmd": "pause"})["type"], "error")


class Pause(Base):
    def test_a_running_session_lets_go_of_the_mouse(self):
        ctl = mock.MagicMock()
        ctl.deadline = ctl.clicklock_deadline = ctl.debounce_deadline = None
        ctl.cfg = dict(C.DEFAULTS)
        ctl.paused = False
        ctl.restart_requested = False
        log = []

        class Dev:
            fd, name, path, info = 99, "fake", "/dev/input/event9", None

            def active_keys(self):
                return []

            def read(self):
                raise BlockingIOError(errno.EAGAIN, "empty")

            def grab(self):
                log.append("grab")

            def ungrab(self):
                log.append("ungrab")

            def close(self):
                log.append("close")

        def service(c, server, timeout, extra):
            c.paused = True                 # the pause command arrives
            return []

        with mock.patch.object(daemon.UInput, "from_device", return_value=mock.MagicMock()), \
                mock.patch.object(daemon, "service", side_effect=service):
            self.assertTrue(daemon.run_session(Dev(), ctl, server=None, tick_log=lambda d: None))
        self.assertEqual(log, ["grab", "ungrab", "close"])

    def test_paused_is_never_saved(self):
        self.ctl.set_paused(True)
        self.assertFalse(self.path.exists())
        self.assertFalse(Controller(config_path=self.path).paused)

    def test_idle_ends_at_once_on_a_wake(self):
        self.ctl.wake = True
        t0 = time.monotonic()
        with mock.patch.object(daemon, "service", return_value=[]):
            daemon.idle(self.ctl, None, 30)
        self.assertLess(time.monotonic() - t0, 1)


class Counts(Base):
    def test_double_click_fix_counts_per_button(self):
        self.ctl.set_config({"debounce_ms": 40})
        t = 1.0
        for code, bounces in ((e.BTN_LEFT, 2), (e.BTN_MIDDLE, 1)):
            self.frame([(e.EV_KEY, code, 1)], t)
            for _ in range(bounces):
                self.frame([(e.EV_KEY, code, 0)], t + 0.010)
                self.frame([(e.EV_KEY, code, 1)], t + 0.015)
                t += 0.02
            self.frame([(e.EV_KEY, code, 0)], t + 0.5)
            t += 1
        st = self.ctl.status()["stats"]
        self.assertEqual(st["debounced"], 3)
        self.assertEqual(st["debounced_by"], {"left": 2, "right": 0, "middle": 1})
        self.ctl.reset_stats()
        self.assertEqual(self.ctl.status()["stats"]["debounced_by"], {"left": 0, "right": 0, "middle": 0})

    def test_status_copy_is_not_the_live_counter(self):
        st = self.ctl.status()
        st["stats"]["debounced_by"]["left"] = 99
        self.assertEqual(self.ctl.stats["debounced_by"]["left"], 0)


class DragCounts(Base):
    def test_threshold_follows_the_setting(self):
        for counts, drags in ((200, True), (1000, False)):
            cl = ClickLock(True, 1000, "repeat", 10, 0, True, counts)
            cl.process([LEFT_DOWN], 1.0)
            cl.process([(e.EV_REL, e.REL_X, 500)], 1.1)
            self.assertEqual(cl.pressed_at is None, drags)

    def test_config_key(self):
        self.assertEqual(C.DEFAULTS["clicklock_drag_counts"], 400)
        self.assertIsNone(self.ctl.set_config({"clicklock_drag_counts": 800}))
        self.assertEqual(self.ctl.cl.drag_counts, 800)
        for bad in (10, 50000, "x"):
            self.assertIsNotNone(self.ctl.set_config({"clicklock_drag_counts": bad}))

    def test_changing_it_keeps_a_running_lock(self):
        self.ctl.set_config({"clicklock": True, "clicklock_ms": 500})
        self.frame([LEFT_DOWN], 1.0)
        self.ctl.handle_flush(1.6, self.written.extend)
        self.ctl.set_config({"clicklock_drag_counts": 800})
        self.assertTrue(self.ctl.cl.locked)


class Ident(Base):
    def test_status_names_the_filtered_mouse(self):
        self.assertIsNone(self.ctl.status()["ident"])
        self.ctl.set_device(True, "Mouse", None, ident=(0x3367, 0x1970))
        self.assertEqual(self.ctl.status()["ident"], "3367:1970")
        self.ctl.set_device(False, None, "mouse disconnected")
        self.assertIsNone(self.ctl.status()["ident"])


class SaveWithoutFsync(Base):
    def test_ipc_saves_do_not_wait_for_the_disk(self):
        with mock.patch.object(C.os, "fsync") as fsync:
            self.assertIsNone(self.ctl.set_config({"lock_ms": 400}))
            fsync.assert_not_called()
            C.save(C.normalize({}), self.path)
            fsync.assert_called_once()
        self.assertEqual(C.load(self.path)["lock_ms"], 300)


class LockWatch(unittest.TestCase):
    def watch(self, script):
        w = ScreenWatch(command=("sh", "-c", script))
        self.addCleanup(w.close)
        return w

    def wait(self, w, want, tries=200):
        for _ in range(tries):
            if w.drain() == want and (want or w.fileno() is None):
                return True
            if want is False and w.fileno() is None:
                return True
            time.sleep(0.01)
        return False

    def test_sees_the_screen_lock(self):
        w = self.watch("echo 'Monitoring signals on object /org/gnome/ScreenSaver owned by org.gnome.ScreenSaver';"
                       "echo '/org/gnome/ScreenSaver: org.gnome.ScreenSaver.ActiveChanged (true,)'; sleep 30")
        self.assertIsNotNone(w.fileno())
        self.assertTrue(self.wait(w, True))
        self.assertFalse(w.drain())                 # reported once

    def test_unlock_and_other_lines_are_ignored(self):
        w = self.watch("echo '/org/gnome/ScreenSaver: org.gnome.ScreenSaver.ActiveChanged (false,)';"
                       "echo '/org/gnome/ScreenSaver: org.gnome.ScreenSaver.WakeUpScreen ()'")
        seen = False
        for _ in range(200):
            seen = seen or w.drain()
            if w.fileno() is None:
                break
            time.sleep(0.01)
        self.assertFalse(seen)
        self.assertIsNone(w.fileno())               # the watcher ended and was cleaned up

    def test_missing_gdbus_is_a_no_op(self):
        w = ScreenWatch(command=("/nonexistent/gdbus",))
        self.assertIsNone(w.fileno())
        self.assertFalse(w.drain())
        w.start()
        self.assertIsNone(w.fileno())
        w.close()

    def _until_ended(self, w):
        for _ in range(200):
            w.drain()
            if w.fileno() is None:
                return
            time.sleep(0.01)
        self.fail("the watcher never ended")

    def test_a_watcher_that_cannot_run_is_tried_ever_more_rarely(self):
        now = [0.0]
        w = ScreenWatch(command=("true",), clock=lambda: now[0])
        self.addCleanup(w.close)
        self._until_ended(w)                        # ended at once: wait 60 s, not 30
        now[0] = 31.0
        w.start()
        self.assertIsNone(w.fileno())               # too soon
        now[0] = 61.0
        w.start()
        self.assertIsNotNone(w.fileno())
        self._until_ended(w)                        # and again: now 120 s
        now[0] = 61.0 + 119.0
        w.start()
        self.assertIsNone(w.fileno())
        now[0] = 61.0 + 121.0
        w.start()
        self.assertIsNotNone(w.fileno())

    def test_a_watcher_that_had_been_working_comes_back_soon(self):
        now = [0.0]
        w = ScreenWatch(command=("true",), clock=lambda: now[0])
        self.addCleanup(w.close)
        now[0] = 500.0                              # it "ran" for a long time before ending
        self._until_ended(w)
        now[0] = 531.0
        w.start()
        self.assertIsNotNone(w.fileno())

    def test_session_stops_click_lock_when_the_screen_locks(self):
        ctl = mock.MagicMock()
        ctl.deadline = ctl.clicklock_deadline = ctl.debounce_deadline = None
        ctl.cfg = dict(C.DEFAULTS)
        ctl.paused = False
        ctl.restart_requested = False
        lockwatch = mock.Mock()
        lockwatch.fileno.return_value = 55
        lockwatch.drain.return_value = True

        class Dev:
            fd, name, path, info = 99, "fake", "/dev/input/event9", None
            reads = 0

            def active_keys(self):
                return []

            def grab(self):
                pass

            ungrab = close = grab

            def read(self):
                raise BlockingIOError(errno.EAGAIN, "empty")

        calls = []

        def service(c, server, timeout, extra):
            calls.append(list(extra))
            if len(calls) > 1:
                raise OSError(errno.ENODEV, "gone")
            return [55]

        with mock.patch.object(daemon.UInput, "from_device", return_value=mock.MagicMock()), \
                mock.patch.object(daemon, "service", side_effect=service):
            with self.assertRaises(OSError):
                daemon.run_session(Dev(), ctl, None, lambda d: None, None, lockwatch)
        self.assertIn(55, calls[0])
        ctl.stop_clicklock.assert_called_once_with("the screen locked")


if __name__ == "__main__":
    unittest.main()
