import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import battlenet as bn
from bnet_steam import cli, steam, wizard
from bnet_steam.errors import BnetSteamError

from .helpers import FakeSteam


class FakeIO:
    """Stands in for the person at the terminal."""

    def __init__(self, confirms=(True,), on_wait=None, wait_result=True):
        self.confirms, self.on_wait, self.wait_result = list(confirms), on_wait, wait_result
        self.said, self.asked, self.waits = [], [], 0

    def say(self, text=""):
        self.said.append(text)

    def confirm(self, prompt, default=True):
        self.asked.append(prompt)
        return self.confirms.pop(0)

    def wait_for(self, condition, seconds, poll=5, on_enter=None):
        self.waits += 1
        if self.on_wait:
            self.on_wait()                      # the user "finishes the Battle.net install"
        return self.wait_result and condition()

    def text(self):
        return "\n".join(self.said)


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.env = self.fs.env
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
        self.assertIn("press Enter", text)

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
        io = FakeIO(confirms=[])
        wizard.run(self.env, io)
        self.assertIn("Checking for new Battle.net games", io.text())
        self.assertNotIn("close and reopen a few times", io.text())

    def test_a_first_time_user_is_told_what_to_expect(self):
        self.fresh()
        io = FakeIO(confirms=[False])
        wizard.run(self.env, io)
        self.assertIn("close and reopen a few times", io.text())

    def test_already_set_up_just_adds_games_without_questions(self):
        self.fs.add_game("w2bn", "w2", "Warcraft II", "Warcraft II Battle.net Edition", "Warcraft II BNE.exe")
        io = FakeIO(confirms=[])
        wizard.run(self.env, io)
        self.assertEqual(io.asked, [])
        names = [e["appname"] for e in self.fs.shortcuts().values()]
        self.assertIn("Warcraft II Battle.net Edition", names)
        self.assertIn("All done", io.text())

    def test_an_error_from_a_step_reaches_the_caller_to_be_shown_plainly(self):
        with mock.patch.object(cli, "cmd_sync", side_effect=BnetSteamError("Steam did not exit")):
            with self.assertRaisesRegex(BnetSteamError, "Steam did not exit"):
                wizard.run(self.env, FakeIO())

    def test_the_desktop_icon_failing_does_not_spoil_a_finished_setup(self):
        self.m_desktop.side_effect = BnetSteamError("no desktop")
        io = FakeIO()
        wizard.run(self.env, io)
        self.assertIn("All done", io.text())


class Console(unittest.TestCase):
    def test_wait_for_returns_when_the_condition_comes_true(self):
        flips = iter([False, False, True])
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch("time.sleep"):
            self.assertTrue(wizard.Console().wait_for(lambda: next(flips), 60, poll=0))

    def test_wait_for_gives_up_after_the_time_limit(self):
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch("time.sleep"), \
                mock.patch("time.time", side_effect=[0, 1, 2, 99, 100]):
            self.assertFalse(wizard.Console().wait_for(lambda: False, 10, poll=0))

    def test_pressing_enter_rechecks_and_reports_when_it_is_not_ready_yet(self):
        said = []
        flips = iter([False, False, False, True])
        with mock.patch("sys.stdin") as stdin, mock.patch("select.select", return_value=([stdin], [], [])):
            stdin.isatty.return_value = True
            stdin.readline.return_value = "\n"
            ok = wizard.Console().wait_for(lambda: next(flips), 60, poll=0,
                                           on_enter=lambda: said.append("not yet"))
        self.assertTrue(ok)
        self.assertEqual(said, ["not yet"])

    def test_confirm_defaults_to_yes_on_a_bare_enter(self):
        for typed, want in (("", True), ("y", True), ("YES", True), ("n", False), ("no", False)):
            with mock.patch("builtins.input", return_value=typed):
                self.assertEqual(wizard.Console().confirm("ok?"), want, typed)


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
