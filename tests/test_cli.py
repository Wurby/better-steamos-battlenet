import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import battlenet as bn
from bnet_steam import cli, steam, vdf
from bnet_steam.errors import BnetSteamError

from .helpers import FakeSteam, args


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.env = self.fs.env
        patches = [mock.patch.object(steam, "steam_running", return_value=True),
                   mock.patch.object(steam, "stop_steam"),
                   mock.patch.object(steam, "start_steam")]
        self.m_running, self.m_stop, self.m_start = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition",
                         "Warcraft II BNE.exe")

    def files(self):
        return self.env.shortcuts_path.read_bytes(), self.env.config_path.read_bytes()

    def by_name(self):
        return {e["appname"]: e for e in self.fs.shortcuts().values()}


class Sync(Base):
    def test_adds_game_registers_proton_and_leaves_other_shortcuts_alone(self):
        before = self.fs.shortcuts()
        cli.cmd_sync(self.env, args())
        names = self.by_name()
        game = names["Warcraft II Battle.net Edition"]
        self.assertTrue(game["exe"].strip('"').endswith("Warcraft II BNE.exe"))
        self.assertTrue(game["LaunchOptions"].endswith("%command% -uid w2"))
        self.assertNotIn("PROTON_LOG", game["LaunchOptions"])
        self.assertEqual(names["My Emulator"], before["0"])
        self.assertEqual(names["Battle.net"], before["1"])
        cfg = self.env.config_path.read_text()
        self.assertRegex(cfg, rf'"{game["appid"]}"\s*\{{\s*"name"\s*"proton_11"')
        self.assertEqual(cli.load_state(self.env)["games"]["w2"], game["appid"])
        self.m_stop.assert_called_once()
        self.m_start.assert_called_once()

    def test_second_run_is_a_no_op_that_does_not_touch_steam(self):
        cli.cmd_sync(self.env, args())
        snap = self.files()
        self.m_stop.reset_mock()
        self.m_start.reset_mock()
        cli.cmd_sync(self.env, args())
        self.assertEqual(self.files(), snap)
        self.m_stop.assert_not_called()
        self.m_start.assert_not_called()

    def test_dry_run_writes_nothing_and_leaves_steam_alone(self):
        snap = self.files()
        cli.cmd_sync(self.env, args(dry_run=True))
        self.assertEqual(self.files(), snap)
        self.m_stop.assert_not_called()
        self.assertFalse((self.env.work / "state.json").exists())

    def test_changed_metadata_updates_in_place_keeping_appid(self):
        cli.cmd_sync(self.env, args())
        old = self.by_name()["Warcraft II Battle.net Edition"]
        self.fs.aggregate[0]["name"] = "Warcraft II: BNE"
        self.fs.flush_db()
        cli.cmd_sync(self.env, args())
        new = self.by_name()["Warcraft II: BNE"]
        self.assertEqual(new["appid"], old["appid"])
        self.assertEqual(len(self.fs.shortcuts()), 3)

    def test_users_custom_icon_survives_when_no_art_is_available(self):
        cli.cmd_sync(self.env, args())
        data = steam.load_shortcuts(self.env)
        for e in data["shortcuts"].values():
            if e["appname"].startswith("Warcraft"):
                e["icon"] = "/my/icon.png"
        self.fs.write_shortcuts(data["shortcuts"])
        snap = self.files()
        cli.cmd_sync(self.env, args())
        self.assertEqual(self.files(), snap)

    def test_proton_logging_is_opt_in_and_sticky(self):
        cli.cmd_sync(self.env, args(proton_log=True))
        self.assertIn("PROTON_LOG=1", self.by_name()["Warcraft II Battle.net Edition"]["LaunchOptions"])
        cli.cmd_sync(self.env, args())                 # unspecified: keep the saved choice
        self.assertIn("PROTON_LOG=1", self.by_name()["Warcraft II Battle.net Edition"]["LaunchOptions"])
        cli.cmd_sync(self.env, args(proton_log=False))
        self.assertNotIn("PROTON_LOG", self.by_name()["Warcraft II Battle.net Edition"]["LaunchOptions"])

    def test_refuses_until_launcher_is_repointed(self):
        sc = self.fs.shortcuts()
        sc["1"]["exe"] = '"/x/Battle.net-Setup.exe"'
        self.fs.write_shortcuts(sc)
        with self.assertRaisesRegex(BnetSteamError, "repoint"):
            cli.cmd_sync(self.env, args())

    def test_failure_midway_restores_both_files(self):
        snap = self.files()
        with mock.patch.object(vdf, "set_compat_tool", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                cli.cmd_sync(self.env, args())
        self.assertEqual(self.files(), snap)

    def test_unconfirmed_run_without_a_terminal_changes_nothing(self):
        snap = self.files()
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(BnetSteamError, "--yes"):
                cli.cmd_sync(self.env, args(yes=False))
        self.assertEqual(self.files(), snap)
        self.m_stop.assert_not_called()

    def test_refuses_a_shortcuts_file_it_cannot_reproduce(self):
        self.env.shortcuts_path.write_bytes(self.env.shortcuts_path.read_bytes() + b"\x00")
        snap = self.files()
        with self.assertRaisesRegex(BnetSteamError, "round-trip"):
            cli.cmd_sync(self.env, args())
        self.assertEqual(self.files(), snap)


class InstallRepoint(Base):
    def test_install_adds_installer_shortcut_outside_compatdata(self):
        self.fs.write_shortcuts({"0": self.fs.other})
        installer = self.env.work / "Battle.net-Setup.exe"
        installer.parent.mkdir(parents=True, exist_ok=True)
        installer.write_text("MZ")
        with mock.patch.object(bn, "download_installer", return_value=installer):
            cli.cmd_install(self.env, args())
            e = bn.find_bnet_shortcut(self.fs.shortcuts())
            self.assertEqual(e["exe"], f'"{installer}"')
            self.assertFalse(bn.in_compatdata(self.env, bn.prefix_for(self.env, e)))
            self.assertIn(f'"{e["appid"]}"', self.env.config_path.read_text())
            self.assertEqual(cli.load_state(self.env)["proton"], "proton_11")
            with self.assertRaisesRegex(BnetSteamError, "already exists"):
                cli.cmd_install(self.env, args())

    def test_repoint_switches_exe_and_keeps_appid_and_options(self):
        sc = self.fs.shortcuts()
        sc["1"]["exe"] = '"/x/Battle.net-Setup.exe"'
        self.fs.write_shortcuts(sc)
        cli.cmd_repoint(self.env, args())
        e = bn.find_bnet_shortcut(self.fs.shortcuts())
        self.assertEqual(e["exe"], f'"{bn.launcher_exe(self.fs.prefix)}"')
        self.assertEqual((e["appid"], e["LaunchOptions"]), (222, self.fs.launcher["LaunchOptions"]))

    def test_repoint_before_launcher_installed_is_an_error(self):
        bn.launcher_exe(self.fs.prefix).unlink()
        sc = self.fs.shortcuts()
        sc["1"]["exe"] = '"/x/Battle.net-Setup.exe"'
        self.fs.write_shortcuts(sc)
        with self.assertRaisesRegex(BnetSteamError, "not installed"):
            cli.cmd_repoint(self.env, args())


class Restore(Base):
    def test_restore_latest_undoes_last_change_and_is_itself_undoable(self):
        original = self.files()
        cli.cmd_sync(self.env, args())
        self.assertNotEqual(self.files(), original)
        n_before = len(steam.list_backups(self.env, "shortcuts.vdf"))
        cli.cmd_restore(self.env, args(latest=True))
        self.assertEqual(self.files(), original)
        self.assertEqual(len(steam.list_backups(self.env, "shortcuts.vdf")), n_before + 1)

    def test_restore_rejects_files_that_are_not_backups(self):
        junk = self.env.work / "notes.txt"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("x")
        with self.assertRaises(BnetSteamError):
            cli.cmd_restore(self.env, args(file=str(junk)))

    def test_restore_rejects_a_corrupt_backup_and_keeps_current_files(self):
        bad = self.env.work / "shortcuts.vdf.bak-20200101-000000"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"garbage")
        snap = self.files()
        with self.assertRaises(BnetSteamError):
            cli.cmd_restore(self.env, args(file=str(bad)))
        self.assertEqual(self.files(), snap)

    def test_listing_changes_nothing(self):
        snap = self.files()
        cli.cmd_restore(self.env, args())
        self.assertEqual(self.files(), snap)
        self.m_stop.assert_not_called()


class Desktop(Base):
    def test_writes_executable_script_and_entry_with_this_machines_paths(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(Path, "home", return_value=Path(home)):
            cli.cmd_desktop(self.env, args())
            script = self.env.work / "sync.sh"
            entry = (Path(home) / ".local/share/applications/bnet-steam-sync.desktop").read_text()
            self.assertTrue(os.access(script, os.X_OK))
            self.assertIn(str(self.env.work), script.read_text())
            self.assertIn(str(self.env.root), script.read_text())
            self.assertIn(f"Exec={script}", entry)
            self.assertIn("Terminal=true", entry)


if __name__ == "__main__":
    unittest.main()
