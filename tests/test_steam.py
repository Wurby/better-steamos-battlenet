import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import steam, vdf
from bnet_steam.errors import BnetSteamError

from .helpers import FakeSteam

LOGINUSERS = '''"users"
{
\t"76561197960265729"
\t{
\t\t"AccountName"\t\t"first"
\t\t"MostRecent"\t\t"0"
\t}
\t"76561197960265730"
\t{
\t\t"AccountName"\t\t"second"
\t\t"MostRecent"\t\t"1"
\t}
}
'''


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for n in ("1", "2", "0"):
            (self.root / "userdata" / n / "config").mkdir(parents=True)
        (self.root / "config").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_most_recent_user_from_loginusers(self):
        (self.root / "config/loginusers.vdf").write_text(LOGINUSERS)
        self.assertEqual(steam.most_recent_account(self.root), 2)
        self.assertEqual(steam.find_userdir(self.root).name, "2")

    def test_explicit_user_wins_and_must_exist(self):
        self.assertEqual(steam.find_userdir(self.root, "1").name, "1")
        with self.assertRaises(BnetSteamError):
            steam.find_userdir(self.root, "99")

    def _put_shortcut(self, user, exe="/x/Battle.net-Setup.exe"):
        e = steam.new_shortcut(5, "Battle.net", exe, "")
        (self.root / "userdata" / user / "config/shortcuts.vdf").write_bytes(
            vdf.dumps({"shortcuts": {"0": e}}))

    def test_account_holding_our_shortcut_beats_login_order(self):
        # account 2 logged in last, but the shortcut lives in account 1
        from bnet_steam import battlenet as bn
        (self.root / "config/loginusers.vdf").write_text(LOGINUSERS)
        self._put_shortcut("1")
        self.assertEqual(steam.find_userdir(self.root, ours=bn.find_bnet_shortcut).name, "1")

    def test_hint_breaks_a_tie_between_accounts(self):
        from bnet_steam import battlenet as bn
        (self.root / "config/loginusers.vdf").write_text(LOGINUSERS)
        self._put_shortcut("1")
        self._put_shortcut("2")
        self.assertEqual(steam.find_userdir(self.root, ours=bn.find_bnet_shortcut, hint=1).name, "1")

    def test_shortcut_found_by_recorded_appid_and_exe_case(self):
        from bnet_steam import battlenet as bn
        sc = {"0": steam.new_shortcut(7, "Renamed", "/x/whatever.exe", "")}
        self.assertIsNone(bn.find_bnet_shortcut(sc))
        self.assertIs(bn.find_bnet_shortcut(sc, 7), sc["0"])
        sc = {"0": steam.new_shortcut(8, "B", "/x/BATTLE.NET.EXE", "")}
        self.assertIs(bn.find_bnet_shortcut(sc), sc["0"])

    def test_capitalised_field_names_are_found_and_kept(self):
        from bnet_steam import battlenet as bn
        raw = {"appid": 9, "AppName": "Battle.net", "Exe": '"/x/Battle.net-Setup.exe"',
               "LaunchOptions": ""}
        sc = {"0": steam.Entry(raw)}
        self.assertEqual(sc["0"]["exe"], raw["Exe"])
        self.assertEqual(bn.find_bnet_shortcut({"0": raw})["appid"], 9)   # plain dict too
        sc["0"]["exe"] = "new"
        self.assertEqual(list(sc["0"]), ["appid", "AppName", "Exe", "LaunchOptions"])
        self.assertEqual(sc["0"]["Exe"], "new")

    def test_an_entry_without_an_exe_is_never_the_launcher(self):
        from bnet_steam import battlenet as bn
        self.assertIsNone(bn.find_bnet_shortcut({"0": {"appid": 5, "appname": "Battle.net"}}, 5))

    def test_userdata_zero_is_never_chosen(self):
        import shutil
        shutil.rmtree(self.root / "userdata/1")
        shutil.rmtree(self.root / "userdata/2")
        (self.root / "userdata/0/config/shortcuts.vdf").write_bytes(vdf.dumps({"shortcuts": {}}))
        with self.assertRaises(BnetSteamError):           # only the placeholder "0" is left
            steam.find_userdir(self.root)

    def test_not_a_steam_dir(self):
        with self.assertRaises(BnetSteamError):
            steam.find_root(self.tmp.name + "/nope")

    def test_flatpak_only_is_refused_with_explanation(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata").mkdir(parents=True)
            with mock.patch.object(Path, "home", return_value=Path(home)):
                with self.assertRaisesRegex(BnetSteamError, "Flatpak"):
                    steam.find_root()

    def test_work_dir_honours_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/x/y"}):
            self.assertEqual(steam.default_work_dir(), Path("/x/y/bnet-steam"))


class Proton(unittest.TestCase):
    def test_tool_names(self):
        self.assertEqual(steam.proton_tool_name("Proton 11.0"), "proton_11")
        self.assertEqual(steam.proton_tool_name("Proton 9.0 (Beta)"), "proton_9")
        self.assertEqual(steam.proton_tool_name("Proton - Experimental"), "proton_experimental")
        self.assertEqual(steam.proton_tool_name("Proton Hotfix"), "proton_hotfix")
        self.assertIsNone(steam.proton_tool_name("Steamworks Common Redistributables"))

    def test_pick_newest_numbered_then_explicit_override(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for d in ("Proton 9.0 (Beta)", "Proton 11.0", "Proton - Experimental"):
                (root / "steamapps/common" / d).mkdir(parents=True)
                (root / "steamapps/common" / d / "proton").write_text("")
            (root / "compatibilitytools.d/GE-Proton10-1").mkdir(parents=True)
            (root / "compatibilitytools.d/GE-Proton10-1/proton").write_text("")
            self.assertEqual(steam.pick_proton(root), "proton_11")
            self.assertEqual(steam.pick_proton(root, "GE-Proton10-1"), "GE-Proton10-1")
            self.assertIn("GE-Proton10-1", [n for n, _ in steam.installed_protons(root)])

    def test_none_installed_is_an_error(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(BnetSteamError):
                steam.pick_proton(Path(t))


class Appids(unittest.TestCase):
    def test_formula_and_gameid(self):
        # crc32(exe + name) | 1<<31; the formula was checked against 14 real shortcuts
        self.assertEqual(steam.shortcut_appid('"/opt/game/run.sh"', "My Game"), 3617010538)
        self.assertEqual(steam.steam_gameid(0x80000001), (0x80000001 << 32) | 0x02000000)
        self.assertGreaterEqual(steam.shortcut_appid("x", "y"), 0x80000000)

    def test_next_key_never_collides(self):
        self.assertEqual(steam.next_key({}), "0")
        self.assertEqual(steam.next_key({"0": {}, "5": {}}), "6")


class Backups(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)

    def test_backup_copies_and_prunes_only_our_copies(self):
        env, p = self.fs.env, self.fs.env.shortcuts_path
        first = steam.backup(env, p)
        self.assertEqual(first.read_bytes(), p.read_bytes())
        for _ in range(steam.BACKUP_KEEP + 3):
            steam.backup(env, p)
        self.assertEqual(len(steam.list_backups(env, "shortcuts.vdf")), steam.BACKUP_KEEP)
        self.assertTrue(p.exists())

    def test_backup_mtime_is_backup_time_so_newest_sorts_first(self):
        import time
        env, p = self.fs.env, self.fs.env.shortcuts_path
        os.utime(p, (1000, 1000))                    # source last edited long ago
        b = steam.backup(env, p)
        self.assertGreater(b.stat().st_mtime, time.time() - 60)
        p.write_bytes(p.read_bytes())                # a later state, backed up second
        newer = steam.backup(env, p)
        self.assertEqual(steam.list_backups(env, "shortcuts.vdf")[0], newer)

    def test_legacy_backups_in_work_dir_are_listed_not_pruned(self):
        env = self.fs.env
        old = env.work / "shortcuts.vdf.bak-20200101-000000"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("x")
        self.assertIn(old, steam.list_backups(env, "shortcuts.vdf"))
        for _ in range(steam.BACKUP_KEEP + 2):
            steam.backup(env, env.shortcuts_path)
        self.assertTrue(old.exists())


class Strictness(unittest.TestCase):
    def test_strict_load_refuses_non_roundtripping_file(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        fs.env.shortcuts_path.write_bytes(fs.env.shortcuts_path.read_bytes() + b"\x00")
        steam.load_shortcuts(fs.env)                         # lenient read is fine
        with self.assertRaisesRegex(BnetSteamError, "round-trip"):
            steam.load_shortcuts(fs.env, strict=True)

    def test_missing_file_is_empty(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        fs.env.shortcuts_path.unlink()
        self.assertEqual(steam.load_shortcuts(fs.env, strict=True), {"shortcuts": {}})


class WriteSession(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.env = self.fs.env
        self.running = mock.patch.object(steam, "steam_running", return_value=True)
        self.stop = mock.patch.object(steam, "stop_steam")
        self.start = mock.patch.object(steam, "start_steam")
        self.m_running, self.m_stop, self.m_start = (
            self.running.start(), self.stop.start(), self.start.start())
        for p in (self.running, self.stop, self.start):
            self.addCleanup(p.stop)

    def test_closes_steam_backs_up_edits_and_restarts(self):
        with steam.write_session(self.env, "test", yes=True) as s:
            data = s.load_shortcuts()
            data["shortcuts"]["9"] = steam.new_shortcut(5, "X", "/x", "")
            s.save_shortcuts(data)
        self.m_stop.assert_called_once()
        self.m_start.assert_called_once()
        self.assertIn("9", self.fs.shortcuts())
        self.assertEqual(len(steam.list_backups(self.env, "shortcuts.vdf")), 1)
        self.assertEqual(len(steam.list_backups(self.env, "config.vdf")), 1)

    def test_error_inside_rolls_back_both_files_and_still_restarts(self):
        before_s, before_c = self.env.shortcuts_path.read_bytes(), self.env.config_path.read_bytes()
        with self.assertRaises(RuntimeError):
            with steam.write_session(self.env, "test", yes=True) as s:
                data = s.load_shortcuts()
                data["shortcuts"]["9"] = steam.new_shortcut(5, "X", "/x", "")
                s.save_shortcuts(data)
                s.save_config(vdf.set_compat_tool(s.read_config(), 5, "proton_11"))
                raise RuntimeError("boom")
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before_s)
        self.assertEqual(self.env.config_path.read_bytes(), before_c)
        self.m_start.assert_called_once()

    def test_unconfirmed_non_interactive_run_changes_nothing_and_leaves_steam_alone(self):
        before = self.env.shortcuts_path.read_bytes()
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(BnetSteamError, "--yes"):
                with steam.write_session(self.env, "test", yes=False):
                    self.fail("body must not run")
        self.m_stop.assert_not_called()
        self.m_start.assert_not_called()
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)

    def test_declined_prompt_cancels(self):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", return_value="n"):
            with self.assertRaisesRegex(BnetSteamError, "cancelled"):
                with steam.write_session(self.env, "test", yes=False):
                    self.fail("body must not run")
        self.m_stop.assert_not_called()

    def test_steam_that_will_not_close_aborts_without_writing_or_restarting(self):
        self.m_stop.side_effect = BnetSteamError("Steam did not exit")
        before = self.env.shortcuts_path.read_bytes()
        with self.assertRaises(BnetSteamError):
            with steam.write_session(self.env, "test", yes=True):
                self.fail("body must not run")
        self.m_start.assert_not_called()
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)
        self.assertEqual(steam.list_backups(self.env, "shortcuts.vdf"), [])

    def test_does_not_start_steam_that_was_not_running_unless_asked(self):
        self.m_running.return_value = False
        with steam.write_session(self.env, "test", yes=True):
            pass
        self.m_stop.assert_not_called()
        self.m_start.assert_not_called()
        with steam.write_session(self.env, "test", yes=True, start=True):
            pass
        self.m_start.assert_called_once()

    def test_invalid_config_is_refused_before_writing(self):
        with self.assertRaises(BnetSteamError):
            with steam.write_session(self.env, "test", yes=True) as s:
                s.save_config("not a config")
        self.assertIn("CompatToolMapping", self.env.config_path.read_text())

    def test_second_concurrent_session_is_refused(self):
        with steam.write_session(self.env, "outer", yes=True):
            with self.assertRaisesRegex(BnetSteamError, "already running"):
                with steam.write_session(self.env, "inner", yes=True):
                    pass


class StartSteam(unittest.TestCase):
    def test_steam_does_not_inherit_the_terminal_keyboard(self):
        with mock.patch("subprocess.Popen") as popen:
            steam.start_steam()
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
