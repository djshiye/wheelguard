"""Saved mouse setups: the validated store, and applying one through the controller's normal apply path."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard.gui import setups, traymodel
from wheelguard.hw import codec, mock, razer, session

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui.mouse import MouseController
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

ENDGAME = "endgame-xm2w-4k-v2"
ALLOWED = {ENDGAME: tuple(codec.KEYS), "razer-deathadder-v4-pro": tuple(razer.KEYS)}


def store(tmp):
    return setups.SetupStore(Path(tmp) / "wheelguard" / "setups.json", ALLOWED)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = store(self.tmp.name)
        self.vals = {"dpi_x0": 800, "dpi_y0": 800, "polling": 0x08, "ripple": True}

    def test_save_list_replace_delete(self):
        self.assertEqual(self.s.save(ENDGAME, "Gaming", self.vals), "")
        self.assertEqual(self.s.save(ENDGAME, "Desktop", {"dpi_x0": 400}), "")
        self.assertEqual([x.name for x in self.s.list(ENDGAME)], ["Gaming", "Desktop"])
        self.assertEqual(self.s.save(ENDGAME, "gaming", {"dpi_x0": 1600}), "")           # same name, any case
        self.assertEqual([(x.name, x.values) for x in self.s.list(ENDGAME)],
                         [("gaming", {"dpi_x0": 1600}), ("Desktop", {"dpi_x0": 400})])  # keeps its place
        self.assertEqual(self.s.delete(ENDGAME, "GAMING"), "")
        self.assertEqual([x.name for x in self.s.list(ENDGAME)], ["Desktop"])
        self.assertEqual(self.s.list("razer-deathadder-v4-pro"), [])

    def test_stored_per_model_and_only_that_models_keys(self):
        self.s.save(ENDGAME, "Mixed", {"dpi_x0": 800, "idle_min": 5, "bogus": 1, "button0": 1})
        self.assertEqual(self.s.list(ENDGAME)[0].values, {"dpi_x0": 800, "button0": 1})     # idle_min is Razer's
        self.s.save("razer-deathadder-v4-pro", "Razer", {"idle_min": 5, "ripple": True})
        self.assertEqual(self.s.list("razer-deathadder-v4-pro")[0].values, {"idle_min": 5})

    def test_names_are_cleaned_and_limited(self):
        self.assertEqual(self.s.save(ENDGAME, "   ", self.vals), "Give the setup a name.")
        self.assertEqual(self.s.save(ENDGAME, "x", {}), "There are no settings to save yet.")
        self.assertEqual(self.s.save("nope", "x", self.vals), "This mouse can't save setups.")
        self.s.save(ENDGAME, "  My \t gaming\n setup  " + "z" * 60, self.vals)
        name = self.s.list(ENDGAME)[0].name
        self.assertTrue(name.startswith("My gaming setup z"))
        self.assertLessEqual(len(name), setups.NAME_MAX)

    def test_a_corrupt_file_is_moved_aside_not_silently_replaced(self):
        path = Path(self.tmp.name) / "wheelguard" / "setups.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.assertEqual(self.s.list(ENDGAME), [])
        self.assertTrue(path.exists())                                  # reading changes nothing
        self.assertEqual(self.s.save(ENDGAME, "Gaming", self.vals), "")
        self.assertEqual(path.with_name("setups.json.corrupt").read_text(), "{not json")
        self.assertEqual([x.name for x in self.s.list(ENDGAME)], ["Gaming"])

    def test_unknown_models_are_preserved(self):
        path = Path(self.tmp.name) / "wheelguard" / "setups.json"
        path.parent.mkdir(parents=True)
        other = [{"name": "Future", "values": {"dpi_x0": 1, "new_key": 7}}]
        path.write_text(json.dumps({"version": 1, "setups": {"some-new-mouse": other}}))
        self.s.save(ENDGAME, "Gaming", self.vals)
        data = json.loads(path.read_text())
        self.assertEqual(data["setups"]["some-new-mouse"], other)
        self.assertEqual(self.s.delete(ENDGAME, "Gaming"), "")
        self.assertEqual(json.loads(path.read_text())["setups"]["some-new-mouse"], other)

    def test_limit_per_model(self):
        for i in range(setups.MAX_PER_MODEL):
            self.assertEqual(self.s.save(ENDGAME, f"s{i}", self.vals), "")
        self.assertIn("Delete one first", self.s.save(ENDGAME, "one too many", self.vals))
        self.assertEqual(self.s.save(ENDGAME, "s3", {"dpi_x0": 50}), "")                 # replacing is still fine

    def test_file_is_valid_json_private_and_atomic(self):
        self.s.save(ENDGAME, "Gaming", self.vals)
        data = json.loads(self.s.path.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["setups"][ENDGAME][0]["name"], "Gaming")
        self.assertEqual(os.listdir(self.s.path.parent), ["setups.json"])                  # no temp files left behind

    def test_garbage_is_dropped_on_load(self):
        self.s.path.parent.mkdir(parents=True)
        bad = {"version": 1, "setups": {
            ENDGAME: [{"name": "ok", "values": {"dpi_x0": 800, "dpi_y0": 1.5, "polling": "fast", "evil": 1,
                                                "ripple": True}},
                      {"name": "", "values": {"dpi_x0": 800}}, {"name": "ok", "values": {"dpi_x0": 400}},
                      {"name": "huge", "values": {"dpi_x0": 10 ** 12}}, "string", {"values": {"dpi_x0": 1}},
                      {"name": "x" * 100, "values": {"dpi_x0": 100}}],
            "unknown-model": [{"name": "a", "values": {"dpi_x0": 1}}], ENDGAME + "2": 5}}
        self.s.path.write_text(json.dumps(bad))
        got = self.s.list(ENDGAME)
        self.assertEqual([x.name for x in got], ["ok", "x" * setups.NAME_MAX])
        self.assertEqual(got[0].values, {"dpi_x0": 800, "ripple": True})
        for text in ("not json", "[]", '{"version": 2, "setups": {}}', '{"version": 1, "setups": []}'):
            self.s.path.write_text(text)
            self.assertEqual(self.s.list(ENDGAME), [])

    def test_unwritable_folder_reports_instead_of_raising(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        bad = setups.SetupStore(blocker / "setups.json", ALLOWED)
        self.assertIn("Couldn't save", bad.save(ENDGAME, "x", self.vals))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dev = mock.FakeHidraw("connected")
        s = session.MouseSession(lambda: (self.dev, self.dev.wired), tmp.name, lambda t: None)
        self.store = store(tmp.name)
        c = MouseController(s, setups=self.store)
        self.addCleanup(c.shutdown)
        c.ensureLoaded()
        self.wait(c, ("ready",))
        return c

    def wait(self, c, states, timeout=5000):
        loop = QEventLoop()
        c.stateChanged.connect(lambda: loop.quit() if c.state in states else None)
        QTimer.singleShot(timeout, loop.quit)
        if c.state not in states:
            loop.exec()
        self.assertIn(c.state, states)

    def test_save_current_then_list(self):
        c = self.make()
        self.assertEqual(c.saveSetup("Desktop"), "")
        rows = c.setups
        self.assertEqual([r["name"] for r in rows], ["Desktop"])
        self.assertTrue(rows[0]["active"])                      # the mouse holds exactly this right now
        self.assertEqual(c.activeSetup, "Desktop")
        self.assertIn("DPI", rows[0]["summary"])
        self.assertEqual(c.setupNames, ["Desktop"])

    def test_apply_goes_through_the_normal_flow_with_backup(self):
        c = self.make()
        original = self.dev.blob[0x05]
        c.saveSetup("Desktop")
        c.setValues({"dpi_x1": 3200, "dpi_y1": 3200})
        c.setValue("polling", 0x04)
        c.apply()
        self.wait(c, ("ready",))
        self.assertEqual(c.activeSetup, "")
        self.assertTrue(c.hasBackup)
        c.applySetup("Desktop")
        self.assertEqual(c.state, "applying")
        self.wait(c, ("ready",))
        self.assertEqual((c.messageKind, c.message.startswith("Setup “Desktop” applied")), ("ok", True))
        self.assertEqual(c.activeSetup, "Desktop")
        self.assertEqual(self.dev.blob[0x05], original)         # polling is back as saved

    def test_applying_what_the_mouse_already_has_writes_nothing(self):
        c = self.make()
        c.saveSetup("Same")
        before = len(self.dev.sent)
        c.applySetup("Same")
        self.assertEqual(c.state, "ready")
        self.assertIn("already has", c.message)
        self.assertEqual(len(self.dev.sent), before)

    def test_unsaved_edits_block_applying(self):
        c = self.make()
        c.saveSetup("Desktop")
        c.setValue("polling", 0x04)
        c.applySetup("Desktop")
        self.assertEqual(c.state, "ready")
        self.assertIn("unsaved changes", c.message)
        self.assertEqual(c.changeCount, 1)                       # the user's edit is untouched

    def test_a_tampered_setup_is_refused_before_anything_is_written(self):
        c = self.make()
        c.saveSetup("Desktop")
        data = json.loads(self.store.path.read_text())
        data["setups"][ENDGAME][0]["values"]["dpi_x0"] = 999999
        self.store.path.write_text(json.dumps(data))
        sent = len(self.dev.sent)
        c.applySetup("Desktop")
        self.assertEqual(c.state, "ready")
        self.assertIn("doesn't fit this mouse", c.message)
        self.assertEqual(len(self.dev.sent), sent)

    def test_delete_and_unknown_name(self):
        c = self.make()
        c.saveSetup("A")
        c.deleteSetup("A")
        self.assertEqual(c.setups, [])
        c.applySetup("A")
        self.assertIn("isn't saved any more", c.message)

    def test_names_reach_the_panel_state(self):
        c = self.make()
        c.saveSetup("Gaming")
        s = traymodel.snapshot(type("D", (), dict(state="running", enabled=True, presetName="", dropped=0,
                                                   clickLock=False, clickLockMs=5000, clickLockActive=False,
                                                   clickLockMode="repeat"))(), c)
        d = traymodel.state_dict(s)
        self.assertEqual((d["SetupNames"], d["ActiveSetup"]), (["Gaming"], "Gaming"))


if __name__ == "__main__":
    unittest.main()
