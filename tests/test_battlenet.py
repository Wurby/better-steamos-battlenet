import json
import tempfile
import unittest
from pathlib import Path

from bnet_steam import battlenet as bn
from bnet_steam.errors import BnetSteamError

from .helpers import FakeSteam, product


class ProductDb(unittest.TestCase):
    def test_parses_uid_code_and_path_and_keeps_uid_distinct_from_code(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "product.db"
            p.write_bytes(product("agent", "agent", "C:/ProgramData/Battle.net/Agent")
                          + product("w2", "w2bn", "C:/Program Files (x86)/Warcraft II"))
            recs = bn.read_product_db(p)
        self.assertEqual(recs[1], {"uid": "w2", "code": "w2bn",
                                   "install_path": "C:/Program Files (x86)/Warcraft II"})

    def test_truncated_file_gives_clean_error(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "product.db"
            p.write_bytes(product("s1", "s1", "C:/x")[:-4])
            with self.assertRaisesRegex(BnetSteamError, "product.db"):
                bn.read_product_db(p)

    def test_empty_file_means_no_products(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "product.db"
            p.write_bytes(b"")
            self.assertEqual(bn.read_product_db(p), [])


class Games(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.prefix = self.fs.prefix

    def test_only_installed_games_and_never_agent_or_launcher(self):
        self.fs.add_game("s1", "s1", "StarCraft", "StarCraft", "x86_64/StarCraft.exe")
        (self.prefix / "pfx/drive_c/Program Files (x86)/StarCraft").rename(
            self.prefix / "pfx/drive_c/Program Files (x86)/StarCraft-gone")
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition",
                         "Warcraft II BNE.exe")
        games = bn.installed_games(self.prefix)
        self.assertEqual([g["code"] for g in games], ["w2bn"])
        self.assertEqual(games[0]["uid"], "w2")
        self.assertEqual(games[0]["name"], "Warcraft II Battle.net Edition")

    def test_name_falls_back_to_code_without_aggregate(self):
        self.fs.add_game("wow_classic_era", "wow_classic_era", "Zed", "Zed Game", "Zed.exe")
        (self.prefix / "pfx" / bn.AGGREGATE_REL).write_text("{not json")
        self.assertEqual(bn.installed_games(self.prefix)[0]["name"], "Wow Classic Era")

    def test_launch_spec_precedence_learned_then_verified_then_derived(self):
        self.fs.add_game("s1", "s1", "StarCraft", "StarCraft", "x86/StarCraft.exe")
        (self.prefix / "pfx/drive_c/Program Files (x86)/StarCraft/x86_64").mkdir()
        (self.prefix / "pfx/drive_c/Program Files (x86)/StarCraft/x86_64/StarCraft.exe").write_text("")
        self.fs.add_game("w2bn", "w2", "Warcraft II", "W2", "Warcraft II BNE.exe")
        self.fs.add_game("newgame", "ng", "New Game", "New Game", "New.exe")
        g = {x["code"]: x for x in bn.installed_games(self.prefix)}

        exe, args, src = bn.launch_spec(self.prefix, g["s1"], {})
        self.assertEqual((exe.name, args, src), ("StarCraft.exe", "-launch -uid s1", "verified"))
        self.assertEqual(exe.parent.name, "x86_64")           # verified exe beats aggregate's x86

        exe, args, src = bn.launch_spec(self.prefix, g["w2bn"], {})
        self.assertEqual((args, src), ("-uid w2", "verified"))   # uid, not code

        exe, args, src = bn.launch_spec(self.prefix, g["newgame"], {})
        self.assertEqual((args, src), ("-uid ng", "derived, unverified"))

        learned = {"learned": {"newgame": {"exe": "New.exe", "args": "-foo {uid} -bar"}}}
        _, args, src = bn.launch_spec(self.prefix, g["newgame"], learned)
        self.assertEqual((args, src), ("-foo ng -bar", "learned"))

    def _no_reported_exe(self, code):
        agg = self.prefix / "pfx" / bn.AGGREGATE_REL
        agg.write_text(json.dumps({"installed": [{"product_id": code, "name": "NG", "icon_path": ""}]}))

    def test_exe_is_found_in_the_install_dir_when_battlenet_reports_none(self):
        self.fs.add_game("ng", "ng", "New Game", "New Game", "New.exe")
        self._no_reported_exe("ng")
        exe, args, src = bn.launch_spec(self.prefix, bn.installed_games(self.prefix)[0], {})
        self.assertEqual((exe.name, args, src), ("New.exe", "-uid ng", "derived, unverified"))

    def test_only_helper_exes_means_fall_back_to_the_launcher(self):
        self.fs.add_game("ng", "ng", "New Game", "New Game", "CrashReporter.exe")
        self._no_reported_exe("ng")
        self.assertIsNone(bn.launch_spec(self.prefix, bn.installed_games(self.prefix)[0], {}))

    def test_launch_options_shape(self):
        p = Path("/p")
        self.assertEqual(bn.launch_options(p), 'STEAM_COMPAT_DATA_PATH="/p" %command%')
        self.assertEqual(bn.launch_options(p, Path("/l"), "-uid s1"),
                         'STEAM_COMPAT_DATA_PATH="/p" PROTON_LOG=1 PROTON_LOG_DIR="/l" '
                         '%command% -uid s1')


class Helpers(unittest.TestCase):
    def test_prefix_for_reads_launch_options_else_compatdata(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        self.assertEqual(bn.prefix_for(fs.env, fs.launcher), fs.prefix)
        plain = {"appid": 77, "LaunchOptions": ""}
        self.assertEqual(bn.prefix_for(fs.env, plain), fs.env.root / "steamapps/compatdata/77")

    def test_compatdata_prefix_is_flagged_as_unsafe(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        self.assertTrue(bn.in_compatdata(fs.env, fs.env.root / "steamapps/compatdata/77"))
        self.assertFalse(bn.in_compatdata(fs.env, fs.prefix))

    def test_stage_progress(self):
        with tempfile.TemporaryDirectory() as t:
            prefix = Path(t)
            self.assertEqual(bn.stage(prefix)[1], "installer not run yet")
            db = prefix / "pfx" / bn.PRODUCT_DB_REL
            db.parent.mkdir(parents=True)
            db.write_bytes(b"")
            self.assertEqual(bn.stage(prefix)[0], 2)
            bn.launcher_exe(prefix).parent.mkdir(parents=True)
            bn.launcher_exe(prefix).write_text("")
            self.assertEqual(bn.stage(prefix)[0], 3)


if __name__ == "__main__":
    unittest.main()


class CaseInsensitive(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        (self.d / "_classic_era_").mkdir()
        (self.d / "_classic_era_/WowClassic.exe").write_text("")

    def test_finds_the_real_file_whatever_the_case_and_returns_its_on_disk_name(self):
        got = bn.resolve_ci(self.d, "_classic_era_/WoWClassic.exe")
        self.assertEqual(got.name, "WowClassic.exe")
        self.assertEqual(bn.resolve_ci(self.d, "_CLASSIC_ERA_/wowclassic.EXE").name, "WowClassic.exe")

    def test_exact_name_wins_and_missing_is_none(self):
        (self.d / "A.exe").write_text("")
        self.assertEqual(bn.resolve_ci(self.d, "A.exe").name, "A.exe")
        self.assertIsNone(bn.resolve_ci(self.d, "nope.exe"))
        self.assertIsNone(bn.resolve_ci(self.d, "missing_dir/x.exe"))

    def test_art_code_fallbacks_go_from_specific_to_parent(self):
        self.assertEqual(bn.art_codes("wow_classic_era"), ["wow_classic_era", "wow_classic", "wow"])
        self.assertEqual(bn.art_codes("s1"), ["s1"])


class LearnedSpecCase(unittest.TestCase):
    def test_learned_exe_whose_case_differs_from_disk_still_launches_the_real_file(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        fs.add_game("wow_classic_era", "wow_classic_era", "World of Warcraft", "WoW Classic Era",
                    "_classic_era_/WowClassic.exe")
        g = bn.installed_games(fs.prefix)[0]
        state = {"learned": {"wow_classic_era": {"exe": "_classic_era_/WoWClassic.exe",
                                                 "args": "-launcherlogin -uid {uid} -initialgamemode=cera"}}}
        exe, args, src = bn.launch_spec(fs.prefix, g, state)
        self.assertEqual((exe.name, src), ("WowClassic.exe", "learned"))
        self.assertEqual(args, "-launcherlogin -uid wow_classic_era -initialgamemode=cera")


class LauncherLogin(unittest.TestCase):
    def test_flags_that_mean_the_launcher_must_start_the_game(self):
        self.assertTrue(bn.signs_in_via_launcher("-launcherlogin -uid wow_classic_era -initialgamemode=cera"))
        self.assertTrue(bn.signs_in_via_launcher("-nostreamline -sso -launch -uid fenris"))
        self.assertFalse(bn.signs_in_via_launcher("-launch -uid s1"))
        self.assertFalse(bn.signs_in_via_launcher("-uid w2"))
        self.assertFalse(bn.signs_in_via_launcher(""))

    def test_family_ids_come_from_what_the_launcher_logged_on_play(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        logs = fs.prefix / "pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs"
        logs.mkdir(parents=True)
        (logs / "battle.net-20260101T000000.log").write_bytes(
            b"D x [GameController] {Main} Selecting game family by id: WoWC\r\n"
            b"I x [GameLaunchController] {Main} LaunchBinary: uid=wow_classic_era selectedRegion=US\r\n"
            b"D x [GameController] {Main} Selecting game family by id: Pro\r\n"       # browsed, not played
            b"D x [GameController] {Main} Selecting game family by id: Fen\r\n"
            b"I x [GameLaunchController] {Main} LaunchBinary: uid=fenris selectedRegion=US\r\n")
        (logs / "battle.net-20260102T000000.log").write_bytes(
            b"D x [GameController] {Main} Selecting game family by id: S1\r\n"
            b"I x [GameLaunchController] {Main} LaunchBinary: uid=s1 selectedRegion=US\r\n")
        self.assertEqual(bn.launcher_families(fs.prefix),
                         {"wow_classic_era": "WoWC", "fenris": "Fen", "s1": "S1"})

    def test_a_launch_with_no_preceding_selection_is_not_mapped_to_nothing(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        logs = fs.prefix / "pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs"
        logs.mkdir(parents=True)
        (logs / "battle.net-20260101T000000.log").write_bytes(
            b"I x [GameLaunchController] {Main} LaunchBinary: uid=mystery selectedRegion=US\r\n")
        self.assertEqual(bn.launcher_families(fs.prefix), {})

    def test_no_logs_means_no_families(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        self.assertEqual(bn.launcher_families(fs.prefix), {})


class BestExe(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)

    def make(self, name, size):
        (self.d / name).write_bytes(b"x" * size)
        return self.d / name

    def test_launcher_stub_is_skipped_for_the_real_game(self):
        """Diablo IV: Battle.net reports the 5 MB launcher stub, the game is the big exe."""
        stub = self.make("Diablo IV Launcher.exe", 5000)
        game = self.make("Diablo IV.exe", 58000)
        self.make("BlizzardError.exe", 2000)
        self.assertEqual(bn.best_exe(self.d, stub), game)

    def test_reported_exe_is_kept_when_it_is_not_a_helper(self):
        reported = self.make("Small.exe", 10)
        self.make("Bigger.exe", 9999)
        self.assertEqual(bn.best_exe(self.d, reported), reported)

    def test_real_exe_one_folder_down_is_found_when_the_root_has_only_a_launcher(self):
        """World of Warcraft: root has only its launcher stub; the game is in _classic_era_/."""
        (self.d / "_classic_era_").mkdir()
        (self.d / "_classic_era_/UTILS").mkdir()
        self.make("World of Warcraft Launcher.exe", 4000)
        game = self.make("_classic_era_/WowClassic.exe", 64000)
        self.make("_classic_era_/BlizzardError.exe", 700)
        self.make("_classic_era_/UTILS/BlizzardBrowser.exe", 2000)
        self.assertEqual(bn.best_exe(self.d, None), game)

    def test_root_exes_win_over_deeper_ones(self):
        (self.d / "sub").mkdir()
        root = self.make("Game.exe", 10)
        self.make("sub/Bigger.exe", 9999)
        self.assertEqual(bn.best_exe(self.d, None), root)

    def test_helper_only_folder_gives_none(self):
        self.make("unins000.exe", 10)
        self.make("CrashHandler.exe", 10)
        self.assertIsNone(bn.best_exe(self.d, None))
