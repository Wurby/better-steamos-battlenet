import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import art

from .helpers import FakeSteam

GAME = {"code": "s1", "name": "StarCraft", "box_art": "http://x/box.webp",
        "logo_art": "http://x/logo.webp"}


class ExistingArt(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSteam()
        self.addCleanup(self.fs.cleanup)
        self.grid = self.fs.env.grid_dir
        self.grid.mkdir(parents=True)

    def test_any_format_counts_as_existing(self):
        for name in ("9p.png", "9.webp", "9_hero.jpeg", "9_icon.ico"):
            (self.grid / name).write_text("x")
        with mock.patch.object(art, "_fetch") as fetch, mock.patch.object(art, "_compose") as comp:
            icon, written = art.ensure_art(self.fs.env, 9, GAME)
        self.assertEqual((Path(icon).name, written), ("9_icon.ico", []))
        fetch.assert_not_called()
        comp.assert_not_called()

    def test_dry_run_reports_but_writes_nothing(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(art, "_fetch") as fetch:
            icon, written = art.ensure_art(self.fs.env, 9, GAME, dry_run=True)
        self.assertEqual(written, [])
        fetch.assert_not_called()
        self.assertEqual(list(self.grid.iterdir()), [])

    def test_without_ffmpeg_returns_existing_icon_or_nothing(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(art.ensure_art(self.fs.env, 9, GAME), ("", []))
            (self.grid / "9_icon.png").write_text("x")
            self.assertEqual(Path(art.ensure_art(self.fs.env, 9, GAME)[0]).name, "9_icon.png")

    def test_only_missing_sizes_are_created(self):
        (self.grid / "9p.png").write_text("user art")
        made = []
        with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(art, "_fetch", return_value=True), \
                mock.patch.object(art, "_compose", side_effect=lambda s, d, *a: made.append(d.name)), \
                mock.patch("subprocess.run"):
            art.ensure_art(self.fs.env, 9, GAME)
        self.assertEqual(sorted(made), ["9.jpg", "9_hero.jpg"])
        self.assertEqual((self.grid / "9p.png").read_text(), "user art")


if __name__ == "__main__":
    unittest.main()
