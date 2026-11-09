import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evdev import ecodes as e

from wheelguard import history as H
from wheelguard.control import Controller
from wheelguard.history import History
from wheelguard.ipc import Server

NOON = datetime.datetime(2026, 11, 9, 12, 0, 0).timestamp()
DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data" / "wheelguard" / "history.json"
        self.now = NOON

    def make(self, **kw):
        return History(self.path, clock=lambda: self.now, **kw)

    def write(self, obj):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


class Counting(Base):
    def test_totals_per_local_day(self):
        h = self.make()
        h.add(10, 1)
        h.add(5, 0)
        self.now += DAY
        h.add(7, 2)
        self.assertEqual(h.to_list(), [{"date": "2026-11-09", "ticks": 15, "dropped": 1},
                                       {"date": "2026-11-10", "ticks": 7, "dropped": 2}])

    def test_nothing_to_add(self):
        h = self.make()
        h.add(0, 0)
        self.assertEqual((h.to_list(), h.dirty), ([], False))


class Saving(Base):
    def test_round_trip_and_atomic_file(self):
        h = self.make()
        h.add(100, 4)
        self.assertTrue(h.save())
        self.assertFalse(h.dirty)
        data = json.loads(self.path.read_text())
        self.assertEqual(data, {"version": 1, "days": [{"date": "2026-11-09", "ticks": 100, "dropped": 4}]})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["history.json"])      # no temp file left
        self.assertEqual(self.make().to_list(), h.to_list())

    def test_saves_at_most_every_few_minutes(self):
        h = self.make(save_every=300)
        h.add(1, 0)
        h.maybe_save()
        self.assertFalse(self.path.exists())
        self.now += 299
        h.maybe_save()
        self.assertFalse(self.path.exists())
        self.now += 2
        h.maybe_save()
        self.assertTrue(self.path.exists())
        mtime = self.path.stat().st_mtime_ns
        h.add(1, 0)
        self.now += 10
        h.maybe_save()
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.now += 400
        h.maybe_save()
        self.assertEqual(json.loads(self.path.read_text())["days"][0]["ticks"], 2)

    def test_nothing_new_nothing_written(self):
        h = self.make()
        self.now += 10 ** 4
        h.maybe_save()
        self.assertTrue(h.save())
        self.assertFalse(self.path.exists())

    def test_a_failed_write_keeps_the_data_and_cleans_up(self):
        h = self.make()
        h.add(5, 1)
        with mock.patch.object(H.os, "replace", side_effect=OSError(28, "No space left")):
            self.assertFalse(h.save())
        self.assertTrue(h.dirty)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [])
        self.assertTrue(h.save())                                # works again once the disk does

    def test_unwritable_directory_is_not_fatal(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        h = History(blocker / "sub" / "history.json", clock=lambda: self.now)
        h.add(1, 0)
        self.assertFalse(h.save())

    def test_file_is_world_readable_like_the_config(self):
        h = self.make()
        h.add(1, 0)
        h.save()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)


class Loading(Base):
    def test_missing_file(self):
        self.assertEqual(self.make().to_list(), [])

    def test_corrupt_files_start_fresh_and_are_set_aside(self):
        for bad in ("{not json", "", "[]", '{"days": 5}', "null", "\x00\xff", '{"days": "x"}'):
            self.write(bad)
            h = self.make()
            self.assertEqual(h.to_list(), [], repr(bad))
            self.assertTrue(self.path.with_name("history.json.corrupt").exists() or not self.path.exists())
            h.add(3, 1)
            self.assertTrue(h.save())
            self.assertEqual(self.make().to_list(), [{"date": "2026-11-09", "ticks": 3, "dropped": 1}])
            self.path.unlink()

    def test_binary_garbage(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"\xff\xfe\x00garbage")
        self.assertEqual(self.make().to_list(), [])

    def test_bad_rows_are_skipped_good_ones_kept(self):
        self.write({"version": 1, "days": [
            {"date": "2026-11-08", "ticks": 50, "dropped": 5},
            {"date": "2026-11-31", "ticks": 1, "dropped": 0},           # not a date
            {"date": "yesterday", "ticks": 1, "dropped": 0},
            {"date": "2026-11-07", "ticks": -3, "dropped": 0},
            {"date": "2026-11-06", "ticks": "many", "dropped": 0},
            {"date": "2026-11-05", "ticks": True, "dropped": 0},
            {"date": "2026-11-04", "ticks": float("nan"), "dropped": 0},
            {"date": "2026-11-03", "ticks": 10},
            "junk", 5, None,
            {"date": "2026-11-02", "ticks": 10, "dropped": 99},         # more blocked than ticks: clamped
            {"date": "2026-11-08", "ticks": 10, "dropped": 1}]})        # a repeated date adds up
        self.assertEqual(self.make().to_list(), [{"date": "2026-11-02", "ticks": 10, "dropped": 10},
                                                 {"date": "2026-11-08", "ticks": 60, "dropped": 6}])

    def test_huge_numbers_are_capped(self):
        self.write({"days": [{"date": "2026-11-08", "ticks": 10 ** 30, "dropped": 0}]})
        self.assertEqual(self.make().to_list()[0]["ticks"], H.MAX_COUNT)


class Pruning(Base):
    def test_only_90_days_are_kept(self):
        days = [{"date": (datetime.date(2026, 11, 9) - datetime.timedelta(days=n)).isoformat(),
                 "ticks": 10, "dropped": 1} for n in range(0, 120)]
        self.write({"version": 1, "days": days})
        h = self.make()
        got = h.to_list()
        self.assertEqual(len(got), 90)
        self.assertEqual(got[0]["date"], "2026-08-12")              # today and the 89 days before it
        self.assertEqual(got[-1]["date"], "2026-11-09")

    def test_pruned_on_save_as_the_days_go_by(self):
        h = self.make()
        h.add(1, 0)
        self.now += 95 * DAY
        h.add(1, 0)
        h.save()
        self.assertEqual([d["date"] for d in self.make().to_list()], ["2027-02-12"])

    def test_future_dates_are_dropped(self):
        self.write({"days": [{"date": "2030-01-01", "ticks": 5, "dropped": 0},
                             {"date": "2026-11-10", "ticks": 5, "dropped": 0}]})      # tomorrow: a timezone slop, kept
        self.assertEqual([d["date"] for d in self.make().to_list()], ["2026-11-10"])


class ControllerSide(Base):
    def make_ctl(self):
        self.hist = self.make(save_every=300)
        self.events = []
        return Controller(config_path=Path(self.tmp.name) / "c.json", publish=self.events.append,
                          clock=lambda: self.now, history=self.hist)

    @staticmethod
    def tick(sign):
        return [(e.EV_REL, e.REL_WHEEL_HI_RES, 120 * sign), (e.EV_REL, e.REL_WHEEL, sign)]

    def test_ticks_and_blocks_are_counted_like_the_live_stats(self):
        ctl = self.make_ctl()
        out = []
        for t, s in [(1.0, 1), (1.01, 1), (1.02, -1), (1.2, -1), (1.21, 1)]:      # as in test_control.Counters
            ctl.handle_frame(self.tick(s), t, out.extend)
        self.assertEqual(ctl.stats["ticks"], 5)
        self.assertEqual(ctl.stats["dropped"], 2)
        self.assertEqual(self.hist.to_list(), [{"date": "2026-11-09", "ticks": 5, "dropped": 2}])

    def test_reset_stats_keeps_the_history(self):
        ctl = self.make_ctl()
        ctl.handle_frame(self.tick(1), 1.0, lambda f: None)
        ctl.reset_stats()
        self.assertEqual(self.hist.to_list()[0]["ticks"], 1)

    def test_saved_periodically_when_the_mouse_goes_away_and_with_the_command(self):
        ctl = self.make_ctl()
        ctl.handle_frame(self.tick(1), 1.0, lambda f: None)
        ctl.handle_flush(2.0, lambda f: None)
        self.assertFalse(self.path.exists())
        self.now += 301
        ctl.handle_flush(3.0, lambda f: None)
        self.assertTrue(self.path.exists())
        ctl.handle_frame(self.tick(1), 4.0, lambda f: None)
        ctl.set_device(False, None, "mouse disconnected")
        self.assertEqual(json.loads(self.path.read_text())["days"][0]["ticks"], 2)

    def test_history_command(self):
        ctl = self.make_ctl()
        ctl.handle_frame(self.tick(1), 1.0, lambda f: None)
        srv = Server(ctl, path=str(Path(self.tmp.name) / "s"))
        self.assertEqual(srv.dispatch(None, {"cmd": "history"}),
                         {"type": "history", "days": [{"date": "2026-11-09", "ticks": 1, "dropped": 0}]})

    def test_no_history_object_means_an_empty_answer(self):
        ctl = Controller(config_path=Path(self.tmp.name) / "c.json")
        self.assertIsNone(ctl.history)
        ctl.handle_frame(self.tick(1), 1.0, lambda f: None)
        srv = Server(ctl, path=str(Path(self.tmp.name) / "s"))
        self.assertEqual(srv.dispatch(None, {"cmd": "history"}), {"type": "history", "days": []})

    def test_history_is_never_in_the_status_or_the_tick_stream(self):
        ctl = self.make_ctl()
        ctl.handle_frame(self.tick(1), 1.0, lambda f: None)
        self.assertNotIn("history", ctl.status())

    def test_the_real_daemon_default_path_follows_xdg_data_home(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp.name}):
            import importlib
            mod = importlib.reload(H)
            try:
                self.assertEqual(mod.HISTORY_PATH, Path(self.tmp.name) / "wheelguard" / "history.json")
            finally:
                importlib.reload(H)


if __name__ == "__main__":
    unittest.main()
