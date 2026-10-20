import json
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard import config as C


class Normalize(unittest.TestCase):
    def test_defaults(self):
        cfg = C.normalize({})
        self.assertEqual(cfg, C.DEFAULTS)
        self.assertTrue(cfg["enabled"])

    def test_merge_keeps_other_keys(self):
        base = C.normalize({"lock_ms": 400, "enabled": False})
        cfg = C.normalize({"hold_ms": 30}, base)
        self.assertEqual((cfg["lock_ms"], cfg["hold_ms"], cfg["enabled"]), (400, 30, False))
        self.assertEqual(base["hold_ms"], 25, "base must not be mutated")

    def test_rejects(self):
        bad = [
            {"hold_ms": 0},                      # hold must be > 0
            {"hold_ms": 300},                    # hold < lock
            {"hold_ms": 500, "lock_ms": 400},
            {"pre_ms": -1},
            {"pre_ms": 301},                     # pre <= lock
            {"lock_ms": 2001},
            {"lock_ms": 2500, "pre_ms": 10},
            {"enabled": 1},
            {"enabled": "yes"},
            {"lock_ms": "300"},
            {"lock_ms": True},
            {"lock_ms": float("nan")},
            {"bogus": 1},
            {"vendor": "zzzz"},
            {"vendor": 0x10000},
            {"retry_s": 0},
        ]
        for partial in bad:
            with self.subTest(partial=partial), self.assertRaises(ValueError):
                C.normalize(partial)
        with self.assertRaises(ValueError):
            C.normalize([1, 2])

    def test_accepts_boundaries(self):
        cfg = C.normalize({"lock_ms": 2000, "hold_ms": 1999, "pre_ms": 2000})
        self.assertEqual(cfg["lock_ms"], 2000)
        C.normalize({"pre_ms": 0})

    def test_ids_are_hex(self):
        cfg = C.normalize({"vendor": "3367", "product": "0x1970"})
        self.assertEqual((cfg["vendor"], cfg["product"]), (0x3367, 0x1970))
        self.assertEqual(C.to_wire(cfg)["vendor"], "3367")


class LoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sub" / "config.json"

    def test_missing_file_gives_defaults(self):
        self.assertEqual(C.load(self.path), C.DEFAULTS)

    def test_save_roundtrip_is_atomic(self):
        cfg = C.normalize({"lock_ms": 350, "enabled": False})
        C.save(cfg, self.path)
        self.assertEqual(C.load(self.path), cfg)
        self.assertEqual(json.loads(self.path.read_text())["vendor"], "3367")
        self.assertEqual(os.listdir(self.path.parent), ["config.json"], "no temp file left behind")

    def test_failed_save_keeps_old_file(self):
        C.save(C.normalize({"lock_ms": 350}), self.path)
        before = self.path.read_text()
        with self.assertRaises(TypeError):
            C.save({**C.DEFAULTS, "lock_ms": object()}, self.path)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(os.listdir(self.path.parent), ["config.json"])

    def test_invalid_file_raises(self):
        self.path.parent.mkdir()
        self.path.write_text('{"hold_ms": 999}')
        with self.assertRaises(ValueError):
            C.load(self.path)
        self.path.write_text("not json")
        with self.assertRaises(ValueError):
            C.load(self.path)


if __name__ == "__main__":
    unittest.main()
