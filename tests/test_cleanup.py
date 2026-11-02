import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import cleanup, cli, steam, vdf, wizard
from bnet_steam.errors import BnetSteamError

from .helpers import FakeSteam, args, fake_home


class Base(unittest.TestCase):
    """A set-up machine: the launcher plus one game of ours, a user's own shortcut and art."""

    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.env = self.fs.env
        self.home = fake_home(self, self.fs)
        for name, kw in (("steam_running", {"return_value": False}), ("stop_steam", {}),
                         ("start_steam", {})):
            p = mock.patch.object(steam, name, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(wizard, "battlenet_running", return_value=False)
        self.running = p.start()
        self.addCleanup(p.stop)
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition",
                         "Warcraft II BNE.exe")
        cli.cmd_sync(self.env, args(learn=False))
        self.game = next(e for e in self.fs.shortcuts().values()
                         if e["appname"] == "Warcraft II Battle.net Edition")
        grid = self.env.grid_dir
        grid.mkdir(parents=True, exist_ok=True)
        for appid in (222, self.game["appid"], 111):         # launcher, game, user's own
            for stem in (f"{appid}p", f"{appid}_icon"):
                (grid / f"{stem}.png").write_bytes(b"x")
        self.icons = [self.home / ".local/bin/bnet-steam",
                      self.home / ".local/share/applications/bnet-steam.desktop",
                      self.home / "Desktop" / "Battle.net for Steam.desktop"]
        for f in self.icons:
            f.write_text("ours")
        cfg = self.env.config_path.read_text()
        for appid in (222, self.game["appid"], 111):
            cfg = vdf.set_compat_tool(cfg, appid, "proton_11")
        self.env.config_path.write_text(cfg)

    def names(self):
        return [e["appname"] for e in self.fs.shortcuts().values()]


class Plan(Base):
    def test_lists_the_launcher_and_our_games_but_never_the_users_own_shortcuts(self):
        p = cleanup.plan(self.env, self.fs.shortcuts(), cli.load_state(self.env))
        self.assertEqual(sorted(n for _, n in p.shortcuts),
                         ["Battle.net", "Warcraft II Battle.net Edition"])
        self.assertNotIn(111, p.appids)

    def test_art_is_only_found_for_our_own_shortcuts(self):
        p = cleanup.plan(self.env, self.fs.shortcuts(), cli.load_state(self.env))
        self.assertFalse([f for f in p.art if f.name.startswith("111")])
        self.assertTrue([f for f in p.art if f.name.startswith("222")])

    def test_a_fresh_machine_has_nothing_to_remove(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        shutil.rmtree(fs.env.work)
        fs.write_shortcuts({"0": fs.other})
        for f in self.icons:
            f.unlink()
        p = cleanup.plan(fs.env, fs.shortcuts(), {})
        self.assertTrue(p.empty())
        self.assertFalse(cleanup.set_up_before(fs.env, p))

    def test_human_sizes(self):
        self.assertEqual(cleanup.human(500), "500 B")
        self.assertEqual(cleanup.human(3 * 1024 ** 3), "3.0 GB")


class ShortcutsOnly(Base):
    def run_it(self, **kw):
        return cli.cmd_uninstall(self.env, args(**kw))

    def test_removes_our_shortcuts_and_keeps_everything_else(self):
        self.run_it()
        self.assertEqual(self.names(), ["My Emulator"])
        self.assertTrue(self.fs.prefix.exists())                  # Battle.net and games stay
        self.assertTrue((self.env.work / "run-game.sh").exists())
        self.assertTrue(all(f.exists() for f in self.icons))
        self.assertTrue((self.env.grid_dir / "222p.png").exists())     # art kept for next time

    def test_steam_files_are_backed_up_and_stay_valid(self):
        self.run_it()
        self.assertTrue(list(self.env.backups_dir.glob("shortcuts.vdf.bak-*")))
        self.assertTrue(vdf.roundtrips(self.env.shortcuts_path.read_bytes()))

    def test_only_our_compat_tool_entries_go(self):
        self.run_it()
        cfg = self.env.config_path.read_text()
        self.assertNotIn('"222"', cfg)
        self.assertNotIn(f'"{self.game["appid"]}"', cfg)
        self.assertIn('"111"', cfg)                               # the user's own
        self.assertIn('"280220"', cfg)                            # untouched Steam game
        self.assertTrue(vdf.braces_balanced(cfg))

    def test_it_forgets_what_it_created_so_a_later_run_starts_clean(self):
        self.run_it()
        self.assertEqual(cli.load_state(self.env)["games"], {})

    def test_dry_run_changes_nothing(self):
        before = self.env.shortcuts_path.read_bytes()
        self.run_it(dry_run=True, everything=True)
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)
        self.assertTrue(self.fs.prefix.exists())
        self.assertTrue(all(f.exists() for f in self.icons))

    def test_a_failure_restores_steam_and_deletes_nothing(self):
        before = self.env.shortcuts_path.read_bytes()
        with mock.patch.object(steam.Session, "save_config", side_effect=BnetSteamError("boom")):
            with self.assertRaises(BnetSteamError):
                self.run_it(everything=True)
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)
        self.assertTrue(self.fs.prefix.exists())
        self.assertTrue(all(f.exists() for f in self.icons))

    def test_steam_is_closed_and_reopened_around_the_edit(self):
        with mock.patch.object(steam, "steam_running", return_value=True):
            self.run_it()
        steam.stop_steam.assert_called_once()
        steam.start_steam.assert_called_once()

    def test_nothing_to_remove_is_not_an_error(self):
        self.run_it()
        self.assertTrue(self.run_it().shortcuts == [])


class Everything(Base):
    def run_it(self, **kw):
        return cli.cmd_uninstall(self.env, args(everything=True, **kw))

    def test_removes_the_data_art_command_and_icons(self):
        self.run_it()
        self.assertFalse(self.env.work.exists())
        self.assertEqual(self.names(), ["My Emulator"])
        self.assertFalse(any(f.exists() for f in self.icons))
        self.assertFalse((self.env.grid_dir / "222p.png").exists())
        self.assertFalse((self.env.grid_dir / f"{self.game['appid']}_icon.png").exists())

    def test_the_users_own_art_shortcuts_and_files_survive(self):
        other = self.home / "Desktop" / "My Game.desktop"
        other.write_text("mine")
        self.run_it()
        self.assertTrue((self.env.grid_dir / "111p.png").exists())
        self.assertTrue(other.exists())
        self.assertTrue(self.env.root.exists())
        self.assertTrue(self.home.exists())

    def test_refuses_while_battlenet_is_open(self):
        self.running.return_value = True
        with self.assertRaisesRegex(BnetSteamError, "close it first"):
            self.run_it()
        self.assertTrue(self.fs.prefix.exists())
        self.assertEqual(len(self.names()), 3)

    def test_needs_confirmation_when_not_told_yes(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(BnetSteamError):
                self.run_it(yes=False)
        self.assertTrue(self.fs.prefix.exists())

    def test_leftovers_are_cleaned_even_if_the_shortcuts_are_already_gone(self):
        self.fs.write_shortcuts({"0": self.fs.other})              # removed by hand in Steam
        self.run_it()
        self.assertFalse(self.env.work.exists())
        self.assertEqual(self.names(), ["My Emulator"])


class DataFolderSafety(Base):
    def env_at(self, path):
        return steam.SteamEnv(self.env.root, self.env.userdir, path)

    def test_never_deletes_the_home_folder_or_a_parent_of_it(self):
        for bad in (self.home, self.home.parent):
            (bad / "state.json").write_text("{}")      # looks ours, so only this rule can stop it
            self.addCleanup((bad / "state.json").unlink)
            with self.assertRaises(BnetSteamError, msg=str(bad)):
                cleanup.check_data_dir(self.env_at(bad))

    def test_never_deletes_the_steam_folder_or_anything_around_it(self):
        for bad in (self.env.root, self.env.root.parent, self.env.root / "steamapps"):
            (bad / "state.json").write_text("{}")
            self.addCleanup((bad / "state.json").unlink)
            with self.assertRaises(BnetSteamError, msg=str(bad)):
                cleanup.check_data_dir(self.env_at(bad))

    def test_never_deletes_a_folder_that_contains_the_home_folder(self):
        users = self.fs.tmp / "deep" / "users"
        home = users / "me"
        home.mkdir(parents=True)
        (users / "state.json").write_text("{}")
        with mock.patch.object(Path, "home", return_value=home):
            with self.assertRaises(BnetSteamError):
                cleanup.check_data_dir(self.env_at(users))

    def test_never_deletes_the_filesystem_root(self):
        with self.assertRaises(BnetSteamError):
            cleanup.check_data_dir(self.env_at(Path("/")))

    def test_never_deletes_a_folder_that_is_not_clearly_ours(self):
        stranger = self.fs.tmp / "documents"
        stranger.mkdir()
        (stranger / "thesis.txt").write_text("precious")
        with self.assertRaises(BnetSteamError):
            cleanup.check_data_dir(self.env_at(stranger))
        with self.assertRaises(BnetSteamError):
            cli.cmd_uninstall(self.env_at(stranger), args(everything=True))
        self.assertTrue((stranger / "thesis.txt").exists())

    def test_our_own_folder_passes(self):
        self.assertEqual(cleanup.check_data_dir(self.env), self.env.work.resolve())


class CompatToolEntries(unittest.TestCase):
    TEXT = ('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n'
            '\t\t\t{\n\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
            '\t\t\t\t\t"100"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"a"\n\t\t\t\t\t}\n'
            '\t\t\t\t\t"1000"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"b"\n\t\t\t\t\t}\n'
            '\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n')

    def test_removes_exactly_that_entry(self):
        out = vdf.remove_compat_tool(self.TEXT, 100)
        self.assertNotIn('"100"', out)
        self.assertIn('"1000"', out)                  # an id that merely starts with it stays
        self.assertTrue(vdf.braces_balanced(out))

    def test_set_then_remove_is_a_round_trip(self):
        added = vdf.set_compat_tool(self.TEXT, 555, "proton_11")
        self.assertIn('"555"', added)
        self.assertEqual(vdf.remove_compat_tool(added, 555), self.TEXT)

    def test_a_missing_entry_or_section_is_left_alone(self):
        self.assertEqual(vdf.remove_compat_tool(self.TEXT, 7), self.TEXT)
        self.assertEqual(vdf.remove_compat_tool("{}", 7), "{}")


if __name__ == "__main__":
    unittest.main()
