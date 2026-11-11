"""Keyboard shortcuts: the gsettings list handling, the one-shot commands and the socket fallback."""
import json
import os
import socket
import tempfile
import threading
import unittest

try:
    from wheelguard.gui import commands, shortcuts
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

BASE = shortcuts.BASE if HAVE_QT else ""


class FakeGsettings:
    """An in-memory `gsettings`: one list key plus relocatable custom-keybinding items."""

    def __init__(self, paths=None, bindings=None, fail_on=None):
        self.paths = list(paths or [])
        self.items = {p: {"name": "", "command": "", "binding": ""} for p in self.paths}
        for p, b in (bindings or {}).items():
            self.items[p]["binding"] = b
        self.writes = []
        self.fail_on = fail_on

    def __call__(self, args):
        args = list(args)
        if args[0] == "get":
            if args[2] == "custom-keybindings":
                return "[" + ", ".join(f"'{p}'" for p in self.paths) + "]\n" if self.paths else "@as []\n"
            path = args[1].split(":", 1)[1]
            return f"'{self.items.get(path, {}).get(args[2], '')}'\n"
        if args[0] == "set":
            if self.fail_on and self.fail_on in args:
                return None
            self.writes.append(args)
            if args[2] == "custom-keybindings":
                self.paths = shortcuts.parse_str_list(args[3])
            else:
                path = args[1].split(":", 1)[1]
                self.items.setdefault(path, {})[args[2]] = shortcuts.parse_str_list("[" + args[3] + "]")[0]
            return ""
        return None


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ListTests(unittest.TestCase):
    def test_parse_and_format(self):
        self.assertEqual(shortcuts.parse_str_list("@as []"), [])
        self.assertEqual(shortcuts.parse_str_list("[]"), [])
        self.assertEqual(shortcuts.parse_str_list(None), [])
        self.assertEqual(shortcuts.parse_str_list("['/a/', '/b/']\n"), ["/a/", "/b/"])
        self.assertEqual(shortcuts.format_str_list(["/a/", "/b/"]), "['/a/', '/b/']")
        self.assertEqual(shortcuts.format_str_list([]), "[]")
        self.assertEqual(shortcuts.parse_str_list(shortcuts.format_str_list(["it's"])), ["it's"])

    def test_key_label(self):
        self.assertEqual(shortcuts.key_label("<Super><Alt>c"), "Super+Alt+C")
        self.assertEqual(shortcuts.key_label("<Control>F5"), "Control+F5")
        self.assertEqual(shortcuts.key_label(""), "")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AddTests(unittest.TestCase):
    def make(self, **kw):
        self.g = FakeGsettings(**kw)
        return shortcuts.Shortcuts(self.g, lambda flag: "wheelguard-settings " + flag)

    def test_adds_all_to_an_empty_list(self):
        s = self.make()
        res = s.add()
        self.assertTrue(res.ok)
        self.assertEqual(res.added, ["Super+Alt+C", "Super+Alt+D", "Super+Alt+P"])
        self.assertEqual(self.g.paths, [sc.path for sc in shortcuts.SHORTCUTS])
        clicklock = self.g.items[shortcuts.SHORTCUTS[0].path]
        self.assertEqual((clicklock["command"], clicklock["binding"]),
                         ("wheelguard-settings --toggle-clicklock", "<Super><Alt>c"))
        self.assertEqual(self.g.items[shortcuts.SHORTCUTS[1].path]["binding"], "<Super><Alt>d")
        self.assertTrue(s.installed())

    def test_keeps_the_users_own_shortcuts_and_never_rewrites_them(self):
        mine = [BASE + "custom0/", BASE + "custom1/"]
        s = self.make(paths=mine, bindings={mine[0]: "<Super>t"})
        self.g.items[mine[0]].update(name="Terminal", command="gnome-terminal")
        before = {p: dict(v) for p, v in self.g.items.items() if p in mine}
        self.assertTrue(s.add().ok)
        self.assertEqual(self.g.paths[:2], mine)                       # order and entries kept
        self.assertEqual(len(self.g.paths), 5)
        self.assertEqual({p: self.g.items[p] for p in mine}, before)   # their items untouched
        for a in self.g.writes:           # only our own items and the list itself were written
            if a[2] != "custom-keybindings":
                self.assertIn("wheelguard-", a[1])

    def test_twice_changes_nothing_the_second_time(self):
        s = self.make()
        s.add()
        n = len(self.g.writes)
        res = s.add()
        self.assertTrue(res.ok)
        self.assertEqual((res.added, len(res.existing)), ([], 3))
        self.assertEqual(len(self.g.writes), n)

    def test_does_not_overwrite_a_wheelguard_entry_the_user_changed(self):
        sc = shortcuts.SHORTCUTS[0]
        s = self.make(paths=[sc.path], bindings={sc.path: "<Super>x"})
        self.assertTrue(s.add().ok)
        self.assertEqual(self.g.items[sc.path]["binding"], "<Super>x")
        self.assertEqual(self.g.paths, [sc.path, shortcuts.SHORTCUTS[1].path, shortcuts.SHORTCUTS[2].path])

    def test_a_key_in_use_by_another_custom_shortcut_gets_the_fallback(self):
        other = BASE + "custom0/"
        s = self.make(paths=[other], bindings={other: "<Super><Alt>c"})
        s.add()
        self.assertEqual(self.g.items[shortcuts.SHORTCUTS[0].path]["binding"], "<Super><Alt>k")

    def test_assigned_reports_the_keys_actually_in_use(self):
        other = BASE + "custom0/"
        s = self.make(paths=[other], bindings={other: "<Super><Alt>c"})
        self.assertEqual(s.assigned(), ["Super+Alt+C", "Super+Alt+D", "Super+Alt+P"])       # not added yet: the suggestions
        s.add()
        self.assertEqual(s.assigned(), ["Super+Alt+K", "Super+Alt+D", "Super+Alt+P"])       # the fall-back, not always Super+Alt+C
        self.g.items[shortcuts.SHORTCUTS[1].path]["binding"] = "<Super>x"    # changed in GNOME Settings
        self.assertEqual(s.assigned(), ["Super+Alt+K", "Super+X", "Super+Alt+P"])
        self.g.items[shortcuts.SHORTCUTS[1].path]["binding"] = ""
        self.assertEqual(s.assigned()[1], "no key set")

    def test_failure_leaves_the_list_alone(self):
        s = self.make(paths=[BASE + "custom0/"], fail_on="command")
        res = s.add()
        self.assertFalse(res.ok)
        self.assertEqual(self.g.paths, [BASE + "custom0/"])

    def test_no_gsettings(self):
        s = shortcuts.Shortcuts(lambda args: None)
        res = s.add()
        self.assertFalse(res.ok)
        self.assertIn("aren't available", res.error)
        self.assertFalse(s.installed())

    def test_command_uses_the_launcher_on_path_or_the_checkout(self):
        self.assertEqual(shortcuts.command_for("--x", which=lambda n: "/usr/bin/wheelguard-settings"),
                         "/usr/bin/wheelguard-settings --x")
        self.assertIn("wheelguard-settings --x", shortcuts.command_for("--x", which=lambda n: None))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CommandTests(unittest.TestCase):
    def test_results_round_trip(self):
        self.assertEqual(commands.decode_result(commands.encode_result(True, "DPI 800")), (True, "DPI 800"))
        self.assertEqual(commands.decode_result(commands.encode_result(False, "nope")), (False, "nope"))

    def test_app_answer_wins(self):
        calls = []
        res = commands.run_action("next-dpi-stage", lambda m: calls.append(m) or "ok:DPI 1600", lambda: (False, "x"))
        self.assertEqual((res, calls), ((True, "DPI 1600"), ["NextDpiStage"]))

    def test_dpi_without_the_app_says_what_to_do(self):
        ok, text = commands.run_action("next-dpi-stage", lambda m: None, lambda: (True, "x"))
        self.assertFalse(ok)
        self.assertIn("isn't running", text)

    def test_clicklock_without_the_app_goes_to_the_daemon(self):
        self.assertEqual(commands.run_action("toggle-clicklock", lambda m: None, lambda: (True, "Click-Lock on")),
                         (True, "Click-Lock on"))


class FakeDaemonSocket:
    """A one-connection stand-in for the daemon: answers status and set per PROTOCOL.md."""

    def __init__(self, clicklock=False, reject=None):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "wg.sock")
        self.cfg = {"clicklock": clicklock}
        self.reject = reject
        self.paused = False
        self.got = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.srv.accept()
        with conn:
            buf = b""
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = json.loads(line)
                    self.got.append(msg)
                    if msg["cmd"] == "status":
                        out = {"type": "status", "connected": True, "config": self.cfg, "paused": self.paused}
                    elif msg["cmd"] == "pause" and not self.reject:
                        self.paused = msg["on"]
                        out = {"type": "ok"}
                    elif self.reject:
                        out = {"type": "error", "message": self.reject}
                    else:
                        self.cfg.update(msg["config"])
                        out = {"type": "ok"}
                    conn.sendall(json.dumps(out).encode() + b"\n")

    def close(self):
        self.srv.close()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DirectSocketTests(unittest.TestCase):
    def test_turns_click_lock_on_and_off(self):
        d = FakeDaemonSocket(clicklock=False)
        self.addCleanup(d.close)
        self.assertEqual(commands.toggle_clicklock_direct(d.path), (True, "Click-Lock on"))
        self.assertEqual(d.got[-1], {"cmd": "set", "config": {"clicklock": True}})

    def test_turns_it_off_when_on(self):
        d = FakeDaemonSocket(clicklock=True)
        self.addCleanup(d.close)
        self.assertEqual(commands.toggle_clicklock_direct(d.path), (True, "Click-Lock off"))

    def test_daemon_error_is_reported(self):
        d = FakeDaemonSocket(reject="clicklock_ms out of range")
        self.addCleanup(d.close)
        self.assertEqual(commands.toggle_clicklock_direct(d.path), (False, "clicklock_ms out of range"))

    def test_pause_and_resume_without_the_app(self):
        d = FakeDaemonSocket(clicklock=False)
        self.addCleanup(d.close)
        self.assertEqual(commands.toggle_pause_direct(d.path), (True, commands.PAUSED))
        self.assertEqual(d.got[-1], {"cmd": "pause", "on": True})

    def test_resume_when_paused(self):
        d = FakeDaemonSocket(clicklock=False)
        d.paused = True
        self.addCleanup(d.close)
        self.assertEqual(commands.toggle_pause_direct(d.path), (True, commands.RESUMED))
        self.assertEqual(d.got[-1], {"cmd": "pause", "on": False})

    def test_click_lock_and_pause_never_ask_the_app(self):
        asked = []
        for action in ("toggle-clicklock", "toggle-pause"):
            self.assertEqual(commands.run_action(action, asked.append, lambda: (True, "done")), (True, "done"))
        self.assertEqual(asked, [])

    def test_no_daemon(self):
        ok, text = commands.toggle_clicklock_direct("/nonexistent/wg.sock")
        self.assertFalse(ok)
        self.assertIn("service isn't running", text)


if __name__ == "__main__":
    unittest.main()
