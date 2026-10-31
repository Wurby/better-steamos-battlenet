import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"


def make_tarball(dest, marker=None):
    """Like GitHub's archive: everything under one top-level directory."""
    with tarfile.open(dest, "w:gz") as tar:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if ".git" in rel.parts or "__pycache__" in rel.parts or path.is_dir():
                continue
            tar.add(path, arcname=f"better-steamos-battlenet-main/{rel}")
        if marker:
            m = Path(dest).parent / "marker.txt"
            m.write_text(marker)
            tar.add(m, arcname="better-steamos-battlenet-main/MARKER.txt")


class InstallScript(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.t = Path(self._t.name)
        self.home = self.t / "home"
        (self.home / ".local/share/Steam/userdata/4242/config").mkdir(parents=True)
        (self.home / ".config").mkdir()
        (self.home / ".config/user-dirs.dirs").write_text(f'XDG_DESKTOP_DIR="{self.home}/Desktop"\n')
        self.tar = self.t / "src.tar.gz"
        make_tarball(self.tar)
        self.data = self.home / ".local/share/bnet-steam"

    def run_install(self, cwd=None, **env):
        e = {k: v for k, v in os.environ.items()
             if k not in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "BNET_STEAM_DATA")}
        e.update(HOME=str(self.home), BNET_STEAM_URL=f"file://{self.tar}")
        e.update(env)
        e = {k: v for k, v in e.items() if v is not None}
        return subprocess.run(["bash", str(INSTALL)], env=e, capture_output=True, text=True,
                              timeout=120, cwd=cwd)

    def test_a_fresh_install_gives_an_app_a_command_and_desktop_icons(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.data / "app/bnet_steam/__init__.py").exists())
        cmd = self.home / ".local/bin/bnet-steam"
        self.assertTrue(os.access(cmd, os.X_OK))
        self.assertTrue((self.home / "Desktop/Battle.net for Steam.desktop").exists())
        self.assertTrue((self.home / ".local/share/applications/bnet-steam.desktop").exists())
        self.assertIn("Battle.net for Steam", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_the_desktop_icon_runs_the_installed_copy_in_a_terminal(self):
        self.run_install()
        entry = (self.home / "Desktop/Battle.net for Steam.desktop").read_text()
        self.assertIn("Terminal=true", entry)
        exec_line = next(x for x in entry.splitlines() if x.startswith("Exec="))
        script = Path(exec_line[len("Exec="):].strip('"'))
        self.assertTrue(script.exists() and os.access(script, os.X_OK))
        self.assertIn(str(self.data / "app"), script.read_text())      # runs what was installed
        self.assertNotIn(str(ROOT), script.read_text())                # never the checkout
        self.assertIn("wizard", script.read_text())

    def test_the_installed_command_works(self):
        self.run_install()
        r = subprocess.run([str(self.home / ".local/bin/bnet-steam"), "--version"],
                           env={**os.environ, "HOME": str(self.home)}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bnet-steam", r.stdout)

    def test_a_folder_that_happens_to_contain_a_bnet_steam_dir_cannot_hijack_it(self):
        """`python -m` puts the current directory first; the installed copy must still win."""
        evil = self.t / "elsewhere"
        (evil / "bnet_steam").mkdir(parents=True)
        (evil / "bnet_steam/__init__.py").write_text("raise SystemExit('wrong copy')")
        (evil / "bnet_steam/cli.py").write_text("def main(): raise SystemExit('wrong copy')")
        r = self.run_install(cwd=evil)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("wrong copy", r.stdout + r.stderr)
        script = next((self.home / "Desktop").glob("*.desktop")).read_text()
        self.assertNotIn(str(evil), script)
        cmd = subprocess.run([str(self.home / ".local/bin/bnet-steam"), "--version"], cwd=evil,
                             env={**os.environ, "HOME": str(self.home)}, capture_output=True, text=True)
        self.assertEqual(cmd.returncode, 0, cmd.stderr)
        self.assertIn("bnet-steam", cmd.stdout)

    def test_running_it_again_updates_the_code_and_keeps_your_data(self):
        self.run_install()
        for rel in ("prefix/pfx/user.reg", "backups/shortcuts.vdf.bak-1", "state.json"):
            f = self.data / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("precious")
        make_tarball(self.tar, marker="v2")
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.data / "app/MARKER.txt").read_text(), "v2")
        for rel in ("prefix/pfx/user.reg", "backups/shortcuts.vdf.bak-1", "state.json"):
            self.assertEqual((self.data / rel).read_text(), "precious")

    def test_without_steam_it_says_so_in_plain_words_and_installs_nothing(self):
        import shutil
        shutil.rmtree(self.home / ".local/share/Steam")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot find Steam", r.stderr)
        self.assertIn("Desktop Mode", r.stderr)
        self.assertFalse((self.data / "app").exists())
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_flatpak_steam_gets_its_own_message(self):
        import shutil
        shutil.rmtree(self.home / ".local/share/Steam")
        (self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata").mkdir(parents=True)
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Flatpak", r.stderr)

    def test_an_old_python_is_refused_clearly(self):
        old = self.t / "python-old"
        old.write_text("#!/bin/sh\nexit 1\n")
        old.chmod(0o755)
        r = self.run_install(BNET_STEAM_PYTHON=str(old))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("3.9", r.stderr)
        self.assertFalse((self.data / "app").exists())

    def test_a_failed_download_changes_nothing_and_says_what_to_do(self):
        self.run_install()
        before = (self.data / "app/bnet_steam/cli.py").read_text()
        r = self.run_install(BNET_STEAM_URL=f"file://{self.t}/does-not-exist.tar.gz")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("internet connection", r.stderr)
        self.assertEqual((self.data / "app/bnet_steam/cli.py").read_text(), before)   # still works

    def test_a_corrupt_download_is_not_installed_over_a_good_copy(self):
        self.run_install()
        before = (self.data / "app/bnet_steam/cli.py").read_text()
        self.tar.write_bytes(b"this is not a tarball")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a valid archive", r.stderr)
        self.assertEqual((self.data / "app/bnet_steam/cli.py").read_text(), before)

    def test_an_archive_that_is_some_other_project_is_rejected(self):
        other = self.t / "other"
        (other / "proj").mkdir(parents=True)
        (other / "proj/README").write_text("hi")
        with tarfile.open(self.tar, "w:gz") as tar:
            tar.add(other / "proj/README", arcname="proj/README")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not look like this project", r.stderr)
        self.assertFalse((self.data / "app").exists())

    def test_it_never_touches_steams_own_files(self):
        steam = self.home / ".local/share/Steam/userdata/4242/config"
        (steam / "shortcuts.vdf").write_bytes(b"untouched")
        self.run_install()
        self.assertEqual((steam / "shortcuts.vdf").read_bytes(), b"untouched")


if __name__ == "__main__":
    unittest.main()
