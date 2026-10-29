"""Non-visual tests for the settings GUI: presets, gsettings parsing, protocol client logic.

The pure modules (presets, gsettings, protocol) need no Qt. The DaemonClient tests need PySide6 and
are skipped when it is not importable.
"""
import json
import contextlib
import io
import os
import socket
import tempfile
import threading
import unittest

from wheelguard.gui import gsettings, presets, protocol

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui import backend
    HAVE_QT = True
except ImportError:  # system python without PySide6
    HAVE_QT = False

# Output of `gsettings list-recursively org.gnome.desktop.peripherals.mouse` on GNOME 49 / Fedora 44.
LIST_RECURSIVELY = """\
org.gnome.desktop.peripherals.mouse accel-profile 'flat'
org.gnome.desktop.peripherals.mouse double-click 400
org.gnome.desktop.peripherals.mouse drag-threshold 8
org.gnome.desktop.peripherals.mouse left-handed false
org.gnome.desktop.peripherals.mouse middle-click-emulation false
org.gnome.desktop.peripherals.mouse natural-scroll true
org.gnome.desktop.peripherals.mouse speed 0.30472103004291839
"""


class PresetTests(unittest.TestCase):
    def test_values_match_spec(self):
        got = {p.name: (p.pre_ms, p.hold_ms, p.lock_ms) for p in presets.PRESETS}
        self.assertEqual(got, {
            "Light": (40, 15, 250),
            "Balanced": (60, 25, 300),
            "Strong": (90, 35, 400),
            "Maximum": (130, 50, 500),
        })
        self.assertEqual(presets.PRESETS[presets.DEFAULT_PRESET].name, "Balanced")

    def test_every_preset_is_valid_for_the_daemon(self):
        for p in presets.PRESETS:
            self.assertTrue(0 < p.hold_ms < p.lock_ms, p)
            self.assertTrue(0 <= p.pre_ms <= p.lock_ms, p)

    def test_index_and_name(self):
        for i, p in enumerate(presets.PRESETS):
            self.assertEqual(presets.preset_index(p.pre_ms, p.hold_ms, p.lock_ms), i)
            self.assertEqual(presets.preset_name(p.pre_ms, p.hold_ms, p.lock_ms), p.name)
            self.assertEqual(presets.preset_summary(p.pre_ms, p.hold_ms, p.lock_ms), p.summary)

    def test_custom_when_nothing_matches(self):
        self.assertEqual(presets.preset_index(61, 25, 300), -1)
        self.assertEqual(presets.preset_name(60, 25, 301), presets.CUSTOM)
        self.assertEqual(presets.preset_name(None, 25, 300), presets.CUSTOM)
        self.assertEqual(presets.preset_summary(1, 2, 3), presets.CUSTOM_SUMMARY)

    def test_float_values_from_json_still_match(self):
        self.assertEqual(presets.preset_name(60.0, 25.0, 300.0), "Balanced")

    def test_preset_config_roundtrip(self):
        cfg = presets.preset_config(2)
        self.assertEqual(cfg, {"pre_ms": 90, "hold_ms": 35, "lock_ms": 400})
        self.assertEqual(presets.preset_name(**cfg), "Strong")

    def test_summaries_are_one_line(self):
        for p in presets.PRESETS:
            self.assertNotIn("\n", p.summary)
            self.assertIn("ms later", p.summary)

    def test_normalize_keeps_daemon_rules(self):
        out = presets.normalize({"pre_ms": 60, "hold_ms": 200, "lock_ms": 150}, "hold_ms")
        self.assertLess(out["hold_ms"], out["lock_ms"])
        out = presets.normalize({"pre_ms": 60, "hold_ms": 200, "lock_ms": 150}, "lock_ms")
        self.assertLess(out["hold_ms"], out["lock_ms"])
        self.assertEqual(out["lock_ms"], 150)
        out = presets.normalize({"pre_ms": 400, "hold_ms": 25, "lock_ms": 300}, "lock_ms")
        self.assertLessEqual(out["pre_ms"], out["lock_ms"])
        ok = {"pre_ms": 60, "hold_ms": 25, "lock_ms": 300}
        self.assertEqual(presets.normalize(ok, "pre_ms"), ok)


class GsettingsParseTests(unittest.TestCase):
    def test_scalars(self):
        p = gsettings.parse_gvariant
        self.assertIs(p("true"), True)
        self.assertIs(p("false\n"), False)
        self.assertEqual(p("'flat'"), "flat")
        self.assertEqual(p('"adaptive"'), "adaptive")
        self.assertEqual(p("400"), 400)
        self.assertEqual(p("-3"), -3)
        self.assertAlmostEqual(p("0.30472103004291839"), 0.30472103004291839)
        self.assertEqual(p("uint32 500"), 500)
        self.assertEqual(p("int64 -5"), -5)
        self.assertAlmostEqual(p("double 0.5"), 0.5)

    def test_string_escapes_and_unknown(self):
        self.assertEqual(gsettings.parse_gvariant(r"'it\'s'"), "it's")
        self.assertEqual(gsettings.parse_gvariant("@as []"), "@as []")
        self.assertEqual(gsettings.parse_gvariant("['a', 'b']"), "['a', 'b']")

    def test_list_recursively_fixture(self):
        got = gsettings.parse_list_recursively(LIST_RECURSIVELY)
        self.assertEqual(got["accel-profile"], "flat")
        self.assertEqual(got["double-click"], 400)
        self.assertEqual(got["drag-threshold"], 8)
        self.assertIs(got["left-handed"], False)
        self.assertIs(got["natural-scroll"], True)
        self.assertAlmostEqual(got["speed"], 0.3047210)
        self.assertEqual(len(got), 7)

    def test_list_recursively_ignores_other_schemas_and_noise(self):
        text = "org.gnome.desktop.interface color-scheme 'prefer-dark'\n\n" + LIST_RECURSIVELY
        self.assertEqual(len(gsettings.parse_list_recursively(text)), 7)

    def test_format(self):
        f = gsettings.format_gvariant
        self.assertEqual(f(True), "true")
        self.assertEqual(f(False), "false")
        self.assertEqual(f(400), "400")
        self.assertEqual(f("flat"), "'flat'")
        self.assertEqual(f(0.25), "0.25")
        self.assertEqual(f("it's"), r"'it\'s'")

    def test_format_parse_roundtrip(self):
        for v in (True, False, 12, -7, 0.5, "default", "it's"):
            self.assertEqual(gsettings.parse_gvariant(gsettings.format_gvariant(v)), v)

    def test_coerce_clamps_and_defaults(self):
        c = gsettings.coerce
        self.assertEqual(c("speed", 5), 1.0)
        self.assertEqual(c("speed", -5), -1.0)
        self.assertEqual(c("double-click", 5000), 1000)
        self.assertEqual(c("double-click", 3.7), 100)
        self.assertEqual(c("drag-threshold", 0), 1)
        self.assertEqual(c("accel-profile", "bogus"), "default")
        self.assertEqual(c("accel-profile", "adaptive"), "adaptive")
        self.assertIs(c("left-handed", 1), True)
        self.assertEqual(c("double-click", "abc"), 400)

    def test_prefers_dark(self):
        self.assertTrue(gsettings.prefers_dark("'prefer-dark'\n"))
        self.assertTrue(gsettings.prefers_dark("color-scheme: 'prefer-dark'"))
        self.assertFalse(gsettings.prefers_dark("'default'"))
        self.assertFalse(gsettings.prefers_dark("'prefer-light'"))

    def test_write_readonly_does_not_spawn(self):
        old = os.environ.get("WHEELGUARD_GSETTINGS_READONLY")
        os.environ["WHEELGUARD_GSETTINGS_READONLY"] = "1"
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertTrue(gsettings.write("speed", 0.2))
            self.assertIn("speed 0.2", out.getvalue())
            with self.assertRaises(KeyError):
                gsettings.write("nope", 1)
        finally:
            if old is None:
                del os.environ["WHEELGUARD_GSETTINGS_READONLY"]
            else:
                os.environ["WHEELGUARD_GSETTINGS_READONLY"] = old


class ProtocolTests(unittest.TestCase):
    def test_line_buffer_splits_partial_chunks(self):
        lb = protocol.LineBuffer()
        self.assertEqual(lb.feed(b'{"type":"ok"}\n{"type":'), [b'{"type":"ok"}'])
        self.assertEqual(lb.feed(b'"status"}\n\n'), [b'{"type":"status"}'])
        self.assertEqual(lb.feed(b"a\nb\nc"), [b"a", b"b"])
        self.assertEqual(lb.feed(b"\n"), [b"c"])

    def test_parse(self):
        self.assertEqual(protocol.parse(b'{"type":"ok"}'), {"type": "ok"})
        self.assertIsNone(protocol.parse(b"not json"))
        self.assertIsNone(protocol.parse(b"[1,2]"))
        self.assertIsNone(protocol.parse(b'{"no":"type"}'))
        self.assertIsNone(protocol.parse(b"\xff\xfe"))

    def test_encode(self):
        raw = protocol.encode({"cmd": "set", "config": {"hold_ms": 30}})
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw), {"cmd": "set", "config": {"hold_ms": 30}})

    def test_tick(self):
        t = protocol.as_tick({"type": "tick", "t": 1.5, "sign": -1, "kind": "drop", "held": True, "gap_ms": 12.5})
        self.assertEqual(t, protocol.Tick(1.5, -1, "drop", True, 12.5))
        t = protocol.as_tick({"type": "tick", "t": 1.0, "sign": 1, "kind": "emit", "held": False, "gap_ms": None})
        self.assertIsNone(t.gap_ms)
        self.assertIsNone(protocol.as_tick({"type": "tick", "sign": 0, "kind": "emit"}))
        self.assertIsNone(protocol.as_tick({"type": "tick", "sign": 1, "kind": "weird"}))
        self.assertIsNone(protocol.as_tick({"type": "status"}))

    def test_device_state_three_failure_modes(self):
        run = protocol.device_state({"connected": True, "device": "Mouse", "error": None})
        self.assertEqual((run.state, run.device), ("running", "Mouse"))
        wait = protocol.device_state({"connected": False, "device": None, "error": None})
        self.assertEqual(wait.state, "waiting")
        perm = protocol.device_state({"connected": False, "error": "permission denied: /dev/input/event2"})
        self.assertEqual(perm.state, "permission")
        self.assertIn("event2", perm.error)
        self.assertEqual(protocol.device_state({"connected": False, "error": "Permission Denied"}).state, "permission")
        other = protocol.device_state({"connected": False, "error": "device busy"})
        self.assertEqual(other.state, "waiting")

    def test_status_config_and_stats(self):
        st = {"config": {"enabled": False, "lock_ms": 400.0, "pre_ms": 90, "hold_ms": 35, "vendor": "3367"},
              "stats": {"ticks": 100, "passed": 91, "dropped": 9, "since": 1.0}}
        self.assertEqual(protocol.status_config(st),
                         {"enabled": False, "pre_ms": 90, "hold_ms": 35, "lock_ms": 400})
        self.assertEqual(protocol.status_stats(st), {"ticks": 100, "dropped": 9})
        self.assertIsNone(protocol.status_config({}))
        self.assertEqual(protocol.status_stats({}), {"ticks": 0, "dropped": 0})
        self.assertEqual(protocol.status_config({"config": {}})["lock_ms"], 300)

    def test_block_rate(self):
        self.assertEqual(protocol.block_rate(0, 0), 0.0)
        self.assertAlmostEqual(protocol.block_rate(200, 18), 9.0)

    def test_command_queue_correlates_replies_in_order(self):
        q = protocol.CommandQueue()
        q.push("set")
        q.push("reset_stats")
        self.assertIsNone(q.resolve({"type": "status"}))      # events never pop
        self.assertEqual(len(q), 2)
        self.assertEqual(q.resolve({"type": "error", "message": "hold_ms must be < lock_ms"}),
                         ("set", "hold_ms must be < lock_ms"))
        self.assertEqual(q.resolve({"type": "ok"}), ("reset_stats", None))
        self.assertEqual(q.resolve({"type": "ok"}), ("?", None))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DaemonClientTests(unittest.TestCase):
    """DaemonClient against a tiny fake server on a private unix socket."""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "wg.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.srv.settimeout(5)
        self.received = []
        self.conn = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        self.client = backend.DaemonClient(self.path)
        self.client._debounce.setInterval(20)

    def tearDown(self):
        self.client._retry.stop()
        self.client._sock.abort()
        self.srv.close()
        if self.conn is not None:
            self.conn.close()
        self.thread.join(timeout=1)
        self.dir.cleanup()

    def _serve(self):
        try:
            self.conn, _ = self.srv.accept()
        except OSError:
            return
        buf = b""
        while True:
            try:
                data = self.conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.received.append(json.loads(line))
                self._reply(self.received[-1])

    reply_for = staticmethod(lambda msg: [])

    def _reply(self, msg):
        for out in self.reply_for(msg):
            self.conn.sendall(json.dumps(out).encode() + b"\n")

    def pump(self, cond, timeout=3.0):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        deadline = QTimer()
        deadline.setSingleShot(True)
        poll.timeout.connect(lambda: loop.quit() if cond() else None)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(int(timeout * 1000))
        loop.exec()
        poll.stop()
        deadline.stop()
        return cond()

    STATUS = {"type": "status", "version": "1", "connected": True, "device": "Dev", "error": None,
              "config": {"enabled": True, "lock_ms": 300, "pre_ms": 60, "hold_ms": 25},
              "stats": {"ticks": 10, "passed": 9, "dropped": 1, "since": 0}}

    def test_subscribe_status_ticks_and_set_ok(self):
        status = self.STATUS

        def reply(msg):
            if msg["cmd"] == "subscribe":
                return [status, {"type": "tick", "t": 1.0, "sign": 1, "kind": "emit", "held": False, "gap_ms": None},
                        {"type": "tick", "t": 1.1, "sign": -1, "kind": "drop", "held": False, "gap_ms": 5}]
            if msg["cmd"] == "set":
                cfg = dict(status["config"], **msg["config"])
                return [{"type": "ok"}, dict(status, config=cfg)]
            return []
        self.reply_for = reply
        ticks = []
        self.client.tick.connect(lambda *a: ticks.append(a))
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "running" and len(ticks) == 2))
        self.assertEqual(self.client.device, "Dev")
        self.assertEqual(self.client.presetName, "Balanced")
        self.assertEqual((self.client.ticks, self.client.dropped), (12, 2))
        self.assertEqual(ticks[1][:3], (-1, "drop", False))
        self.assertEqual(self.received[0], {"cmd": "subscribe"})

        self.client.applyPreset(3)                    # Maximum
        self.assertEqual(self.client.presetName, "Maximum")   # optimistic
        self.assertTrue(self.pump(lambda: any(m["cmd"] == "set" for m in self.received)))
        sent = [m for m in self.received if m["cmd"] == "set"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["config"], {"pre_ms": 130, "hold_ms": 50, "lock_ms": 500})
        self.pump(lambda: False, 0.15)
        self.assertEqual(self.client.presetName, "Maximum")
        self.assertEqual(self.client.editError, "")

    def test_set_error_reverts_and_reports(self):
        status = self.STATUS

        def reply(msg):
            if msg["cmd"] == "subscribe":
                return [status]
            if msg["cmd"] == "set":
                return [{"type": "error", "message": "hold_ms must be > 0 and < lock_ms"}]
            return []
        self.reply_for = reply
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "running"))
        self.client.setMs("pre_ms", 111)
        self.assertEqual(self.client.preMs, 111)
        self.assertTrue(self.pump(lambda: self.client.editError != ""))
        self.assertIn("hold_ms", self.client.editError)
        self.assertEqual(self.client.preMs, 60)           # reverted
        self.assertEqual(self.client.presetName, "Balanced")

    def test_debounce_coalesces_drag(self):
        status = self.STATUS
        self.reply_for = lambda m: [status] if m["cmd"] == "subscribe" else [{"type": "ok"}]
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "running"))
        for v in (70, 80, 90, 100):
            self.client.setMs("pre_ms", v)
        self.assertTrue(self.pump(lambda: any(m["cmd"] == "set" for m in self.received)))
        self.pump(lambda: False, 0.15)
        sent = [m for m in self.received if m["cmd"] == "set"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["config"], {"pre_ms": 100})

    def test_status_does_not_clobber_pending_edit(self):
        status = self.STATUS
        self.reply_for = lambda m: [status] if m["cmd"] == "subscribe" else []
        self.client._debounce.setInterval(500)
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "running"))
        self.client.setMs("hold_ms", 40)
        self.conn.sendall(json.dumps(status).encode() + b"\n")   # stale status mid-edit
        self.pump(lambda: False, 0.1)
        self.assertEqual(self.client.holdMs, 40)

    def test_permission_and_waiting_states(self):
        st = dict(self.STATUS, connected=False, device=None, error="permission denied: /dev/input/event2")
        self.reply_for = lambda m: [st] if m["cmd"] == "subscribe" else []
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "permission"))
        self.assertIn("event2", self.client.detail)
        self.conn.sendall(json.dumps(dict(st, error=None)).encode() + b"\n")
        self.assertTrue(self.pump(lambda: self.client.state == "waiting"))

    def test_no_daemon_goes_to_stopped_and_keeps_retrying(self):
        self.srv.close()
        os.unlink(self.path)
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "stopped"))
        self.assertTrue(self.client._retry.isActive())


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class LiquidGlassTests(unittest.TestCase):
    """Token files from the Liquid Glass theme, as it writes them."""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lg = os.path.join(self.dir.name, "liquid-glass")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, scheme, **tokens):
        os.makedirs(self.lg, exist_ok=True)
        with open(os.path.join(self.lg, f"tokens-{scheme}.json"), "w") as f:
            json.dump({"version": 1, **tokens}, f)

    def test_inactive_without_files_keeps_fallbacks(self):
        g = backend.LiquidGlass(self.lg)
        self.assertFalse(g.active)
        self.assertEqual(g.color(True, "chrome", "#0A0B0E"), "#0A0B0E")
        self.assertEqual(g.number(True, "radius", 16), 16)

    def test_maps_roles_and_converts_alpha_to_qt_order(self):
        self.write("dark", chrome="#2c2f3670", fg="#f4f5f9", radius=22)
        self.write("light", chrome="#f5f6fab0")
        g = backend.LiquidGlass(self.lg)
        self.assertTrue(g.active)
        self.assertEqual(g.color(True, "chrome", ""), "#702c2f36")
        self.assertEqual(g.color(True, "text", ""), "#f4f5f9")
        self.assertEqual(g.color(False, "chrome", ""), "#b0f5f6fa")
        self.assertEqual(g.color(False, "text", "#14161D"), "#14161D")   # missing token: fallback
        self.assertEqual(g.color(True, "nonsense", "#123456"), "#123456")
        self.assertEqual(g.number(True, "radius", 16), 22)

    def test_ignores_unknown_versions_and_garbage(self):
        self.write("dark", chrome="#2c2f3670")
        with open(os.path.join(self.lg, "tokens-dark.json"), "w") as f:
            f.write('{"version": 2, "chrome": "#000000ff"}')
        with open(os.path.join(self.lg, "tokens-light.json"), "w") as f:
            f.write("not json")
        self.assertFalse(backend.LiquidGlass(self.lg).active)

    def test_reload_emits_changed_when_tokens_change(self):
        g = backend.LiquidGlass(self.lg)
        seen = []
        g.changed.connect(lambda: seen.append(g.active))
        self.write("dark", chrome="#11111111")
        g.reload()
        g.reload()                      # same content: no second signal
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
