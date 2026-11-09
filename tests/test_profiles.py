"""Per-game profiles: validation, the store, the decision engine and the installed-app listing (no Qt)."""
import json
import tempfile
import unittest
from pathlib import Path

from wheelguard.gui import desktopapps, profiles
from wheelguard.gui.profiles import Profile, ProfileEngine

BALANCED = (60, 25, 300)
STRONG = (90, 35, 400)
LIGHT = (40, 15, 250)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def live(protection=BALANCED, clicklock=False, stage=2):
    return {"protection": protection, "clicklock": clicklock, "stage": stage}


GAME = {"protection": STRONG, "clicklock": True, "stage": 0}
GAME_ID, OTHER_ID, EDITOR_ID = "steam_app_1.desktop", "other_game.desktop", "org.gnome.Nautilus.desktop"


class ValidationTests(unittest.TestCase):
    def test_app_ids(self):
        self.assertEqual(profiles.clean_app_id("steam_app_123.desktop"), "steam_app_123.desktop")
        for bad in ("", " ", "a b\n", "a b ", " a", "a\tb", "../x", "a /b", "a/b", "x" * 201, None, 5, "-x", "a\nb", "\u202eevil"):
            self.assertEqual(profiles.clean_app_id(bad), "", repr(bad))

    def test_app_ids_may_have_spaces_like_steam_shortcuts(self):
        for ok in ("Portal 2.desktop", "steam_app_620", "Half-Life 2 Episode 1.desktop"):
            self.assertEqual(profiles.clean_app_id(ok), ok)
        self.assertEqual(profiles.clean_app_id("a " * 100), "")          # trailing space
        self.assertEqual(profiles.clean_app_id("a b" * 100), "")         # too long
        self.assertIsNotNone(profiles.make_profile("Portal 2.desktop", "Strong"))

    def test_make_profile_accepts_ui_words_and_rejects_empty(self):
        p = profiles.make_profile("a.desktop", "strong", "on", 1)
        self.assertEqual(p, Profile("a.desktop", "Strong", True, 1))
        self.assertIsNone(profiles.make_profile("a.desktop", "keep", "keep", None))
        self.assertIsNone(profiles.make_profile("bad/id", "Strong"))
        self.assertIsNone(profiles.make_profile("a.desktop", "nonsense", "maybe", 99))
        self.assertEqual(profiles.make_profile("a.desktop", None, False, None).clicklock, False)
        self.assertIsNone(profiles.make_profile("a.desktop", None, None, True))

    def test_summary_lists_only_what_changes(self):
        self.assertEqual(profiles.summary(Profile("a", "Strong", False, 1)),
                         "Strong protection · Click-Lock off · DPI stage 2")
        self.assertEqual(profiles.summary(Profile("a", None, True, None)), "Click-Lock on")

    def test_engine_values_map_presets(self):
        self.assertEqual(profiles.engine_values(Profile("a", "Light", None, 3)), {"protection": LIGHT, "stage": 3})

    def test_parse_drops_bad_entries_and_caps_the_count(self):
        items = [{"app": f"a{i}.desktop", "protection": "Light", "clicklock": "keep", "stage": None} for i in range(40)]
        items += [{"app": "a1.desktop", "protection": "Strong"}, "junk", {"app": "x y", "protection": "Light"},
                  {"app": "z.desktop"}]
        got = profiles.parse({"version": 1, "profiles": items})
        self.assertEqual(len(got), profiles.MAX_PROFILES)
        self.assertEqual(got[1].app_id, "a1.desktop")
        self.assertEqual(got[1].protection, "Light")        # first one wins on a repeat
        for bad in (None, [], {"version": 2, "profiles": []}, {"version": 1, "profiles": "x"}):
            self.assertEqual(profiles.parse(bad), [])


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sub" / "profiles.json"
        self.store = profiles.ProfileStore(self.path)

    def test_round_trip_and_replace(self):
        self.assertEqual(self.store.save(Profile("a.desktop", "Strong", True, 1)), "")
        self.assertEqual(self.store.save(Profile("b.desktop", None, False, None)), "")
        self.assertEqual(self.store.save(Profile("a.desktop", "Light", None, None)), "")
        self.assertEqual(self.store.load(), [Profile("a.desktop", "Light", None, None),
                                             Profile("b.desktop", None, False, None)])
        self.assertEqual([f.name for f in self.path.parent.iterdir()], ["profiles.json"])

    def test_stores_nothing_but_ids_and_settings(self):
        self.store.save(Profile("a.desktop", "Strong", True, 1))
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc, {"version": 1, "profiles": [
            {"app": "a.desktop", "protection": "Strong", "clicklock": "on", "stage": 1}]})

    def test_limit_and_empty_profiles(self):
        for i in range(profiles.MAX_PROFILES):
            self.assertEqual(self.store.save(Profile(f"a{i}.desktop", "Light")), "")
        self.assertIn("up to", self.store.save(Profile("extra.desktop", "Light")))
        self.assertEqual(self.store.save(Profile("a0.desktop", "Strong")), "")      # replacing is fine
        self.assertNotEqual(self.store.save(Profile("e.desktop")), "")

    def test_delete(self):
        self.store.save(Profile("a.desktop", "Light"))
        self.assertEqual(self.store.delete("nope.desktop"), "")
        self.assertEqual(self.store.delete("a.desktop"), "")
        self.assertEqual(self.store.load(), [])

    def test_corrupt_or_missing_file_is_empty(self):
        self.assertEqual(self.store.load(), [])
        self.path.parent.mkdir(parents=True)
        for text in ("{not json", "[1,2]", '{"version": 1, "profiles": [1, null, {"app": 5}]}', "\x00\xff"):
            self.path.write_text(text, encoding="utf-8")
            self.assertEqual(self.store.load(), [])

    def test_unwritable_folder_reports_an_error(self):
        blocker = self.path.parent.parent / "file"
        blocker.write_text("x")
        err = profiles.ProfileStore(blocker / "profiles.json").save(Profile("a.desktop", "Light"))
        self.assertIn("Couldn't save", err)


class JournalTests(unittest.TestCase):
    def test_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.json"
            self.assertTrue(profiles.journal_save(p, {"protection": BALANCED, "clicklock": False, "stage": 2}))
            self.assertEqual(profiles.journal_load(p), {"protection": BALANCED, "clicklock": False, "stage": 2})
            p.write_text(json.dumps({"version": 1, "restore": {"protection": [1, 2], "clicklock": "x", "stage": 99}}))
            self.assertEqual(profiles.journal_load(p), {})
            p.write_text(json.dumps({"version": 1, "restore": {"protection": [9999, 25, 300], "stage": True}}))
            self.assertEqual(profiles.journal_load(p), {})
            p.write_text("garbage")
            self.assertEqual(profiles.journal_load(p), {})
            self.assertTrue(profiles.journal_save(p, {}))       # nothing owed removes the file
            self.assertFalse(p.exists())
            self.assertTrue(profiles.journal_clear(p))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.e = ProfileEngine({GAME_ID: GAME, OTHER_ID: {"clicklock": True},
                                EDITOR_ID: {"protection": LIGHT}}, clock=self.clock)
        self.e.observe(live())

    def test_engage_applies_only_what_differs_and_leave_restores_once(self):
        self.assertEqual(self.e.focus(GAME_ID, live()), GAME)
        self.e.observe(live(STRONG, True, 0))
        self.assertEqual(self.e.active, GAME_ID)
        plan = self.e.focus("some.desktop", live(STRONG, True, 0))
        self.assertEqual(plan, {"protection": BALANCED, "clicklock": False, "stage": 2})
        self.assertIsNone(self.e.active)
        self.e.observe(live(BALANCED, False, 2))
        self.assertEqual(self.e.focus("", live()), {})              # already left: nothing again
        self.assertEqual(self.e.focus("some.desktop", live()), {})

    def test_same_app_twice_is_a_no_op(self):
        self.e.focus(GAME_ID, live())
        self.assertEqual(self.e.focus(GAME_ID, live(STRONG, True, 0)), {})

    def test_values_already_in_place_are_not_resent_but_still_restored(self):
        cur = live(STRONG, True, 0)
        self.e.observe(cur)
        self.assertEqual(self.e.focus(GAME_ID, cur), {})
        self.assertEqual(self.e.focus("", cur), {})                 # the user's normal *is* the game's values

    def test_unknown_apps_do_nothing(self):
        self.assertEqual(self.e.focus("nothing.desktop", live()), {})
        self.assertEqual(self.e.focus("", live()), {})

    def test_user_changes_while_idle_move_the_base(self):
        self.e.observe(live(LIGHT, True, 1))
        self.e.focus(GAME_ID, live(LIGHT, True, 1))
        self.e.observe(live(STRONG, True, 0))
        self.assertEqual(self.e.focus("", live(STRONG, True, 0)), {"protection": LIGHT, "stage": 1})

    def test_hand_changes_during_a_profile_stick(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.observe(live(LIGHT, True, 0))                        # the user moved a slider
        self.assertEqual(self.e.focus("", live(LIGHT, True, 0)), {"clicklock": False, "stage": 2})

    def test_a_value_still_on_its_way_is_not_a_hand_change(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live())                                      # the daemon hasn't reported our change yet
        self.e.observe(live(STRONG, True, 0))
        self.assertEqual(self.e.focus("", live(STRONG, True, 0)),
                         {"protection": BALANCED, "clicklock": False, "stage": 2})

    def test_a_pending_value_that_never_arrives_becomes_a_hand_change(self):
        self.e.focus(GAME_ID, live())
        self.clock.t += profiles.SETTLE_S + 1
        self.e.observe(live(BALANCED, True, 2))                     # protection never changed, clicklock did
        plan = self.e.focus("", live(BALANCED, True, 2))
        self.assertNotIn("protection", plan)
        self.assertEqual(plan["clicklock"], False)

    def test_direct_switch_between_profiled_apps(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        plan = self.e.focus(OTHER_ID, live(STRONG, True, 0))        # OTHER only sets Click-Lock on
        self.assertEqual(plan, {"protection": BALANCED, "stage": 2})
        self.e.observe(live(BALANCED, True, 2))
        self.assertEqual(self.e.focus("", live(BALANCED, True, 2)), {"clicklock": False})

    def test_switch_keeps_the_original_base_not_the_first_profiles_values(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.focus(EDITOR_ID, live(STRONG, True, 0))
        self.e.observe(live(LIGHT, False, 2))
        self.assertEqual(self.e.focus("", live(LIGHT, False, 2)), {"protection": BALANCED})

    def test_hand_change_survives_a_switch_and_becomes_the_base(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.observe(live(STRONG, True, 3))                       # stage changed by hand
        self.e.focus(EDITOR_ID, live(STRONG, True, 3))
        self.e.observe(live(LIGHT, False, 3))
        plan = self.e.focus("", live(LIGHT, False, 3))
        self.assertEqual(plan, {"protection": BALANCED})            # stage 3 is the user's now

    def test_focus_returning_quickly_uses_pending_restore_values(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.focus("", live(STRONG, True, 0))                     # restore sent, not yet reflected
        self.assertEqual(self.e.focus(GAME_ID, live(STRONG, True, 0)), GAME)    # back in the game at once
        self.e.observe(live(BALANCED, False, 2))
        self.e.observe(live(STRONG, True, 0))
        self.assertEqual(self.e.focus("", live(STRONG, True, 0)),
                         {"protection": BALANCED, "clicklock": False, "stage": 2})

    def test_unavailable_values_are_skipped_and_retried(self):
        self.assertEqual(self.e.focus(GAME_ID, live(stage=None)), GAME)
        self.e.skipped(["stage"])
        self.assertTrue(self.e.has_retry())
        self.assertEqual(self.e.retry_plan(live(STRONG, True, None)), {})   # still unreadable
        self.assertEqual(self.e.retry_plan(live(STRONG, True, 2)), {"stage": 0})
        self.assertFalse(self.e.has_retry())
        self.e.observe(live(STRONG, True, 0))
        self.assertEqual(self.e.focus("", live(STRONG, True, 0)),
                         {"protection": BALANCED, "clicklock": False, "stage": 2})

    def test_mouse_offline_for_the_whole_profile_never_invents_a_restore(self):
        self.e.focus(GAME_ID, live(stage=None))
        self.e.skipped(["stage"])
        plan = self.e.focus("", live(STRONG, True, None))
        self.assertNotIn("stage", plan)

    def test_release_hands_everything_back_and_is_idempotent(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.assertEqual(self.e.release(live(STRONG, True, 0)),
                         {"protection": BALANCED, "clicklock": False, "stage": 2})
        self.assertEqual(self.e.release(live()), {})

    def test_refresh_after_an_edit_and_after_a_delete(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.set_profiles({GAME_ID: {"protection": LIGHT}, OTHER_ID: {"clicklock": True}})
        plan = self.e.refresh(live(STRONG, True, 0))
        self.assertEqual(plan, {"protection": LIGHT, "clicklock": False, "stage": 2})
        self.e.observe(live(LIGHT, False, 2))
        self.e.set_profiles({})
        self.assertEqual(self.e.refresh(live(LIGHT, False, 2)), {"protection": BALANCED})
        self.assertIsNone(self.e.active)

    def test_a_pending_restore_stays_in_the_journal_values(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.focus("", live(STRONG, True, None))                  # the mouse is asleep: stage can't go back
        self.e.skipped(["stage"])
        self.assertEqual(self.e.restore_values(), {"stage": 2})
        self.assertEqual(self.e.retry_plan(live(STRONG, True, 0)), {"stage": 2})
        self.assertEqual(self.e.restore_values(), {})

    def test_a_pending_restore_survives_the_next_profile_engaging(self):
        self.e.focus(GAME_ID, live())
        self.e.observe(live(STRONG, True, 0))
        self.e.focus("", live(STRONG, True, None))
        self.e.skipped(["stage"])
        self.e.focus(OTHER_ID, live(BALANCED, False, None))         # sets Click-Lock only, not the stage
        self.assertEqual(self.e.restore_values(), {"clicklock": False, "stage": 2})
        self.assertEqual(self.e.retry_plan(live(BALANCED, True, 0)), {"stage": 2})
        self.e.observe(live(BALANCED, True, 2))
        self.assertNotIn("stage", self.e.focus("", live(BALANCED, True, 2)))

    def test_restore_values_for_the_journal(self):
        self.assertEqual(self.e.restore_values(), {})
        self.e.focus(GAME_ID, live())
        self.assertEqual(self.e.restore_values(), {"protection": BALANCED, "clicklock": False, "stage": 2})
        self.e.observe(live(STRONG, True, 0))
        self.e.observe(live(STRONG, True, 1))                       # stage by hand: not ours to hand back
        self.assertNotIn("stage", self.e.restore_values())
        self.e.focus("", live(STRONG, True, 1))
        self.assertEqual(self.e.restore_values(), {})

    def test_seed_from_a_crashed_run_wins_over_the_stale_profile_values(self):
        e = ProfileEngine({OTHER_ID: {"clicklock": True}}, clock=self.clock)
        e.seed({"protection": BALANCED, "clicklock": False})
        cur = live(STRONG, True, 0)                                 # the game's values are still in place
        e.observe(cur)
        self.assertEqual(e.seed_plan(cur), {"protection": BALANCED, "clicklock": False})
        e.observe(live(BALANCED, False, 0))
        self.assertEqual(e.restore_values(), {})
        self.assertEqual(e.seed_plan(live(BALANCED, False, 0)), {})

    def test_seed_is_the_base_when_a_profile_engages_before_it_is_handed_back(self):
        e = ProfileEngine({OTHER_ID: {"clicklock": True}}, clock=self.clock)
        e.seed({"clicklock": False})
        cur = live(BALANCED, True, 0)
        e.observe(cur)
        e.focus(OTHER_ID, cur)
        self.assertEqual(e.focus("", cur), {"clicklock": False})


class DesktopAppTests(unittest.TestCase):
    def write(self, root, name, text):
        p = Path(root) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_scan_lists_shown_apps_only(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "a.desktop", "[Desktop Entry]\nType=Application\nName=Zed\nIcon=zed\n")
            self.write(d, "b.desktop", "[Desktop Entry]\nType=Application\nName=Alpha\nNoDisplay=true\n")
            self.write(d, "c.desktop", "[Desktop Entry]\nType=Application\nName=Gamma\nHidden=true\n")
            self.write(d, "d.desktop", "[Desktop Entry]\nType=Link\nName=Link\n")
            self.write(d, "e.desktop", "not an ini file at all\n\x00")
            self.write(d, "f.desktop", "[Desktop Entry]\nType=Application\n")
            self.write(d, "bad\x01name.desktop", "[Desktop Entry]\nType=Application\nName=Spacey\n")
            self.write(d, "sub/g.desktop", "[Desktop Entry]\nType=Application\nName=Beta\n")
            self.write(d, "huge.desktop", "[Desktop Entry]\nName=" + "x" * 70000 + "\n")
            apps = desktopapps.scan([Path(d)])
            self.assertEqual([(a["id"], a["name"]) for a in apps], [("sub-g.desktop", "Beta"), ("a.desktop", "Zed")])
            self.assertEqual(apps[1]["icon"], "zed")

    def test_steam_shortcuts_map_to_the_window_class(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "Portal 2.desktop", "[Desktop Entry]\nType=Application\nName=Portal 2\nIcon=steam_icon_620\n"
                                              "Exec=steam steam://rungameid/620\n")
            self.write(d, "Portal 2b.desktop", "[Desktop Entry]\nType=Application\nName=Portal Dupe\n"
                                                   "Exec=steam steam://rungameid/620\n")
            self.write(d, "steam.desktop", "[Desktop Entry]\nType=Application\nName=Steam\nExec=steam %U\n")
            apps = desktopapps.scan([Path(d)])
            self.assertEqual([(a["id"], a["name"], a["icon"]) for a in apps],
                             [("steam_app_620", "Portal 2", "steam_icon_620"), ("steam.desktop", "Steam", "")])
        self.assertEqual(desktopapps.steam_app_id("steam steam://rungameid/730 -x"), "steam_app_730")
        self.assertEqual(desktopapps.steam_app_id("steam steam://run/730"), "")
        self.assertEqual(desktopapps.steam_app_id(""), "")

    def test_first_folder_wins_and_search_matches_name_and_id(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            self.write(d1, "a.desktop", "[Desktop Entry]\nType=Application\nName=Mine\n")
            self.write(d2, "a.desktop", "[Desktop Entry]\nType=Application\nName=Theirs\n")
            apps = desktopapps.scan([Path(d1), Path(d2)])
            self.assertEqual([a["name"] for a in apps], ["Mine"])
            self.assertTrue(desktopapps.matches(apps[0], "  MIN "))
            self.assertTrue(desktopapps.matches(apps[0], "a.desk"))
            self.assertFalse(desktopapps.matches(apps[0], "zzz"))

    def test_data_dirs_follow_xdg(self):
        dirs = desktopapps.data_dirs({"XDG_DATA_HOME": "/h", "XDG_DATA_DIRS": "/x:/y"})
        self.assertEqual(dirs, [Path("/h/applications"), Path("/x/applications"), Path("/y/applications")])

    def test_display_name_falls_back_to_a_tidy_id(self):
        self.assertEqual(desktopapps.display_name("gone.desktop", []), "gone")
        self.assertEqual(desktopapps.display_name("a.desktop", [{"id": "a.desktop", "name": "A", "icon": ""}]), "A")


if __name__ == "__main__":
    unittest.main()
