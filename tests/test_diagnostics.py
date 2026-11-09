"""The diagnostics report: what it says, and that it never carries a user name, home path or host name."""
import unittest

try:
    from wheelguard.gui import diagnostics as dg
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

HOME, USER, HOST = "/home/alice", "alice", "alices-laptop"


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def facts(**kw):
    d = {"app_version": "1.6.0", "service_version": "1.0.0", "service_state": "running (protecting)",
         "os": "Fedora Linux 44 (Workstation Edition)", "session": "Wayland", "shell_version": "50.1",
         "extension": "active (version 3)", "model": "Endgame Gear XM2w 4k v2", "usb_id": "3367:1982",
         "connection": "wireless", "mouse_fw": "1.05", "dongle_fw": "1.23", "battery": "82%",
         "mouse_state": "ready", "last_error": "", "protection": True, "preset": "Balanced",
         "clicklock": False, "battery_alerts": True, "_home": HOME, "_user": USER, "_host": HOST}
    d.update(kw)
    return d


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ReportTests(unittest.TestCase):
    def test_contains_the_facts_the_issue_asks_for(self):
        text = dg.build_report(facts())
        for want in ("App version: 1.6.0", "Service: version 1.0.0, running (protecting)", "Fedora Linux 44",
                     "GNOME Shell: 50.1", "Panel extension: active (version 3)", "Mouse: Endgame Gear XM2w 4k v2",
                     "USB ID: 3367:1982 (vendor:product)", "Connection: wireless", "Mouse firmware: 1.05",
                     "Dongle firmware: 1.23", "Mouse page: ready", "Last error: none", "Scroll protection: on, preset Balanced"):
            self.assertIn(want, text)

    def test_no_mouse(self):
        text = dg.build_report(facts(model="", usb_id="", mouse_state="missing"))
        self.assertIn("Mouse: none detected", text)
        self.assertNotIn("USB ID", text)

    def test_never_carries_the_home_path_user_or_host(self):
        err = ("Couldn't save the backup first ([Errno 13] Permission denied: '/home/alice/.config/wheelguard/"
               "backups/x.bin'); alice@alices-laptop; /run/user/1000/wheelguard.sock")
        text = dg.build_report(facts(last_error=err, extension="ALICE's extension"))
        for secret in ("alice", "Alice", "/home", "1000", "laptop"):
            self.assertNotIn(secret, text, secret)
        self.assertIn("~/.config/wheelguard/backups/x.bin", text)

    def test_scrub_rules(self):
        self.assertEqual(dg.scrub("/home/bob/x and /home/alice/y", home="/home/alice"), "~/x and ~/y")
        self.assertEqual(dg.scrub("user alice.txt malice", user="alice"), "user alice.txt malice")   # whole names only
        self.assertEqual(dg.scrub("hi alice!", user="alice"), "hi <user>!")
        self.assertEqual(dg.scrub("x", home="/"), "x")
        self.assertEqual(dg.scrub("on box1 now", host="box1"), "on <host> now")

    def test_os_and_shell_parsers(self):
        self.assertEqual(dg.os_name('NAME=Fedora\nPRETTY_NAME="Fedora Linux 44 (Workstation Edition)"\n'),
                         "Fedora Linux 44 (Workstation Edition)")
        self.assertEqual(dg.os_name("NAME=x\n"), "unknown")
        self.assertEqual(dg.shell_version("GNOME Shell 50.1\n"), "50.1")
        self.assertEqual(dg.shell_version("nonsense"), "unknown")

    def test_extension_text(self):
        self.assertEqual(dg.extension_text({}), "not reported by GNOME Shell")
        self.assertEqual(dg.extension_text({"state": 1, "enabled": True, "version-name": "1.5.0"}),
                         "active (version 1.5.0)")
        self.assertEqual(dg.extension_text({"state": 1, "enabled": False}), "installed but switched off")
        self.assertEqual(dg.extension_text({"state": 3}), "failed to load")

    def test_collect_from_backends(self):
        d = Obj(version="1.0.0", state="running", enabled=True, presetName="Strong", clickLock=True)
        m = Obj(state="error", messageKind="error", message="Couldn't talk to /home/alice/dev", detail="", problem="",
                modelName="Razer DeathAdder V4 Pro", usbIdText="1532:00C1", connection="wired", mouseFw="2.0",
                dongleFw="", battery=40, charging=True)
        s = Obj(batteryAlerts=False)
        text = dg.build_report(dg.collect(d, m, {"state": 1}, s, "1.6.0") | {"_home": HOME, "_user": USER, "_host": HOST})
        self.assertIn("Battery: 40% (charging)", text)
        self.assertIn("Last error: Couldn't talk to ~/dev", text)
        self.assertIn("Click-Lock: on", text)
        self.assertIn("Low-battery alerts: off", text)
        self.assertNotIn("alice", text)

    def test_the_real_collector_adds_no_serials(self):
        # whatever the machine is, the report must not contain its own home path
        d = Obj(version="", state="stopped", enabled=True, presetName="Balanced", clickLock=False)
        m = Obj(state="idle", messageKind="", message="", detail="", problem="", modelName="", usbIdText="",
                connection="", mouseFw="", dongleFw="", battery=-1, charging=False)
        text = dg.build_report(dg.collect(d, m, {}, Obj(batteryAlerts=True), "1.6.0"))
        import os
        self.assertNotIn(os.path.expanduser("~") + "/", text)


if __name__ == "__main__":
    unittest.main()
