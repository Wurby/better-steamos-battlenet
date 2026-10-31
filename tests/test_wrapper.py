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
launcher_up() { grep -aqiz '^C:\\Program Files (x86)\\Battle\.net\\Battle\.net\.exe' "$BNET_STEAM_PROC"/[0-9]*/cmdline 2>/dev/null; }
record_game() { printf '%s' "$1" >>"$REC"; shift; printf ' [%s]' "$@" >>"$REC"; echo >>"$REC"; }
case "$1" in
  run) shift; record_game game-via-run "$@"; exit "${FAKE_RC:-0}" ;;
  waitforexitandrun)
    shift
    if launcher_up; then echo "BLOCKED: waitforexitandrun while the launcher is up" >>"$REC"; exit 99; fi
    record_game game-via-waitforexitandrun "$@"; exit "${FAKE_RC:-0}" ;;
esac
'''


class WrapperBase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.t = Path(self._t.name)
        self.fake, self.proc = self.t / "fake", self.t / "proc"
        for d in (self.fake, self.proc):
            d.mkdir()
        self.proton = self.t / "Proton 11.0" / "proton"
        self.proton.parent.mkdir()
        self.proton.write_text(FAKE_PROTON)
        self.proton.chmod(0o755)
        self.script = self.t / "run-game.sh"
        self.script.write_text(wrapper.SCRIPT)
        self.script.chmod(0o755)
        self.game = self.t / "StarCraft.exe"

    def run_wrapper(self, *game_args, **env):
        e = {**os.environ, "FAKE_DIR": str(self.fake), "BNET_STEAM_PROC": str(self.proc), **env}
        cmd = [str(self.script), str(self.proton), "waitforexitandrun", str(self.game), *game_args]
        return subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=60)

    def rec(self):
        p = self.fake / "rec"
        return p.read_text().splitlines() if p.exists() else []

    def open_launcher(self):
        (self.proc / "42").mkdir()
        (self.proc / "42/cmdline").write_bytes(LAUNCHER_CMD)


class WrapperScript(WrapperBase):
    def test_with_no_launcher_the_command_runs_unchanged_and_nothing_is_logged(self):
        r = self.run_wrapper("-launch", "-uid", "s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rec(), [f"game-via-waitforexitandrun [{self.game}] [-launch] [-uid] [s1]"])
        self.assertFalse((self.t / "logs").exists())

    def test_an_open_launcher_would_block_waitforexitandrun_so_run_is_used(self):
        self.open_launcher()
        r = self.run_wrapper("-launch", "-uid", "s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rec(), [f"game-via-run [{self.game}] [-launch] [-uid] [s1]"])
        self.assertTrue((self.proc / "42").exists())                 # the user's launcher is left alone
        self.assertIn("using 'run'", (self.t / "logs/run-game.log").read_text())

    def test_a_process_that_only_mentions_the_launcher_name_is_not_the_launcher(self):
        """e.g. a shell whose command line contains 'Battle.net.exe': must not count."""
        for pid, cmd in ((7, b"bash -c echo Battle.net.exe\0"),
                         (8, b"/usr/bin/proton run /x/Battle.net/Battle.net.exe\0"),
                         (9, b"C:\\Program Files (x86)\\Other\\Battle.net.exe\0"),
                         # contains the launcher's FULL path, but not as the start of an argument
                         (10, b"bash -c echo C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\0")):
            (self.proc / str(pid)).mkdir()
            (self.proc / str(pid) / "cmdline").write_bytes(cmd)
        self.run_wrapper("-uid", "x")
        self.assertTrue(self.rec()[0].startswith("game-via-waitforexitandrun"))

    def test_the_launcher_may_be_any_one_of_many_processes(self):
        for pid in range(100, 140):
            (self.proc / str(pid)).mkdir()
            (self.proc / str(pid) / "cmdline").write_bytes(b"/usr/bin/something\0--flag\0")
        self.open_launcher()
        self.run_wrapper("-uid", "x")
        self.assertTrue(self.rec()[0].startswith("game-via-run"))

    def test_exit_code_passes_through_on_both_paths(self):
        self.assertEqual(self.run_wrapper("-uid", "x", FAKE_RC="5").returncode, 5)
        self.open_launcher()
        self.assertEqual(self.run_wrapper("-uid", "x", FAKE_RC="6").returncode, 6)

    def test_arguments_with_spaces_survive_intact_on_both_paths(self):
        self.run_wrapper("-name", "two words", "-x")
        self.open_launcher()
        self.run_wrapper("-name", "two words", "-x")
        self.assertEqual(sum("[two words]" in x for x in self.rec()), 2)

    def test_only_the_verb_is_changed_not_game_arguments_that_look_like_it(self):
        self.open_launcher()
        self.run_wrapper("-mode", "waitforexitandrun")
        self.assertEqual(self.rec(), [f"game-via-run [{self.game}] [-mode] [waitforexitandrun]"])


class LauncherFix(WrapperBase):
    """--launcher-fix redirects a launcher start through launcher.bat."""

    def setUp(self):
        super().setUp()
        self.compat = self.t / "compat"
        self.cmd = self.compat / "pfx/drive_c/windows/system32/cmd.exe"
        self.bat = self.compat / "pfx/drive_c/bnet-steam/launcher.bat"
        for f in (self.cmd, self.bat):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("")
        self.bnet = self.compat / "pfx/drive_c/Program Files (x86)/Battle.net/Battle.net.exe"

    def run_fix(self, *args, fix=True, **env):
        e = {**os.environ, "FAKE_DIR": str(self.fake), "BNET_STEAM_PROC": str(self.proc),
             "STEAM_COMPAT_DATA_PATH": str(self.compat), **env}
        e = {k: v for k, v in e.items() if v is not None}
        cmd = [str(self.script)] + (["--launcher-fix"] if fix else []) + [
            str(self.proton), "waitforexitandrun", str(self.bnet), *args]
        return subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=60)

    def test_the_launcher_exe_is_replaced_by_cmd_running_the_batch_file_with_the_arguments(self):
        r = self.run_fix("--exec=launch", "WoWC")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rec(), [f"game-via-waitforexitandrun [{self.cmd}] [/c] [C:\\bnet-steam\\launcher.bat] "
                                      "[--exec=launch] [WoWC]"])
        self.assertIn("generic board", (self.t / "logs/run-game.log").read_text())

    def test_without_the_flag_the_command_is_untouched(self):
        self.run_fix("--exec=launch", "WoWC", fix=False)
        self.assertEqual(self.rec(), [f"game-via-waitforexitandrun [{self.bnet}] [--exec=launch] [WoWC]"])

    def test_an_open_launcher_also_gets_the_verb_swap_alongside_the_fix(self):
        self.open_launcher()
        self.run_fix("--exec=launch", "WoWC")
        self.assertEqual(len(self.rec()), 1)
        self.assertTrue(self.rec()[0].startswith(f"game-via-run [{self.cmd}] [/c]"))

    def test_missing_batch_file_or_cmd_falls_back_to_starting_the_launcher_normally(self):
        self.bat.unlink()
        self.run_fix("-x")
        self.assertEqual(self.rec(), [f"game-via-waitforexitandrun [{self.bnet}] [-x]"])
        self.assertIn("not applied", (self.t / "logs/run-game.log").read_text())

    def test_only_a_launcher_exe_argument_is_rewritten(self):
        r = subprocess.run([str(self.script), "--launcher-fix", str(self.proton), "waitforexitandrun",
                            str(self.t / "StarCraft.exe"), "-uid", "s1"],
                           env={**os.environ, "FAKE_DIR": str(self.fake), "BNET_STEAM_PROC": str(self.proc),
                                "STEAM_COMPAT_DATA_PATH": str(self.compat)},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rec(), [f"game-via-waitforexitandrun [{self.t / 'StarCraft.exe'}] [-uid] [s1]"])

    def test_no_compat_path_means_no_rewrite_and_no_crash(self):
        r = self.run_fix("-x", STEAM_COMPAT_DATA_PATH=None)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.rec(), [f"game-via-waitforexitandrun [{self.bnet}] [-x]"])


class WrapperFile(unittest.TestCase):
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

    def test_the_plain_path_spawns_almost_nothing(self):
        """Under Steam's LD_PRELOAD every spawned process is slow; the no-launcher path must
        stay a single grep, not a per-process loop."""
        self.assertNotIn("for c in", wrapper.SCRIPT)
        self.assertNotIn("$(date", wrapper.SCRIPT)


if __name__ == "__main__":
    unittest.main()
