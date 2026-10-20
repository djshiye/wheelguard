import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

from evdev import ecodes as e

from wheelguard import config as C
from wheelguard.control import Controller
from wheelguard.ipc import AlreadyRunning, Server, claim_path


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = Path(self.tmp.name) / "config.json"
        self.ctl = Controller(config_path=self.cfg_path)
        self.server = Server(self.ctl, os.path.join(self.tmp.name, "w.sock"))
        self.addCleanup(self.server.close)

    def connect(self):
        """A client over a real socketpair; returns the client end."""
        a, b = socket.socketpair()
        a.settimeout(1)
        self.server.add_client(b)
        self.addCleanup(a.close)
        return a

    def pump(self):
        self.server.service(self.server.fds())

    def send(self, sock, msg):
        sock.sendall((msg if isinstance(msg, str) else json.dumps(msg)).encode() + b"\n")
        self.pump()

    def recv(self, sock):
        buf = b""
        while not buf.endswith(b"\n"):
            buf += sock.recv(65536)
        return [json.loads(x) for x in buf.splitlines()]

    def rpc(self, sock, msg):
        self.send(sock, msg)
        return self.recv(sock)


class Commands(Harness):
    def test_status(self):
        c = self.connect()
        (st,) = self.rpc(c, {"cmd": "status"})
        self.assertEqual(st["type"], "status")
        self.assertEqual(st["config"]["enabled"], True)

    def test_status_does_not_subscribe(self):
        c = self.connect()
        self.rpc(c, {"cmd": "status"})
        self.ctl.reset_stats()
        c.setblocking(False)
        with self.assertRaises(BlockingIOError):
            c.recv(10)

    def test_subscribe_streams_ticks_and_status(self):
        c = self.connect()
        (st,) = self.rpc(c, {"cmd": "subscribe"})
        self.assertEqual(st["type"], "status")
        self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, 1)], 5.0, lambda f: None)
        (tick,) = self.recv(c)
        self.assertEqual((tick["type"], tick["kind"], tick["sign"]), ("tick", "emit", 1))

    def test_motion_never_on_wire(self):
        c = self.connect()
        self.rpc(c, {"cmd": "subscribe"})
        self.ctl.handle_frame([(e.EV_REL, e.REL_X, 5)], 5.0, lambda f: None)
        c.setblocking(False)
        with self.assertRaises(BlockingIOError):
            c.recv(10)

    def test_set_ok_then_status_to_subscribers(self):
        sub, other = self.connect(), self.connect()
        self.rpc(sub, {"cmd": "subscribe"})
        self.send(other, {"cmd": "set", "config": {"lock_ms": 400, "enabled": False}})
        self.assertEqual(self.recv(other), [{"type": "ok"}])
        (st,) = self.recv(sub)
        self.assertEqual((st["type"], st["config"]["lock_ms"], st["config"]["enabled"]),
                         ("status", 400, False))
        self.assertEqual(C.load(self.cfg_path)["lock_ms"], 400)

    def test_subscribed_requester_gets_ok_before_status(self):
        c = self.connect()
        self.rpc(c, {"cmd": "subscribe"})
        self.send(c, {"cmd": "set", "config": {"lock_ms": 410}})
        msgs = self.recv(c)
        while len(msgs) < 2:
            msgs += self.recv(c)
        self.assertEqual([m["type"] for m in msgs], ["ok", "status"])

    def test_set_validation_errors(self):
        c = self.connect()
        for cfg in ({"hold_ms": 500}, {"nope": 1}, {"pre_ms": -5}, {"lock_ms": 3000}, "x", None):
            with self.subTest(cfg=cfg):
                (r,) = self.rpc(c, {"cmd": "set", "config": cfg})
                self.assertEqual(r["type"], "error")
                self.assertTrue(r["message"])
        (r,) = self.rpc(c, {"cmd": "set"})
        self.assertEqual(r["type"], "error")
        self.assertFalse(self.cfg_path.exists())

    def test_set_merges_with_existing(self):
        c = self.connect()
        self.rpc(c, {"cmd": "set", "config": {"lock_ms": 400}})
        self.rpc(c, {"cmd": "set", "config": {"pre_ms": 80}})
        saved = json.loads(self.cfg_path.read_text())
        self.assertEqual((saved["lock_ms"], saved["pre_ms"], saved["hold_ms"]), (400, 80, 25))

    def test_reload(self):
        c = self.connect()
        self.cfg_path.write_text('{"pre_ms": 90}')
        self.assertEqual(self.rpc(c, {"cmd": "reload"}), [{"type": "ok"}])
        self.assertEqual(self.ctl.cfg["pre_ms"], 90)
        self.cfg_path.write_text("{broken")
        (r,) = self.rpc(c, {"cmd": "reload"})
        self.assertEqual(r["type"], "error")
        self.assertEqual(self.ctl.cfg["pre_ms"], 90)

    def test_reset_stats(self):
        c = self.connect()
        self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, 1)], 5.0, lambda f: None)
        self.assertEqual(self.rpc(c, {"cmd": "reset_stats"}), [{"type": "ok"}])
        self.assertEqual(self.ctl.stats["ticks"], 0)

    def test_bad_input_keeps_connection(self):
        c = self.connect()
        (r,) = self.rpc(c, "not json")
        self.assertEqual(r["type"], "error")
        (r,) = self.rpc(c, "[1,2]")
        self.assertEqual(r["type"], "error")
        (r,) = self.rpc(c, {"cmd": "frobnicate"})
        self.assertIn("frobnicate", r["message"])
        (r,) = self.rpc(c, {"cmd": "status"})
        self.assertEqual(r["type"], "status")

    def test_several_commands_in_one_packet_and_split_lines(self):
        c = self.connect()
        c.sendall(b'{"cmd":"status"}\n{"cmd":"sta')
        self.pump()
        self.assertEqual(len(self.recv(c)), 1)
        c.sendall(b'tus"}\n')
        self.pump()
        self.assertEqual(self.recv(c)[0]["type"], "status")


class Clients(Harness):
    def test_disconnect_removes_client(self):
        c = self.connect()
        self.assertEqual(len(self.server.clients), 1)
        c.close()
        self.pump()
        self.assertEqual(self.server.clients, {})

    def test_overlong_line_disconnects(self):
        c = self.connect()
        c.sendall(b"x" * 70000)
        for _ in range(20):
            self.pump()
        self.assertEqual(self.server.clients, {})

    def test_full_buffer_disconnects_without_blocking(self):
        slow, healthy = self.connect(), self.connect()
        for c in (slow, healthy):
            self.rpc(c, {"cmd": "subscribe"})
        healthy.setblocking(False)
        for i in range(20000):                  # never read from `slow`
            self.ctl.handle_frame([(e.EV_REL, e.REL_WHEEL, 1)], 5.0 + i, lambda f: None)
            if len(self.server.clients) == 1:
                break
            try:
                while healthy.recv(65536):
                    pass
            except BlockingIOError:
                pass
        self.assertEqual(len(self.server.clients), 1, "stalled client should have been dropped")
        self.assertLess(i, 20000 - 1)


class Listening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "w.sock")

    def server(self):
        ctl = Controller(config_path=Path(self.tmp.name) / "config.json")
        return Server(ctl, self.path)

    def test_real_socket_mode_and_cleanup(self):
        srv = self.server()
        srv.start()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        cli = socket.socket(socket.AF_UNIX)
        cli.settimeout(1)
        cli.connect(self.path)
        srv.service(srv.fds())
        cli.sendall(b'{"cmd":"status"}\n')
        srv.service(srv.fds())
        self.assertEqual(json.loads(cli.recv(65536))["type"], "status")
        cli.close()
        srv.close()
        self.assertFalse(os.path.exists(self.path))

    def test_stale_socket_is_replaced(self):
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(self.path)
        stale.close()                          # file remains, nobody listens
        self.assertTrue(os.path.exists(self.path))
        srv = self.server()
        srv.start()
        self.addCleanup(srv.close)
        self.assertTrue(os.path.exists(self.path))

    def test_live_daemon_is_refused(self):
        first = self.server()
        first.start()
        self.addCleanup(first.close)
        with self.assertRaises(AlreadyRunning):
            self.server().start()
        self.assertTrue(os.path.exists(self.path), "must not steal the live socket")

    def test_non_socket_path_is_refused(self):
        Path(self.path).write_text("x")
        with self.assertRaises(OSError):
            claim_path(self.path)
        self.assertTrue(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
