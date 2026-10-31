import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import battlenet as bn
from bnet_steam import art, cli, steam, vdf
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
        launcher = {k: v for k, v in names["Battle.net"].items() if k != "LaunchOptions"}
        self.assertEqual(launcher, {k: v for k, v in before["1"].items() if k != "LaunchOptions"})
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
    def setUp(self):
        super().setUp()
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        patch = mock.patch.object(Path, "home", return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)

    def run_desktop(self, **kw):
        fake = mock.Mock(stdout=str(self.home / "Desktop") + "\n")   # never the real Desktop
        with mock.patch("subprocess.run", return_value=fake):
            cli.cmd_desktop(self.env, args(**kw), quiet=True)

    def test_writes_an_executable_script_that_runs_the_wizard_with_this_machines_paths(self):
        self.run_desktop()
        script = self.env.work / "bnet-steam.sh"
        entry = (self.home / ".local/share/applications/bnet-steam.desktop").read_text()
        self.assertTrue(os.access(script, os.X_OK))
        text = script.read_text()
        self.assertIn(" wizard", text)
        self.assertIn("sys.path.insert(0, sys.argv.pop(1))", text)     # not dependent on the cwd
        self.assertIn(str(self.env.work), text)
        self.assertIn(str(self.env.root), text)
        self.assertIn("Press Enter to close", text)                    # the window stays readable
        self.assertIn(f"Exec={script}", entry)
        self.assertIn("Name=Battle.net for Steam", entry)
        self.assertIn("Terminal=true", entry)

    def test_icon_is_the_generic_one_until_the_battlenet_icon_exists(self):
        self.run_desktop()
        self.assertIn("Icon=steam", (self.home / ".local/share/applications/bnet-steam.desktop").read_text())
        icon = self.env.work / "art" / "battlenet-icon.png"
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"png")
        self.run_desktop()
        self.assertIn(f"Icon={icon}", (self.home / ".local/share/applications/bnet-steam.desktop").read_text())

    def test_on_desktop_adds_a_desktop_icon_and_leaves_no_stray_files(self):
        self.run_desktop(on_desktop=True)
        self.assertTrue((self.home / "Desktop" / "Battle.net for Steam.desktop").exists())
        self.assertEqual(sorted(p.name for p in (self.home / "Desktop").iterdir()),
                         ["Battle.net for Steam.desktop"])

    def test_entries_from_older_versions_are_replaced_but_unrelated_files_are_not(self):
        apps = self.home / ".local/share/applications"
        apps.mkdir(parents=True)
        desk = self.home / "Desktop"
        desk.mkdir()
        old_a = apps / "bnet-steam-sync.desktop"
        old_b = desk / "Battle.net Sync.desktop"
        for f in (old_a, old_b):
            f.write_text("[Desktop Entry]\nName=Battle.net Sync\nExec=/x/sync.sh\n")
        mine = desk / "Battle.net Sync.desktop.mine"
        mine.write_text("keep me")
        other = desk / "Something Else.desktop"
        other.write_text("[Desktop Entry]\nName=Other\n")
        self.run_desktop(on_desktop=True)
        self.assertFalse(old_a.exists() or old_b.exists())
        self.assertTrue(mine.exists() and other.exists())

    def test_a_file_with_an_old_name_that_is_not_ours_is_kept(self):
        desk = self.home / "Desktop"
        desk.mkdir()
        theirs = desk / "Battle.net Sync.desktop"
        theirs.write_text("[Desktop Entry]\nName=My own thing\nExec=/usr/bin/true\n")
        self.run_desktop(on_desktop=True)
        self.assertEqual(theirs.read_text(), "[Desktop Entry]\nName=My own thing\nExec=/usr/bin/true\n")




class Prune(Base):
    """Cleanup of shortcuts for games that are no longer installed."""

    def setUp(self):
        super().setUp()
        self.fs.add_game("s1", "s1", "StarCraft", "StarCraft", "x86_64/StarCraft.exe")
        cli.cmd_sync(self.env, args())
        self.names = set(self.by_name())
        self.assertIn("StarCraft", self.names)
        self.assertIn("Warcraft II Battle.net Edition", self.names)

    def uninstall(self, code):
        """Remove a game the way Battle.net does: its folder and its product.db record."""
        i = [k for k, r in enumerate(self.fs.aggregate) if r["product_id"] == code][0]
        folder = self.fs.aggregate[i]["icon_path"].rsplit("/", 1)[0].split("/", 1)[1]
        import shutil
        shutil.rmtree(self.fs.prefix / "pfx/drive_c" / folder.replace("Program Files (x86)", "Program Files (x86)"))
        del self.fs.records[i]
        del self.fs.aggregate[i]
        self.fs.flush_db()

    def test_explicit_prune_removes_only_that_shortcut_and_keeps_keys_contiguous(self):
        before = self.fs.shortcuts()
        self.uninstall("w2bn")
        cli.cmd_sync(self.env, args(prune=True))
        after = self.fs.shortcuts()
        self.assertEqual(set(self.by_name()), self.names - {"Warcraft II Battle.net Edition"})
        self.assertEqual(sorted(after, key=int), [str(i) for i in range(len(after))])
        keep = {e["appname"]: e for e in before.values()}
        for e in after.values():                          # nothing else was altered
            self.assertEqual(e, keep[e["appname"]])
        self.assertNotIn("w2", cli.load_state(self.env)["games"])
        self.assertIn("s1", cli.load_state(self.env)["games"])
        self.assertTrue(self.fs.prefix.is_dir())          # the Wine prefix is never touched

    def test_scripted_run_without_prune_only_reports(self):
        self.uninstall("w2bn")
        snap = self.env.shortcuts_path.read_bytes()
        cli.cmd_sync(self.env, args())                    # --yes, no --prune
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)

    def test_interactive_run_asks_per_game_and_respects_no(self):
        self.uninstall("w2bn")
        snap = self.env.shortcuts_path.read_bytes()
        with mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", return_value="n") as ask:
            cli.cmd_sync(self.env, args(yes=False))
        ask.assert_called_once()
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)

    def test_interactive_yes_removes_it(self):
        self.uninstall("w2bn")
        with mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", return_value="y"):
            cli.cmd_sync(self.env, args(yes=False))
        self.assertNotIn("Warcraft II Battle.net Edition", self.by_name())

    def test_no_prune_flag_and_dry_run_never_remove(self):
        self.uninstall("w2bn")
        snap = self.env.shortcuts_path.read_bytes()
        cli.cmd_sync(self.env, args(prune=False))
        cli.cmd_sync(self.env, args(prune=True, dry_run=True))
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)

    def test_unreadable_battlenet_files_remove_nothing_even_with_prune(self):
        """If product.db vanished every game would look uninstalled; that must not delete."""
        (self.fs.prefix / "pfx" / bn.PRODUCT_DB_REL).unlink()
        snap = self.env.shortcuts_path.read_bytes()
        cli.cmd_sync(self.env, args(prune=True))
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)

    def test_missing_launcher_exe_also_blocks_pruning(self):
        self.uninstall("w2bn")
        bn.launcher_exe(self.fs.prefix).unlink()
        snap = self.env.shortcuts_path.read_bytes()
        cli.cmd_sync(self.env, args(prune=True))
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)

    def test_launcher_and_users_own_shortcuts_are_never_removed(self):
        state = cli.load_state(self.env)
        state["games"]["bogus"] = 222            # state wrongly claims the launcher is a game
        cli.save_state(self.env, state)
        self.uninstall("w2bn")
        cli.cmd_sync(self.env, args(prune=True))
        names = set(self.by_name())
        self.assertIn("Battle.net", names)
        self.assertIn("My Emulator", names)

    def test_reinstalled_game_is_added_back(self):
        self.uninstall("w2bn")
        cli.cmd_sync(self.env, args(prune=True))
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition",
                         "Warcraft II BNE.exe")
        cli.cmd_sync(self.env, args())
        self.assertIn("Warcraft II Battle.net Edition", self.by_name())

    def test_failed_write_restores_the_removed_shortcut(self):
        self.uninstall("w2bn")
        snap = self.env.shortcuts_path.read_bytes()
        config = self.env.config_path.read_bytes()
        # fail after shortcuts.vdf has already been rewritten without the shortcut
        with mock.patch.object(steam.Session, "save_config", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                cli.cmd_sync(self.env, args(prune=True))
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)
        self.assertEqual(self.env.config_path.read_bytes(), config)
        self.assertIn("Warcraft II Battle.net Edition", self.by_name())


class LauncherIcon(Base):
    def setUp(self):
        super().setUp()
        from .helpers import build_pe
        bn.launcher_exe(self.fs.prefix).write_bytes(build_pe([(0, 0, 32, b"logo")]))
        self.icon = self.env.grid_dir / "222_icon.png"
        self.composed = []

        def fake(icon, dest, w, h, fg):                 # the real one needs a decodable image
            self.composed.append((dest.name, w, h, fg))
            dest.write_bytes(b"composed")
        patcher = mock.patch.object(art, "_brand_compose", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def launcher(self):
        return self.by_name()["Battle.net"]

    def test_icon_is_extracted_from_the_launcher_exe_and_set(self):
        cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual(self.icon.read_bytes(), b"\x89PNG\r\n\x1a\nlogo")
        self.assertEqual(self.launcher()["icon"], str(self.icon))

    def test_second_run_is_a_no_op(self):
        cli.cmd_sync(self.env, args(no_art=False))
        snap = (self.env.shortcuts_path.read_bytes(), self.icon.read_bytes())
        self.m_stop.reset_mock()
        cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual((self.env.shortcuts_path.read_bytes(), self.icon.read_bytes()), snap)
        self.m_stop.assert_not_called()

    def test_a_icon_the_user_chose_is_never_replaced(self):
        sc = self.fs.shortcuts()
        sc["1"]["icon"] = "/my/own.png"
        self.fs.write_shortcuts(sc)
        cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual(self.launcher()["icon"], "/my/own.png")

    def test_art_the_user_already_placed_in_another_format_is_kept(self):
        self.env.grid_dir.mkdir(parents=True)
        mine = self.env.grid_dir / "222_icon.ico"
        mine.write_bytes(b"booped")
        cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual(mine.read_bytes(), b"booped")
        self.assertFalse(self.icon.exists())
        self.assertEqual(self.launcher()["icon"], str(mine))

    def test_no_art_and_dry_run_write_nothing(self):
        cli.cmd_sync(self.env, args(no_art=True))
        self.assertFalse(self.icon.exists())
        cli.cmd_sync(self.env, args(no_art=False, dry_run=True))
        self.assertFalse(self.icon.exists())

    def test_unreadable_exe_is_reported_not_fatal(self):
        bn.launcher_exe(self.fs.prefix).write_bytes(b"not an exe")
        cli.cmd_sync(self.env, args(no_art=False))            # must not raise
        self.assertEqual(self.launcher()["icon"], "")


class StaleState(Base):
    def test_state_forgets_a_shortcut_the_user_already_removed_in_steam(self):
        cli.cmd_sync(self.env, args())
        self.assertIn("w2", cli.load_state(self.env)["games"])
        sc = {k: e for k, e in self.fs.shortcuts().items() if e["appname"] != "Warcraft II Battle.net Edition"}
        self.fs.write_shortcuts({str(i): e for i, e in enumerate(sc.values())})
        self.fs.records.clear()
        self.fs.aggregate.clear()
        self.fs.flush_db()
        cli.cmd_sync(self.env, args())
        self.assertNotIn("w2", cli.load_state(self.env)["games"])

    def test_dry_run_leaves_state_alone(self):
        cli.cmd_sync(self.env, args())
        self.fs.write_shortcuts({"0": self.fs.other, "1": self.fs.launcher})
        self.fs.records.clear()
        self.fs.aggregate.clear()
        self.fs.flush_db()
        cli.cmd_sync(self.env, args(dry_run=True))
        self.assertIn("w2", cli.load_state(self.env)["games"])


class LauncherArt(LauncherIcon):
    """The launcher also gets a logo, portrait, wide capsule and hero."""

    def test_all_images_are_created_with_the_right_shapes(self):
        cli.cmd_sync(self.env, args(no_art=False))
        g = self.env.grid_dir
        for name in ("222p.jpg", "222.jpg", "222_hero.jpg", "222_icon.png", "222_logo.png"):
            self.assertTrue((g / name).exists(), name)
        self.assertEqual(sorted(self.composed), [("222.jpg", 920, 430, 300),
                                                 ("222_hero.jpg", 3840, 1240, 0),
                                                 ("222p.jpg", 600, 900, 380)])
        self.assertEqual((g / "222_logo.png").read_bytes(), self.icon.read_bytes())

    def test_second_run_creates_nothing_new(self):
        cli.cmd_sync(self.env, args(no_art=False))
        self.composed.clear()
        snap = {p.name: p.read_bytes() for p in self.env.grid_dir.iterdir()}
        cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual(self.composed, [])
        self.assertEqual({p.name: p.read_bytes() for p in self.env.grid_dir.iterdir()}, snap)

    def test_art_the_user_placed_in_any_format_is_never_replaced(self):
        self.env.grid_dir.mkdir(parents=True)
        (self.env.grid_dir / "222p.png").write_bytes(b"my portrait")
        (self.env.grid_dir / "222_hero.webp").write_bytes(b"my hero")
        (self.env.grid_dir / "222_logo.png").write_bytes(b"my logo")
        cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual([c[0] for c in self.composed], ["222.jpg"])       # only the wide one
        self.assertEqual((self.env.grid_dir / "222p.png").read_bytes(), b"my portrait")
        self.assertEqual((self.env.grid_dir / "222_logo.png").read_bytes(), b"my logo")
        self.assertFalse((self.env.grid_dir / "222p.jpg").exists())

    def test_without_ffmpeg_icon_and_logo_still_appear(self):
        with mock.patch("shutil.which", return_value=None):
            cli.cmd_sync(self.env, args(no_art=False))
        self.assertEqual(self.composed, [])
        self.assertTrue(self.icon.exists())
        self.assertTrue((self.env.grid_dir / "222_logo.png").exists())

    def test_dry_run_creates_nothing(self):
        cli.cmd_sync(self.env, args(no_art=False, dry_run=True))
        self.assertEqual(self.composed, [])
        self.assertFalse(self.env.grid_dir.exists())


@unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
class RealCompositing(unittest.TestCase):
    def test_real_ffmpeg_produces_exact_sizes_for_every_shape(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            icon = t / "icon.png"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=256x256", "-frames:v", "1", str(icon)], check=True)
            for w, h, fg in ((600, 900, 380), (920, 430, 300), (3840, 1240, 0)):
                out = t / f"{w}.jpg"
                art._brand_compose(icon, out, w, h, fg)
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of",
                     "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
                self.assertEqual(probe, f"{w},{h}")


class LauncherShortcuts(Base):
    """Games that sign in through the launcher get a shortcut that opens the launcher on them."""

    def setUp(self):
        super().setUp()
        self.fs.add_game("wow_classic_era", "wow_classic_era", "World of Warcraft", "WoW Classic Era",
                         "_classic_era_/WowClassic.exe")
        cli.save_state(self.env, {"learned": {"wow_classic_era": {
            "exe": "_classic_era_/WoWClassic.exe",
            "args": "-launcherlogin -uid {uid} -initialgamemode=cera"}}})
        self.script = self.env.work / "run-game.sh"
        logs = self.fs.prefix / "pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "battle.net-20260101T000000.log").write_bytes(
            b"D x [GameController] {Main} Selecting game family by id: WoWC\r\n"
            b"I x [GameLaunchController] {Main} LaunchBinary: uid=wow_classic_era selectedRegion=US\r\n")

    def opts(self, name):
        return self.by_name()[name]["LaunchOptions"]

    def test_login_games_open_the_launcher_on_their_family_and_other_games_run_directly(self):
        cli.cmd_sync(self.env, args())
        wow = self.by_name()["WoW Classic Era"]
        self.assertTrue(wow["exe"].strip('"').endswith("Battle.net.exe"))
        self.assertTrue(wow["LaunchOptions"].endswith('%command% --exec="launch WoWC"'))
        w2 = self.by_name()["Warcraft II Battle.net Edition"]
        self.assertTrue(w2["exe"].strip('"').endswith("Warcraft II BNE.exe"))
        self.assertTrue(w2["LaunchOptions"].endswith("%command% -uid w2"))

    def test_every_shortcut_runs_through_the_wrapper(self):
        cli.cmd_sync(self.env, args())
        self.assertIn(f'"{self.script}" --launcher-fix %command%', self.opts("WoW Classic Era"))
        self.assertIn(f'"{self.script}" %command%', self.opts("Warcraft II Battle.net Edition"))
        self.assertTrue(os.access(self.script, os.X_OK))

    def test_an_unknown_family_falls_back_to_the_product_code(self):
        (self.fs.prefix / "pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs/"
         "battle.net-20260101T000000.log").unlink()
        cli.cmd_sync(self.env, args())
        self.assertTrue(self.opts("WoW Classic Era").endswith('--exec="launch wow_classic_era"'))

    def test_existing_shortcut_switches_to_the_launcher_in_place_keeping_its_appid(self):
        learned = cli.load_state(self.env)["learned"]
        cli.save_state(self.env, {"learned": {}})          # at first the start command is a guess
        cli.cmd_sync(self.env, args())
        before = self.by_name()["WoW Classic Era"]
        self.assertTrue(before["exe"].strip('"').endswith("WowClassic.exe"))
        state = cli.load_state(self.env)
        state["learned"] = learned                          # now the real command is known
        cli.save_state(self.env, state)
        cli.cmd_sync(self.env, args())
        after = self.by_name()["WoW Classic Era"]
        self.assertTrue(after["exe"].strip('"').endswith("Battle.net.exe"))
        self.assertEqual(after["appid"], before["appid"])

    def test_games_without_a_usable_exe_also_open_the_launcher(self):
        self.fs.add_game("ng", "ng", "New Game", "New Game", "CrashReporter.exe")   # only a helper exe
        agg = self.fs.prefix / "pfx" / bn.AGGREGATE_REL
        data = json.loads(agg.read_text())
        data["installed"][-1]["icon_path"] = ""
        agg.write_text(json.dumps(data))
        cli.cmd_sync(self.env, args())
        self.assertTrue(self.opts("New Game").endswith('%command% --exec="launch ng"'))

    def test_dry_run_does_not_write_the_script(self):
        cli.cmd_sync(self.env, args(dry_run=True))
        self.assertFalse(self.script.exists())

    def test_wrapper_script_is_refreshed_when_stale(self):
        cli.cmd_sync(self.env, args())
        self.script.write_text("old")
        cli.cmd_sync(self.env, args())
        self.assertNotEqual(self.script.read_text(), "old")


class HandheldFix(Base):
    """The launcher is started through launcher.bat so it does not switch to its handheld UI."""

    def setUp(self):
        super().setUp()
        self.script = self.env.work / "run-game.sh"
        self.bat = self.fs.prefix / "pfx" / bn.LAUNCHER_SCRIPT_REL

    def launcher_opts(self):
        return self.by_name()["Battle.net"]["LaunchOptions"]

    def test_launcher_shortcut_runs_through_the_wrapper_with_the_fix_and_keeps_its_exe(self):
        before = self.by_name()["Battle.net"]
        cli.cmd_sync(self.env, args())
        after = self.by_name()["Battle.net"]
        self.assertEqual(after["exe"], before["exe"])                  # still points at Battle.net.exe
        self.assertEqual(after["appid"], before["appid"])
        self.assertIn(f'"{self.script}" --launcher-fix %command%', after["LaunchOptions"])
        self.assertIn("STEAM_COMPAT_DATA_PATH=", after["LaunchOptions"])

    def test_batch_file_reports_a_generic_board_then_starts_the_launcher_with_its_arguments(self):
        cli.cmd_sync(self.env, args())
        text = self.bat.read_bytes().decode()
        self.assertIn("\r\n", text)                                    # a Windows batch file
        self.assertNotIn("Valve", text)
        self.assertIn("BaseBoardManufacturer", text)
        self.assertIn("BaseBoardProduct", text)
        self.assertIn("To Be Filled By O.E.M.", text)
        self.assertTrue(text.splitlines()[-1].startswith('start "" "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" %*'))
        self.assertLess(text.index("reg add"), text.index("start"))   # values are set BEFORE it starts

    def test_second_run_changes_nothing(self):
        cli.cmd_sync(self.env, args())
        snap = (self.env.shortcuts_path.read_bytes(), self.bat.read_bytes())
        self.m_stop.reset_mock()
        cli.cmd_sync(self.env, args())
        self.assertEqual((self.env.shortcuts_path.read_bytes(), self.bat.read_bytes()), snap)
        self.m_stop.assert_not_called()

    def test_custom_launch_options_on_the_battlenet_shortcut_are_never_overwritten(self):
        sc = self.fs.shortcuts()
        sc["1"]["LaunchOptions"] = "MY_ENV=1 %command% --my-flag"
        self.fs.write_shortcuts(sc)
        cli.cmd_sync(self.env, args())
        self.assertEqual(self.launcher_opts(), "MY_ENV=1 %command% --my-flag")

    def test_the_fix_can_be_turned_off_and_back_on_and_the_choice_sticks(self):
        cli.cmd_sync(self.env, args(handheld_fix=False))
        self.assertNotIn("--launcher-fix", self.launcher_opts())
        self.assertIn("run-game.sh", self.launcher_opts())
        self.assertFalse(self.bat.exists())
        cli.cmd_sync(self.env, args())                                   # unspecified: stays off
        self.assertNotIn("--launcher-fix", self.launcher_opts())
        cli.cmd_sync(self.env, args(handheld_fix=True))
        self.assertIn("--launcher-fix", self.launcher_opts())
        self.assertTrue(self.bat.exists())

    def test_dry_run_writes_neither_the_batch_file_nor_the_script(self):
        snap = self.env.shortcuts_path.read_bytes()
        cli.cmd_sync(self.env, args(dry_run=True))
        self.assertFalse(self.bat.exists())
        self.assertFalse(self.script.exists())
        self.assertEqual(self.env.shortcuts_path.read_bytes(), snap)

    def test_stale_batch_file_is_refreshed(self):
        cli.cmd_sync(self.env, args())
        self.bat.write_bytes(b"old")
        cli.cmd_sync(self.env, args())
        self.assertNotEqual(self.bat.read_bytes(), b"old")
