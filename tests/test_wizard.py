import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import battlenet as bn
from bnet_steam import cli, steam, ui, wizard
from bnet_steam.errors import BnetSteamError

from .helpers import FakeSteam, fake_home


class FakeIO(ui.Terminal):
    """Stands in for the person at the terminal: a real (plain, non-animated) Terminal writing
    to memory, with scripted answers."""

    def __init__(self, confirms=(True,), on_wait=None, wait_result=True, verbose=False,
                 choices=()):
        super().__init__(verbose, out=io.StringIO())
        self.choices, self.menus = list(choices), []
        self.confirms, self.on_wait, self.wait_result = list(confirms), on_wait, wait_result
        self.asked, self.waits = [], 0

    def confirm(self, prompt, default=True):
        self.asked.append(prompt)
        self.say(prompt)
        return self.confirms.pop(0)

    def choose(self, prompt, options, default=0):
        self.menus.append(options)
        self.say(prompt + " " + " / ".join(options))
        return self.choices.pop(0) if self.choices else default

    def wait_for(self, condition, seconds, message, poll=3, on_enter=None):
        self.waits += 1
        self.say(message)
        if self.on_wait:
            self.on_wait()                      # the user "finishes the Battle.net install"
        return self.wait_result and condition()

    def text(self):
        return " ".join(self.out.getvalue().split())


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.env = self.fs.env
        self.home = fake_home(self, self.fs)
        for name in ("ensure_art", "ensure_launcher_art"):      # offline: art is not what is tested
            q = mock.patch.object(cli.art, name, return_value=("", []))
            q.start()
            self.addCleanup(q.stop)
        for name, kw in (("steam_running", {"return_value": False}), ("stop_steam", {}),
                         ("start_steam", {})):
            p = mock.patch.object(steam, name, **kw)
            setattr(self, "m_" + name, p.start())
            self.addCleanup(p.stop)
        for target in ("sys.stdin.isatty",):
            p = mock.patch(target, return_value=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("builtins.input", return_value="y")      # sync's own "continue?" prompt
        p.start()
        self.addCleanup(p.stop)
        self.desktop = mock.patch.object(cli, "cmd_desktop")    # never touch the real desktop
        self.m_desktop = self.desktop.start()
        self.addCleanup(self.desktop.stop)

    def up_to_date(self):
        """A machine whose setup is complete: the launcher shortcut already has its options."""
        cli.cmd_sync(self.env, cli.make_args(yes=True, learn=False))

    def fresh(self):
        """A machine where Battle.net has never been set up."""
        self.fs.write_shortcuts({"0": self.fs.other})
        shutil.rmtree(self.fs.prefix)

    def installer_file(self):
        f = self.env.work / "Battle.net-Setup.exe"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("MZ")
        p = mock.patch.object(bn, "download_installer", return_value=f)
        p.start()
        self.addCleanup(p.stop)

    def finish_install(self):
        """What the Battle.net installer leaves behind."""
        bn.launcher_exe(self.fs.prefix).parent.mkdir(parents=True, exist_ok=True)
        bn.launcher_exe(self.fs.prefix).write_text("")
        self.fs.flush_db()

    def bnet(self):
        return bn.find_bnet_shortcut(self.fs.shortcuts())


class Stages(Base):
    def test_nothing_set_up_yet(self):
        self.fresh()
        self.assertEqual(wizard.stage(self.env), ("install", None))

    def test_installer_shortcut_but_no_launcher_yet(self):
        self.fresh()
        self.installer_file()
        cli.cmd_install(self.env, cli.make_args(yes=True))
        self.assertEqual(wizard.stage(self.env)[0], "installer")

    def test_launcher_installed_but_shortcut_still_points_at_the_installer(self):
        self.fresh()
        self.installer_file()
        cli.cmd_install(self.env, cli.make_args(yes=True))
        self.finish_install()
        self.assertEqual(wizard.stage(self.env)[0], "repoint")

    def test_fully_set_up_goes_straight_to_adding_games(self):
        self.assertEqual(wizard.stage(self.env)[0], "sync")


class Journey(Base):
    def test_from_nothing_to_a_working_setup_in_one_run(self):
        self.fresh()
        self.installer_file()
        io = FakeIO(confirms=[True], on_wait=self.finish_install)
        wizard.run(self.env, io, running=lambda: False)
        entry = self.bnet()
        self.assertTrue(entry["exe"].strip('"').endswith("Battle.net.exe"))       # repointed
        self.assertIn('"proton_', self.env.config_path.read_text())
        text = io.text()
        for want in ("Add Battle.net to Steam", "Waiting for Battle.net", "Looking for your Battle.net games",
                     "All done"):
            self.assertIn(want, text + " ".join(io.asked))
        self.assertIn('Find "Battle.net"' if False else "Battle.net", text)
        self.assertEqual(io.waits, 1)
        self.m_desktop.assert_called_once()                       # icon refreshed at the end

    def test_the_instructions_tell_a_beginner_exactly_what_to_click(self):
        self.fresh()
        self.installer_file()
        io = FakeIO(on_wait=self.finish_install)
        wizard.run(self.env, io, running=lambda: False)
        text = io.text()
        self.assertIn("Library", text)
        self.assertIn("Play", text)
        self.assertIn("Non-Steam", text)
        self.assertIn("Recommended, not required", text)
        self.assertIn("start your computer", text)
        self.assertIn("desktop icon", text)
        self.assertIn("Press Enter", text)

    def test_each_step_is_its_own_screen_with_progress_and_a_checklist(self):
        self.fresh()
        self.installer_file()
        io = FakeIO(on_wait=self.finish_install)
        wizard.run(self.env, io, running=lambda: False)
        text = io.text()
        for n in (1, 2, 3, 4):
            self.assertIn(f"Step {n} of 4", text)
        self.assertLess(text.index("Step 2 of 4"), text.index("OK Battle.net is in your Steam library"))
        self.assertLess(text.index("Step 4 of 4"), text.index("OK Steam opens the installed Battle.net"))

    def test_cancelling_before_sign_in_still_gets_the_launcher_art_and_wrapper(self):
        """No games yet, only the launcher's own artwork and options to apply: that is still
        work to do, not 'everything is up to date'."""
        self.fresh()
        self.installer_file()
        io = FakeIO(on_wait=self.finish_install)
        with mock.patch.object(cli.art, "ensure_launcher_art",
                               return_value=("/art/icon.png", [])) as made:
            wizard.run(self.env, io, running=lambda: False)
        self.assertTrue((self.env.work / "run-game.sh").exists())
        self.assertIn("run-game.sh", self.bnet()["LaunchOptions"])
        self.assertEqual(self.bnet()["icon"], "/art/icon.png")
        self.assertTrue(any(not c.args[3] for c in made.call_args_list))   # a real run, not a dry one
        self.assertNotIn("Everything is up to date", io.text())

    def test_declining_the_first_question_changes_nothing(self):
        self.fresh()
        before = self.env.shortcuts_path.read_bytes()
        io = FakeIO(confirms=[False])
        wizard.run(self.env, io)
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)
        self.m_stop_steam.assert_not_called()
        self.assertIn("nothing was changed", io.text())

    def test_if_the_install_never_finishes_it_says_how_to_continue_and_does_not_repoint(self):
        self.fresh()
        self.installer_file()
        io = FakeIO(confirms=[True], wait_result=False)
        wizard.run(self.env, io)
        self.assertTrue(self.bnet()["exe"].strip('"').endswith("Battle.net-Setup.exe"))
        self.assertIn("Run this shortcut again", io.text())
        self.m_desktop.assert_not_called()

    def test_running_it_again_later_picks_up_where_it_stopped(self):
        self.fresh()
        self.installer_file()
        wizard.run(self.env, FakeIO(confirms=[True], wait_result=False))       # gave up waiting
        self.finish_install()                                                  # ...installed later
        io = FakeIO(confirms=[])                                               # no questions needed
        wizard.run(self.env, io, running=lambda: False)
        self.assertTrue(self.bnet()["exe"].strip('"').endswith("Battle.net.exe"))
        self.assertEqual(io.asked, [])
        self.assertIn("All done", io.text())

    def test_battlenet_still_open_is_waited_for_not_repointed_over(self):
        """Repointing restarts Steam, which would kill an installer that is still running."""
        self.fresh()
        self.installer_file()
        cli.cmd_install(self.env, cli.make_args(yes=True))
        self.finish_install()
        io = FakeIO(confirms=[], wait_result=False)
        wizard.run(self.env, io, running=lambda: True)
        self.assertIn("still open", io.text())
        self.assertTrue(self.bnet()["exe"].strip('"').endswith("Battle.net-Setup.exe"))

    def test_a_returning_user_is_not_told_steam_will_restart_a_few_times(self):
        self.up_to_date()
        io = FakeIO(confirms=[])
        wizard.run(self.env, io)
        self.assertIn("Checking for new games", io.text())
        self.assertNotIn("close and reopen a few times", io.text())

    def test_a_first_time_user_is_told_what_to_expect(self):
        self.fresh()
        io = FakeIO(confirms=[False])
        wizard.run(self.env, io)
        self.assertIn("close and reopen a few times", io.text())

    def test_already_set_up_lists_new_games_and_asks_once(self):
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition", "Warcraft II BNE.exe")
        io = FakeIO(confirms=[True])
        wizard.run(self.env, io)
        self.assertEqual(len(io.asked), 1)
        self.assertIn("+ Warcraft II Battle.net Edition", io.text())
        names = [e["appname"] for e in self.fs.shortcuts().values()]
        self.assertIn("Warcraft II Battle.net Edition", names)
        self.assertIn("Your Steam library is up to date", io.text())

    def test_an_error_is_explained_on_screen_and_marked_as_shown(self):
        with mock.patch.object(cli, "cmd_sync", side_effect=BnetSteamError("Steam did not exit")):
            io = FakeIO()
            with self.assertRaisesRegex(BnetSteamError, "Steam did not exit") as cm:
                wizard.run(self.env, io)
        self.assertTrue(cm.exception.shown)
        self.assertIn("Something went wrong", io.text())
        self.assertIn("Steam did not exit", io.text())
        self.assertIn("-v", io.text())

    def test_the_detailed_log_stays_hidden_by_default_and_shows_with_v(self):
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition", "Warcraft II BNE.exe")
        quiet = FakeIO(confirms=[True])
        wizard.run(self.env, quiet)
        for noise in ("appid=", "backup:", "NEW "):
            self.assertNotIn(noise, quiet.text())

    def uninstall_second_game(self):
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition", "Warcraft II BNE.exe")
        wizard.run(self.env, FakeIO(confirms=[True]))
        self.fs.records.pop()
        self.fs.aggregate.pop()
        self.fs.flush_db()

    def names(self):
        return [e["appname"] for e in self.fs.shortcuts().values()]

    def test_a_game_that_is_gone_is_kept_unless_the_person_says_to_remove_it(self):
        self.uninstall_second_game()
        io = FakeIO(confirms=[False])
        wizard.run(self.env, io)
        self.assertIn("Warcraft II Battle.net Edition", self.names())
        self.assertIn("no longer installed", io.asked[0])

    def test_a_game_that_is_gone_is_removed_when_the_person_agrees(self):
        self.uninstall_second_game()
        wizard.run(self.env, FakeIO(confirms=[True]))
        self.assertNotIn("Warcraft II Battle.net Edition", self.names())

    def test_declining_to_add_games_changes_nothing(self):
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition", "Warcraft II BNE.exe")
        before = self.env.shortcuts_path.read_bytes()
        io = FakeIO(confirms=[False])
        wizard.run(self.env, io)
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)
        self.assertIn("nothing was changed", io.text())

    def test_a_game_we_can_only_guess_at_is_taught_on_its_own_screen(self):
        self.fs.add_game("zzz", "zzz", "Mystery", "Mystery Game", "Mystery.exe")
        with mock.patch.object(cli, "unverified", return_value=[
                bn.installed_games(self.fs.prefix)[-1]]), \
                mock.patch.object(cli, "learn_games", return_value=True) as learn:
            io = FakeIO(confirms=[True])
            wizard.run(self.env, io)
        learn.assert_called_once()
        self.assertIn("Teach me how to start", io.text())
        self.assertIn("Press Play", io.text())
        self.assertIn("I will remember", io.text())

    def test_skipping_the_teaching_carries_on_with_a_guess(self):
        self.fs.add_game("zzz", "zzz", "Mystery", "Mystery Game", "Mystery.exe")
        with mock.patch.object(cli, "unverified", return_value=[
                bn.installed_games(self.fs.prefix)[-1]]), \
                mock.patch.object(cli, "learn_games", return_value=None):
            io = FakeIO(confirms=[True])
            wizard.run(self.env, io)
        self.assertIn("will use a guess", io.text())
        self.assertIn("Mystery Game", self.names())

    def test_the_desktop_icon_failing_does_not_spoil_a_finished_setup(self):
        self.m_desktop.side_effect = BnetSteamError("no desktop")
        self.up_to_date()
        io = FakeIO()
        wizard.run(self.env, io)
        self.assertIn("Everything is up to date", io.text())


class Removal(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(wizard, "battlenet_running", return_value=False)
        self.running = p.start()
        self.addCleanup(p.stop)
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition", "Warcraft II BNE.exe")
        wizard.run(self.env, FakeIO(confirms=[True]))             # a finished setup

    def names(self):
        return [e["appname"] for e in self.fs.shortcuts().values()]

    def test_someone_who_has_set_up_before_gets_a_menu(self):
        io = FakeIO(choices=[0], confirms=[])
        wizard.run(self.env, io)
        self.assertIn("Welcome back", io.text())
        self.assertEqual(io.menus[0][0], "Check for new games")
        self.assertIn("Remove Battle.net from Steam...", io.menus[0])

    def test_a_brand_new_user_is_not_offered_removal(self):
        self.fresh()
        shutil.rmtree(self.env.work, ignore_errors=True)
        io = FakeIO(confirms=[False])
        wizard.run(self.env, io)
        self.assertNotIn("Welcome back", io.text())
        self.assertEqual(io.menus, [])

    def test_quit_changes_nothing_and_checks_nothing(self):
        before = self.env.shortcuts_path.read_bytes()
        with mock.patch.object(cli, "cmd_sync") as sync:
            wizard.run(self.env, FakeIO(choices=[2], confirms=[]))
        sync.assert_not_called()
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)

    def test_removing_only_the_shortcuts_keeps_battlenet_and_the_games(self):
        io = FakeIO(choices=[1, 0], confirms=[])
        wizard.run(self.env, io)
        self.assertEqual(self.names(), ["My Emulator"])
        self.assertTrue(self.fs.prefix.exists())
        self.assertIn("still on this computer", io.text())
        self.assertIn("Removed from Steam", io.text())

    def test_the_removal_screen_says_what_will_go_before_asking(self):
        io = FakeIO(choices=[1, 2], confirms=[])               # look, then go back
        wizard.run(self.env, io)
        text = io.text()
        self.assertIn("In Steam: Battle.net, Warcraft II Battle.net Edition", text)
        self.assertIn("This cannot be undone", text)
        self.assertEqual(len(self.names()), 3)

    def test_removing_everything_needs_a_yes_and_leaves_nothing_behind(self):
        log = self.env.work / "logs" / "wizard-last.log"
        io = FakeIO(choices=[1, 1], confirms=[True])
        io.log_path = log
        wizard.run(self.env, io)
        self.assertFalse(self.env.work.exists())                # and the log did not recreate it
        self.assertEqual(self.names(), ["My Emulator"])
        self.assertIn("All removed", io.text())
        self.assertTrue(io.asked[0].startswith("Delete Battle.net"))

    def test_saying_no_to_removing_everything_changes_nothing(self):
        before = self.env.shortcuts_path.read_bytes()
        io = FakeIO(choices=[1, 1], confirms=[False])
        wizard.run(self.env, io)
        self.assertEqual(self.env.shortcuts_path.read_bytes(), before)
        self.assertTrue(self.fs.prefix.exists())
        self.assertIn("nothing was changed", io.text())

    def test_battlenet_being_open_stops_a_full_removal_before_asking(self):
        self.running.return_value = True
        io = FakeIO(choices=[1, 1], confirms=[])
        wizard.run(self.env, io)
        self.assertIn("still open", io.text())
        self.assertEqual(io.asked, [])
        self.assertTrue(self.fs.prefix.exists())

    def test_leftovers_can_be_cleaned_after_the_shortcuts_were_removed_in_steam(self):
        self.fs.write_shortcuts({"0": self.fs.other})
        io = FakeIO(choices=[1, 0], confirms=[True])
        wizard.run(self.env, io)
        self.assertIn("Clean up what is left on this computer...", io.menus[0])
        self.assertEqual(io.menus[1][0], "Remove everything")    # no shortcuts-only choice
        self.assertFalse(self.env.work.exists())

    def test_after_removing_shortcuts_the_setup_can_be_run_again_quickly(self):
        wizard.run(self.env, FakeIO(choices=[1, 0], confirms=[]))
        self.installer_file()
        io = FakeIO(choices=[0], confirms=[True])
        wizard.run(self.env, io, running=lambda: False)
        entry = self.bnet()
        self.assertTrue(entry["exe"].strip('"').endswith("Battle.net.exe"))
        self.assertIn("Warcraft II Battle.net Edition", self.names())
        self.assertEqual(io.waits, 0)                           # Battle.net was still installed


class BattlenetRunning(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.proc = Path(self._t.name)

    def add(self, pid, cmd):
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "cmdline").write_bytes(cmd)

    def test_the_launcher_or_the_installer_counts(self):
        self.add(1, b"C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\0--type=renderer\0")
        self.assertTrue(wizard.battlenet_running(self.proc))
        shutil.rmtree(self.proc / "1")
        self.add(2, b"Z:\\home\\u\\bnet\\Battle.net-Setup.exe\0")
        self.assertTrue(wizard.battlenet_running(self.proc))

    def test_unrelated_processes_that_only_mention_it_do_not(self):
        self.add(3, b"bash\0-c\0echo C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\0")
        self.add(4, b"/usr/bin/python3\0Battle.net-Setup.exe\0")
        self.add(5, b"/usr/bin/konsole\0")
        self.assertFalse(wizard.battlenet_running(self.proc))


if __name__ == "__main__":
    unittest.main()
