import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from bnet_steam import wrapper

from .helpers import FakeSteam

LAUNCHER_CMD = b"C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\0"

# Stand-in for Proton. Like the real one, `waitforexitandrun` cannot proceed while anything
# else is running in the prefix (it runs `wineserver -w` first); here that shows as BLOCKED.
FAKE_PROTON = r'''#!/bin/bash
REC="$FAKE_DIR/rec"
BLOG="$STEAM_COMPAT_DATA_PATH/pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs/battle.net-20990101T000000.log"
launcher_up() { ls "$BNET_STEAM_PROC"/*/cmdline >/dev/null 2>&1; }
record_game() {
    printf '%s' "$1" >>"$REC"; shift; printf ' [%s]' "$@" >>"$REC"; echo >>"$REC"
    if grep -aq 'State changed to: Ready' "$BLOG" 2>/dev/null; then echo "launcher-ready-at-game-start" >>"$REC"
    else echo "launcher-NOT-ready-at-game-start" >>"$REC"; fi
}
case "$1" in
  run)
    shift
    case "$1" in
      taskkill) echo "taskkill" >>"$REC"; rm -rf "$BNET_STEAM_PROC/900"; exit 0 ;;
      *Battle.net.exe)
        echo "start-launcher" >>"$REC"
        mkdir -p "$BNET_STEAM_PROC/900" "$(dirname "$BLOG")"
        printf 'C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\0' >"$BNET_STEAM_PROC/900/cmdline"
        printf 'I [GSStateTracker] {Main} State changed to: WaitingForAccountState\r\n' >"$BLOG"
        [ -z "${FAKE_NO_READY:-}" ] && ( sleep 0.4; printf 'I [GSStateTracker] {Main} State changed to: Ready\r\n' >>"$BLOG" ) &
        wait ;;
      *) record_game game-via-run "$@"; exit "${FAKE_RC:-0}" ;;
    esac ;;
  waitforexitandrun)
    shift
    if launcher_up; then echo "BLOCKED: waitforexitandrun while the launcher is up" >>"$REC"; exit 99; fi
    record_game game-via-waitforexitandrun "$@"; exit "${FAKE_RC:-0}" ;;
esac
'''


class WrapperScript(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.t = Path(self._t.name)
        self.fake, self.proc, self.compat = self.t / "fake", self.t / "proc", self.t / "compat"
        for d in (self.fake, self.proc, self.compat):
            d.mkdir()
        self.proton = self.t / "Proton 11.0" / "proton"
        self.proton.parent.mkdir()
        self.proton.write_text(FAKE_PROTON)
        self.proton.chmod(0o755)
        self.bnet = self.compat / "pfx/drive_c/Program Files (x86)/Battle.net/Battle.net.exe"
        self.bnet.parent.mkdir(parents=True)
        self.bnet.write_text("")
        self.blogs = self.compat / "pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs"
        self.script = self.t / "run-game.sh"
        self.script.write_text(wrapper.SCRIPT)
        self.script.chmod(0o755)
        self.game = self.t / "WowClassic.exe"

    def run_wrapper(self, *game_args, login=False, **env):
        e = {**os.environ, "FAKE_DIR": str(self.fake), "BNET_STEAM_PROC": str(self.proc),
             "STEAM_COMPAT_DATA_PATH": str(self.compat), "BNET_STEAM_WAIT": "10",
             "BNET_STEAM_POLL": "0.1", "BNET_STEAM_SETTLE": "0", **env}
        e = {k: v for k, v in e.items() if v is not None}
        cmd = [str(self.script)] + (["--login"] if login else []) + [
            str(self.proton), "waitforexitandrun", str(self.game), *game_args]
        return subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=60)

    def rec(self):
        p = self.fake / "rec"
        return p.read_text().splitlines() if p.exists() else []

    def games(self):
        return [x for x in self.rec() if x.startswith("game-via-")]

    def log(self):
        p = self.t / "logs/run-game.log"
        return p.read_text() if p.exists() else ""

    def open_launcher(self, ready=True):
        """Pretend the user already has the launcher open (and logged in)."""
        (self.proc / "42").mkdir()
        (self.proc / "42/cmdline").write_bytes(LAUNCHER_CMD)
        self.blogs.mkdir(parents=True, exist_ok=True)
        (self.blogs / "battle.net-20260101T000000.log").write_bytes(
            b"I x State changed to: " + (b"Ready" if ready else b"Loading") + b"\r\n")

    # --- the launcher-blocks-the-game problem (affects every game) ---------------------

    def test_with_no_launcher_and_no_login_the_command_runs_unchanged(self):
        r = self.run_wrapper("-uid", "w2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rec()[0], f"game-via-waitforexitandrun [{self.game}] [-uid] [w2]")
        self.assertNotIn("start-launcher", self.rec())
        self.assertFalse(any(x.startswith(("taskkill", "BLOCKED")) for x in self.rec()))

    def test_an_open_launcher_would_block_waitforexitandrun_so_run_is_used(self):
        self.open_launcher()
        r = self.run_wrapper("-uid", "s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.games(), [f"game-via-run [{self.game}] [-uid] [s1]"])
        self.assertFalse(any(x.startswith("BLOCKED") for x in self.rec()))
        self.assertNotIn("start-launcher", self.rec())
        self.assertFalse(any(x.startswith("taskkill") for x in self.rec()))
        self.assertTrue((self.proc / "42").exists())                 # the user's launcher stays open

    # --- games that sign in through the launcher (--login) -----------------------------

    def test_login_cold_start_waits_for_login_runs_game_then_closes_the_launcher_it_started(self):
        r = self.run_wrapper("-launcherlogin", "-uid", "wow_classic_era", login=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.rec()
        self.assertEqual(rec[0], "start-launcher")
        self.assertEqual(self.games(), [f"game-via-run [{self.game}] [-launcherlogin] [-uid] [wow_classic_era]"])
        self.assertIn("launcher-ready-at-game-start", rec)           # game began only after Ready
        self.assertIn("taskkill", rec)
        self.assertFalse((self.proc / "900").exists())                # launcher gone afterwards
        self.assertFalse(any(x.startswith("BLOCKED") for x in rec))
        self.assertIn("launcher logged in", self.log())

    def test_login_with_the_launcher_already_open_uses_it_and_leaves_it_running(self):
        self.open_launcher()
        r = self.run_wrapper("-uid", "x", login=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("start-launcher", self.rec())
        self.assertNotIn("taskkill", self.rec())
        self.assertEqual(len(self.games()), 1)
        self.assertTrue((self.proc / "42").exists())
        self.assertIn("already running", self.log())

    def test_the_login_flag_is_not_passed_on_to_the_game(self):
        self.run_wrapper("-uid", "x", login=True)
        self.assertNotIn("--login", " ".join(self.rec()))

    def test_an_old_ready_log_does_not_fool_a_cold_start(self):
        """Only a log newer than our own start counts, not yesterday's 'Ready'."""
        self.blogs.mkdir(parents=True)
        (self.blogs / "battle.net-20200101T000000.log").write_text("I x State changed to: Ready\n")
        self.run_wrapper("-uid", "x", login=True, FAKE_NO_READY="1", BNET_STEAM_WAIT="1")
        self.assertIn("launcher-NOT-ready-at-game-start", self.rec())
        self.assertIn("not logged in after 1s", self.log())

    def test_launcher_that_never_logs_in_does_not_block_the_game_forever(self):
        r = self.run_wrapper("-uid", "x", login=True, FAKE_NO_READY="1", BNET_STEAM_WAIT="1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(self.games()), 1)
        self.assertIn("running the game anyway", self.log())

    def test_ready_is_recognised_in_a_log_with_windows_line_endings(self):
        self.open_launcher(ready=True)                                # the helper writes CRLF
        self.run_wrapper("-uid", "x", login=True, BNET_STEAM_WAIT="1")
        self.assertIn("launcher logged in", self.log())

    def test_a_launcher_that_is_open_but_not_logged_in_is_waited_for_then_given_up_on(self):
        self.open_launcher(ready=False)
        self.run_wrapper("-uid", "x", login=True, BNET_STEAM_WAIT="1")
        self.assertIn("not logged in after 1s", self.log())
        self.assertEqual(len(self.games()), 1)

    def test_a_process_that_only_mentions_the_launcher_name_is_not_the_launcher(self):
        """e.g. a shell whose command line contains 'Battle.net.exe': must not count."""
        for pid, cmd in ((7, b"bash -c echo Battle.net.exe\0"),
                         (8, b"/usr/bin/proton run /x/Battle.net/Battle.net.exe\0"),
                         (9, b"C:\\Program Files (x86)\\Other\\Battle.net.exe\0")):
            (self.proc / str(pid)).mkdir()
            (self.proc / str(pid) / "cmdline").write_bytes(cmd)
        self.run_wrapper("-uid", "x", login=True)
        self.assertIn("start-launcher", self.rec())                   # it did not think one was up

    # --- robustness ----------------------------------------------------------------------

    def test_games_exit_code_is_passed_through_and_cleanup_still_happens(self):
        r = self.run_wrapper("-uid", "x", login=True, FAKE_RC="7")
        self.assertEqual(r.returncode, 7)
        self.assertIn("taskkill", self.rec())

    def test_exit_code_passes_through_on_the_plain_path_too(self):
        self.assertEqual(self.run_wrapper("-uid", "x", FAKE_RC="5").returncode, 5)

    def test_arguments_with_spaces_survive_intact(self):
        self.run_wrapper("-name", "two words", "-x", login=True)
        self.assertIn("[two words]", " ".join(self.rec()))

    def test_missing_launcher_exe_still_runs_the_game(self):
        self.bnet.unlink()
        r = self.run_wrapper("-uid", "x", login=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("start-launcher", self.rec())
        self.assertEqual(len(self.games()), 1)
        self.assertIn("cannot start the launcher", self.log())

    def test_without_a_compat_path_the_game_just_runs(self):
        r = self.run_wrapper("-uid", "x", login=True, STEAM_COMPAT_DATA_PATH=None)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("start-launcher", self.rec())


class WrapperFile(unittest.TestCase):
    def test_flags_that_need_the_launcher(self):
        self.assertTrue(wrapper.needs_launcher("-launcherlogin -uid wow_classic_era -initialgamemode=cera"))
        self.assertTrue(wrapper.needs_launcher("-nostreamline -sso -launch -uid fenris"))
        self.assertFalse(wrapper.needs_launcher("-launch -uid s1"))
        self.assertFalse(wrapper.needs_launcher("-uid w2"))
        self.assertFalse(wrapper.needs_launcher(""))

    def test_ensure_writes_an_executable_script_and_refreshes_a_stale_one(self):
        fs = FakeSteam()
        self.addCleanup(fs.cleanup)
        p = wrapper.ensure(fs.env)
        self.assertEqual(p.name, wrapper.SCRIPT_NAME)
        self.assertEqual(p.read_text(), wrapper.SCRIPT)
        self.assertTrue(p.stat().st_mode & stat.S_IXUSR)
        p.write_text("old version")
        wrapper.ensure(fs.env)
        self.assertEqual(p.read_text(), wrapper.SCRIPT)


if __name__ == "__main__":
    unittest.main()
