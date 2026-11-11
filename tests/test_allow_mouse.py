""""Allow this mouse": the root helper's rules file and checks, and the app side's reading of it."""
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("wheelguard_allow_mouse", str(ROOT / "build-aux" / "wheelguard-allow-mouse"))
helper = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(helper)

KEY_A = 1 << 30


def fake_node(sysfs, n, vendor, product, rel=0x103, key=0):
    d = Path(sysfs) / f"event{n}" / "device"
    (d / "id").mkdir(parents=True)
    (d / "capabilities").mkdir()
    (d / "id" / "vendor").write_text(f"{vendor:04x}\n")
    (d / "id" / "product").write_text(f"{product:04x}\n")
    (d / "capabilities" / "rel").write_text(f"{rel:x}\n")
    (d / "capabilities" / "key").write_text(f"1f0000 0 0 0 {key:x}\n")


class Ids(unittest.TestCase):
    def test_only_lower_case_hex_pairs(self):
        self.assertTrue(helper.valid_id("046d:c08b"))
        for bad in ("046D:C08B", "46d:c08b", "046d:c08b ", "046d:c08b\n", "046d-c08b", "", None, "046d:c08b:1",
                    '046d:c0"b', "../../etc", "046d:c08b; rm -rf /"):
            self.assertFalse(helper.valid_id(bad), bad)

    def test_usage_errors_touch_nothing(self):
        for argv in ([], ["add"], ["add", "046D:C08B"], ["delete", "046d:c08b"], ["add", "046d:c08b", "x"]):
            self.assertEqual(helper.main(argv), helper.USAGE, argv)

    @unittest.skipIf(os.geteuid() == 0, "running as root")
    def test_refuses_without_root(self):
        self.assertEqual(helper.main(["remove", "046d:c08b"]), helper.REFUSED)


class RulesFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "rules.d", "71-test.rules")

    def tearDown(self):
        self.dir.cleanup()

    def test_round_trip(self):
        ids = ["046d:c08b", "1532:0084"]
        helper.write_ids(ids, self.path)
        self.assertEqual(helper.read_ids(self.path), ids)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)
        text = Path(self.path).read_text()
        self.assertEqual(sum(1 for ln in text.splitlines() if not ln.startswith("#")), 2)
        for line in text.splitlines():
            if not line.startswith("#"):
                self.assertIn('ENV{ID_INPUT_MOUSE}=="1"', line)
                self.assertIn('ENV{ID_INPUT_KEYBOARD}!="1"', line)      # never a keyboard node
                self.assertTrue(line.endswith('TAG+="uaccess"'))

    def test_foreign_lines_are_dropped_not_copied(self):
        os.makedirs(os.path.dirname(self.path))
        Path(self.path).write_text(helper.render(["046d:c08b"])
                                   + 'KERNEL=="*", MODE="0666"\n'
                                   + helper.rule("1532:0084") + ', RUN+="/bin/sh"\n')
        self.assertEqual(helper.read_ids(self.path), ["046d:c08b"])
        helper.write_ids(helper.change("add", "1234:5678", helper.read_ids(self.path)), self.path)
        text = Path(self.path).read_text()
        self.assertNotIn("0666", text)
        self.assertNotIn("RUN", text)
        self.assertEqual(helper.read_ids(self.path), ["046d:c08b", "1234:5678"])

    def test_remove_last_deletes_the_file(self):
        helper.write_ids(["046d:c08b"], self.path)
        helper.write_ids(helper.change("remove", "046d:c08b", helper.read_ids(self.path)), self.path)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(helper.read_ids(self.path), [])

    def test_add_twice_keeps_one_and_the_list_is_capped(self):
        ids = helper.change("add", "046d:c08b", ["046d:c08b"])
        self.assertEqual(ids, ["046d:c08b"])
        full = [f"{n:04x}:0001" for n in range(helper.MAX_MICE)]
        with self.assertRaises(ValueError):
            helper.change("add", "ffff:0001", full)
        self.assertEqual(len(helper.change("add", full[0], full)), helper.MAX_MICE)     # re-adding one is fine

    def test_symlink_is_refused(self):
        os.makedirs(os.path.dirname(self.path))
        target = os.path.join(self.dir.name, "elsewhere")
        Path(target).write_text(helper.render(["046d:c08b"]))
        os.symlink(target, self.path)
        with self.assertRaises(OSError):
            helper.read_ids(self.path)


class Connected(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.sysfs = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def test_a_plain_wheel_mouse_is_accepted(self):
        fake_node(self.sysfs, 3, 0x046D, 0xC08B)
        self.assertEqual(helper.connected("046d:c08b", self.sysfs), "")

    def test_not_connected(self):
        fake_node(self.sysfs, 3, 0x046D, 0xC08B)
        self.assertIn("isn't connected", helper.connected("1532:0084", self.sysfs))

    def test_no_wheel_is_not_a_mouse_we_can_protect(self):
        fake_node(self.sysfs, 3, 0x046D, 0xC08B, rel=0x3)
        self.assertIn("isn't connected", helper.connected("046d:c08b", self.sysfs))

    def test_a_node_that_is_also_a_keyboard_is_refused(self):
        fake_node(self.sysfs, 3, 0x046D, 0xC08B, key=KEY_A)
        self.assertIn("keyboard", helper.connected("046d:c08b", self.sysfs))

    def test_keyboard_sibling_does_not_block_the_mouse_node(self):
        fake_node(self.sysfs, 3, 0x046D, 0xC08B, rel=0, key=KEY_A)       # the mouse's macro-key node
        fake_node(self.sysfs, 4, 0x046D, 0xC08B)
        self.assertEqual(helper.connected("046d:c08b", self.sysfs), "")


try:
    from wheelguard.gui import mouseaccess
except ImportError:             # PySide6 isn't installed
    mouseaccess = None


@unittest.skipIf(mouseaccess is None, "PySide6 not available")
class AppSide(unittest.TestCase):
    def test_reads_what_the_helper_wrote(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.rules")
            self.assertEqual(mouseaccess.allowed_ids(path), [])
            helper.write_ids(["046d:c08b", "1532:0084"], path)
            self.assertEqual(mouseaccess.allowed_ids(path), ["046d:c08b", "1532:0084"])

    def test_same_file_and_helper_path(self):
        self.assertEqual(mouseaccess.RULES_PATH, helper.RULES_PATH)

    def test_result_text(self):
        self.assertEqual(mouseaccess.result_text(0, ""), "")
        self.assertEqual(mouseaccess.result_text(mouseaccess.PKEXEC_DISMISSED, "dismissed"), "")
        self.assertEqual(mouseaccess.result_text(mouseaccess.PKEXEC_DENIED, ""), mouseaccess.DENIED)
        self.assertEqual(mouseaccess.result_text(3, "\nThat mouse isn't connected.\n"), "That mouse isn't connected.")
        self.assertEqual(mouseaccess.result_text(4, ""), mouseaccess.FAILED)

    def test_unavailable_without_the_helper(self):
        acc = mouseaccess.MouseAccess(helper="/nonexistent/helper", rules_path="/nonexistent/rules")
        self.assertFalse(acc.available)
        acc.allow("046d:c08b")
        self.assertEqual(acc.error, mouseaccess.NOT_INSTALLED)
        self.assertFalse(acc.busy)


if __name__ == "__main__":
    unittest.main()


class AutomaticTakesAllowedMice(unittest.TestCase):
    """Allowing a mouse is the user's say-so: Automatic protects it, after the supported mice."""

    def test_shared_reader_matches_the_helper(self):
        from wheelguard import mice
        self.assertEqual(mice.ALLOWED_RULES, helper.RULES_PATH)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.rules")
            self.assertEqual(mice.allowed_mice(path), [])
            helper.write_ids(["046d:c08b", "1532:0084"], path)
            self.assertEqual(mice.allowed_mice(path), [(0x046D, 0xC08B), (0x1532, 0x0084)])

    def test_reader_ignores_comments_and_junk(self):
        from wheelguard import mice
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.rules")
            Path(path).write_text('# ATTRS{id/vendor}=="dead", ATTRS{id/product}=="beef"\nnonsense\n'
                                  'ATTRS{id/vendor}=="DEAD", ATTRS{id/product}=="BEEF"\n')
            self.assertEqual(mice.allowed_ids(path), [])

    def test_search_order(self):
        try:
            from wheelguard import daemon
        except ImportError:
            self.skipTest("evdev not available")
        ids = daemon._wanted(0x3367, 0x1970, allowed=[(0x093A, 0x2510), (0x3367, 0x1982)])
        self.assertEqual(ids[0], (0x3367, 0x1970))
        self.assertEqual(ids[-1], (0x093A, 0x2510))                 # after every supported mouse
        self.assertEqual(ids.count((0x3367, 0x1982)), 1)            # a supported mouse isn't listed twice
        self.assertLess(ids.index((0x1532, 0x00BF)), ids.index((0x093A, 0x2510)))
