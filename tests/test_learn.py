import unittest
from unittest import mock

from bnet_steam import cli, learn, steam

from .helpers import FakeSteam, args

D4 = {"uid": "fenris", "code": "fenris", "name": "Diablo IV",
      "install_path": "C:/Program Files (x86)/Diablo IV"}
W2 = {"uid": "w2", "code": "w2bn", "name": "Warcraft II",
      "install_path": "C:/Program Files (x86)/Warcraft II"}
D4_LINE = r"C:\Program Files (x86)\Diablo IV\Diablo IV.exe -nostreamline -sso -launch -uid fenris"


class ParseLaunch(unittest.TestCase):
    def test_real_diablo_iv_command_is_captured_with_all_its_flags(self):
        g, rel, gargs = learn.parse_launch(D4_LINE, [D4, W2])
        self.assertEqual((g["code"], rel), ("fenris", "Diablo IV.exe"))
        self.assertEqual(gargs, "-nostreamline -sso -launch -uid fenris")
        self.assertEqual(learn.to_spec(g, rel, gargs),
                         {"exe": "Diablo IV.exe", "args": "-nostreamline -sso -launch -uid {uid}"})

    def test_helper_processes_without_uid_are_ignored(self):
        for line in (r"C:\Program Files (x86)\Diablo IV\BlizzardBrowser\BlizzardBrowser.exe --type=gpu",
                     r"C:\Program Files (x86)\Diablo IV\Diablo IV Launcher.exe",
                     r"C:\Program Files (x86)\Battle.net\Battle.net.exe --exec=launch fenris",
                     "python3 something", ""):
            self.assertIsNone(learn.parse_launch(line, [D4, W2]), line)

    def test_uid_must_belong_to_that_games_install_dir(self):
        line = r"C:\Program Files (x86)\Warcraft II\Warcraft II BNE.exe -uid fenris"
        self.assertIsNone(learn.parse_launch(line, [D4, W2]))

    def test_uid_distinct_from_code(self):
        line = r"C:\Program Files (x86)\Warcraft II\Warcraft II BNE.exe -uid w2"
        g, rel, gargs = learn.parse_launch(line, [D4, W2])
        self.assertEqual((g["code"], rel, learn.to_spec(g, rel, gargs)["args"]),
                         ("w2bn", "Warcraft II BNE.exe", "-uid {uid}"))


class Watch(unittest.TestCase):
    def test_returns_hit_when_game_appears_later(self):
        seq = iter([[], ["x"], [D4_LINE]])
        clock = iter(range(100))
        hit = learn.watch([D4], 50, lines=lambda: next(seq), now=lambda: next(clock),
                          sleep=lambda s: None)
        self.assertEqual(hit[0]["code"], "fenris")

    def test_times_out_with_none(self):
        clock = iter([0, 1, 2, 3, 99, 100])
        self.assertIsNone(learn.watch([D4], 5, lines=lambda: [], now=lambda: next(clock),
                                      sleep=lambda s: None))

    def test_enter_skips(self):
        with mock.patch("select.select", return_value=([object()], [], [])), \
                mock.patch("sys.stdin") as stdin:
            stdin.readline.return_value = "\n"
            self.assertIsNone(learn.watch([D4], 50, skip_on_enter=True, lines=lambda: [],
                                          now=lambda: 0))


class GuidedSync(unittest.TestCase):
    """sync walks the user through games it can only guess at."""

    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.env = self.fs.env
        self.fs.add_game("fenris", "fenris", "Diablo IV", "Diablo IV", "Diablo IV Launcher.exe")
        (self.fs.prefix / "pfx/drive_c/Program Files (x86)/Diablo IV/Diablo IV.exe").write_bytes(b"x" * 999)
        patches = [mock.patch.object(steam, "steam_running", return_value=False),
                   mock.patch.object(steam, "stop_steam"), mock.patch.object(steam, "start_steam")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_sync(self, tty, hit=None, **kw):
        with mock.patch("sys.stdin.isatty", return_value=tty), \
                mock.patch("builtins.input", return_value="y"), \
                mock.patch.object(learn, "watch", return_value=hit) as w:
            cli.cmd_sync(self.env, args(**kw))
        return w

    def entry(self):
        return next(e for e in self.fs.shortcuts().values() if e["appname"] == "Diablo IV")

    def test_interactive_run_learns_the_real_command(self):
        g = {**D4, "icon_path": ""}
        w = self.run_sync(True, hit=(g, "Diablo IV.exe", "-nostreamline -sso -launch -uid fenris"),
                          yes=False)
        w.assert_called_once()
        self.assertEqual(cli.load_state(self.env)["learned"]["fenris"]["args"],
                         "-nostreamline -sso -launch -uid {uid}")
        # `-sso` means only the launcher can start it, so the shortcut opens the launcher on it
        self.assertTrue(self.entry()["exe"].strip('"').endswith("Battle.net.exe"))
        self.assertTrue(self.entry()["LaunchOptions"].endswith('%command% --exec="launch fenris"'))

    def test_a_learned_game_that_does_not_need_the_launcher_runs_directly(self):
        g = {**D4, "icon_path": ""}
        self.run_sync(True, hit=(g, "Diablo IV.exe", "-launch -uid fenris"), yes=False)
        self.assertTrue(self.entry()["exe"].strip('"').endswith("Diablo IV.exe"))
        self.assertTrue(self.entry()["LaunchOptions"].endswith("%command% -launch -uid fenris"))

    def test_user_skipping_falls_back_to_the_guess_but_still_picks_the_real_exe(self):
        self.run_sync(True, hit=None, yes=False)
        self.assertTrue(self.entry()["exe"].strip('"').endswith("Diablo IV.exe"))   # not the stub
        self.assertTrue(self.entry()["LaunchOptions"].endswith("%command% -uid fenris"))
        self.assertNotIn("learned", cli.load_state(self.env))

    def test_scripted_runs_never_prompt_or_block(self):
        self.run_sync(True, yes=True).assert_not_called()      # --yes on a terminal
        self.run_sync(False, yes=True).assert_not_called()     # no terminal at all

    def test_dry_run_never_prompts(self):
        self.run_sync(True, dry_run=True, yes=False).assert_not_called()

    def test_learn_flag_forces_it_even_when_scripted(self):
        w = self.run_sync(False, yes=True, learn=True)
        w.assert_called_once()
        self.assertNotIn("learned", cli.load_state(self.env))   # watch found nothing

    def test_no_learn_flag_suppresses_it_on_a_terminal(self):
        self.run_sync(True, yes=False, learn=False).assert_not_called()

    def test_verified_and_learned_games_are_not_asked_about(self):
        state = {"learned": {"fenris": {"exe": "Diablo IV.exe", "args": "-uid {uid}"}}}
        cli.save_state(self.env, state)
        self.run_sync(True, yes=False).assert_not_called()


if __name__ == "__main__":
    unittest.main()
