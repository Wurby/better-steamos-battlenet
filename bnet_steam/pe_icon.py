"""Extract an executable's main icon from its PE resources, with no external tools.

A Windows exe stores icons as RT_ICON images plus an RT_GROUP_ICON directory listing them.
We take the first group, pick its largest image, and return it as PNG (Vista-style icons
embed PNG data directly) or, for old-style bitmap icons, as a single-image .ico for ffmpeg.
"""
import mmap
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from .errors import BnetSteamError

RT_ICON, RT_GROUP_ICON = 3, 14
PNG_SIG = b"\x89PNG\r\n\x1a\n"


class PeError(BnetSteamError):
    pass


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


class _Pe:
    def __init__(self, data):
        if data[:2] != b"MZ":
            raise PeError("not a Windows executable")
        pe = _u32(data, 0x3C)
        if data[pe:pe + 4] != b"PE\0\0":
            raise PeError("not a PE file")
        nsec, optsize = _u16(data, pe + 6), _u16(data, pe + 20)
        opt = pe + 24
        magic = _u16(data, opt)
        if magic not in (0x10B, 0x20B):
            raise PeError("unknown PE optional header")
        dd = opt + (96 if magic == 0x10B else 112)        # data directories
        self.rsrc_rva = _u32(data, dd + 16)                # directory index 2 = resources
        sec = opt + optsize
        self.sections = [(_u32(data, s + 12), _u32(data, s + 8), _u32(data, s + 16),
                          _u32(data, s + 20)) for s in (sec + 40 * i for i in range(nsec))]

    def offset(self, rva):
        for va, vsize, rawsize, rawptr in self.sections:
            if va <= rva < va + max(vsize, rawsize):
                return rawptr + rva - va
        raise PeError("resource points outside the file")


def _entries(data, base, off):
    """[(id, target offset, is_dir)] of the ID-keyed entries in a resource directory."""
    named, ids = _u16(data, base + off + 12), _u16(data, base + off + 14)
    out = []
    for i in range(named, named + ids):                    # named entries come first; skip
        e = base + off + 16 + i * 8
        tgt = _u32(data, e + 4)
        out.append((_u32(data, e), tgt & 0x7FFFFFFF, bool(tgt & 0x80000000)))
    return out


def _resources(data, pe, rtype):
    """{resource id: bytes} for one resource type (first language of each)."""
    if not pe.rsrc_rva:
        return {}
    base = pe.offset(pe.rsrc_rva)
    out = {}
    for tid, toff, tdir in _entries(data, base, 0):
        if tid != rtype or not tdir:
            continue
        for rid, noff, ndir in _entries(data, base, toff):
            langs = _entries(data, base, noff) if ndir else []
            if not langs or langs[0][2]:
                continue
            leaf = base + langs[0][1]
            o = pe.offset(_u32(data, leaf))
            out[rid] = bytes(data[o:o + _u32(data, leaf + 4)])
    return out


def best_icon(data):
    """('png' | 'ico', bytes) for the largest image of the first icon group."""
    pe = _Pe(data)
    groups, images = _resources(data, pe, RT_GROUP_ICON), _resources(data, pe, RT_ICON)
    if not groups:
        raise PeError("no icon in this executable")
    grp = groups[min(groups)]
    cands = []
    for i in range(_u16(grp, 4)):
        w, h, colors, _, planes, bits, size, nid = struct.unpack_from("<BBBBHHIH", grp, 6 + i * 14)
        if nid in images:
            cands.append(((w or 256) * (h or 256), bits, (w, h, colors, planes, bits), images[nid]))
    if not cands:
        raise PeError("icon group references missing images")
    _, _, (w, h, colors, planes, bits), img = max(cands, key=lambda c: c[:2])
    if img.startswith(PNG_SIG):
        return "png", img
    ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w, h, colors, 0, planes, bits,
                                                     len(img), 22) + img
    return "ico", ico


def write_icon_png(exe, dest):
    """Write the exe's main icon to `dest` as a PNG."""
    exe, dest = Path(exe), Path(dest)
    try:
        with open(exe, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            kind, blob = best_icon(data)
    except (OSError, ValueError, IndexError, struct.error) as e:
        raise PeError(f"could not read an icon from {exe.name} ({e})") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "png":
        dest.write_bytes(blob)
        return
    if not shutil.which("ffmpeg"):
        raise PeError("ffmpeg is needed to convert this icon")
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "icon.ico"
        src.write_bytes(blob)
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
                            str(dest)], capture_output=True)
    if r.returncode != 0 or not dest.exists():
        raise PeError("ffmpeg could not convert the icon")
