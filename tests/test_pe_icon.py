import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import pe_icon

from .helpers import PNG, build_pe


class BestIcon(unittest.TestCase):
    def test_largest_png_image_is_chosen(self):
        pe = build_pe([(16, 16, 32, b"small"), (48, 48, 32, b"medium"), (0, 0, 32, b"large-256")])
        self.assertEqual(pe_icon.best_icon(pe), ("png", PNG + b"large-256"))

    def test_zero_size_means_256_and_beats_smaller(self):
        pe = build_pe([(255, 255, 32, b"almost"), (0, 0, 32, b"full")])
        self.assertEqual(pe_icon.best_icon(pe)[1], PNG + b"full")

    def test_higher_bit_depth_wins_at_equal_size(self):
        pe = build_pe([(32, 32, 8, b"8bit"), (32, 32, 32, b"32bit")])
        self.assertEqual(pe_icon.best_icon(pe)[1], PNG + b"32bit")

    def test_bitmap_icon_becomes_a_valid_single_image_ico(self):
        kind, ico = pe_icon.best_icon(build_pe([(32, 32, 32, b"DIBDATA")], dib=True))
        self.assertEqual(kind, "ico")
        self.assertEqual(struct.unpack_from("<HHH", ico, 0), (0, 1, 1))
        w, h, _, _, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", ico, 6)
        self.assertEqual((w, h, bits, size, offset), (32, 32, 32, 7, 22))
        self.assertEqual(ico[offset:], b"DIBDATA")

    def test_lowest_group_id_is_the_main_icon(self):
        pe = build_pe([(16, 16, 32, b"x")], group_id=7)
        self.assertEqual(pe_icon.best_icon(pe)[0], "png")

    def test_not_a_pe_or_no_icon_or_garbage_is_a_clean_error(self):
        for bad in (b"", b"hello world", b"MZ" + b"\0" * 10):
            with self.assertRaises(Exception) as cm:
                pe_icon.best_icon(bad)
            self.assertTrue(isinstance(cm.exception, (pe_icon.PeError, struct.error)))
        no_icons = build_pe([(16, 16, 32, b"x")])
        # blank the resource data directory entry so the file has no resources
        head = bytearray(no_icons)
        struct.pack_into("<I", head, 64 + 4 + 20 + 96 + 16, 0)
        with self.assertRaises(pe_icon.PeError):
            pe_icon.best_icon(bytes(head))


class WriteIconPng(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)

    def test_png_icon_is_written_verbatim_creating_parent_dirs(self):
        exe = self.d / "a.exe"
        exe.write_bytes(build_pe([(0, 0, 32, b"pixels")]))
        out = self.d / "sub/dir/icon.png"
        pe_icon.write_icon_png(exe, out)
        self.assertEqual(out.read_bytes(), PNG + b"pixels")

    def test_truncated_or_missing_files_raise_pe_error_not_a_crash(self):
        exe = self.d / "t.exe"
        exe.write_bytes(build_pe([(0, 0, 32, b"pixels")])[:700])
        with self.assertRaises(pe_icon.PeError):
            pe_icon.write_icon_png(exe, self.d / "o.png")
        with self.assertRaises(pe_icon.PeError):
            pe_icon.write_icon_png(self.d / "missing.exe", self.d / "o.png")
        self.assertFalse((self.d / "o.png").exists())

    def test_bitmap_icon_without_ffmpeg_is_a_clean_error(self):
        exe = self.d / "d.exe"
        exe.write_bytes(build_pe([(32, 32, 32, b"DIB")], dib=True))
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(pe_icon.PeError, "ffmpeg"):
                pe_icon.write_icon_png(exe, self.d / "o.png")


if __name__ == "__main__":
    unittest.main()
