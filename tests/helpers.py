"""Build a throwaway fake Steam install + Battle.net prefix for tests."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from bnet_steam import battlenet as bn
from bnet_steam import steam, vdf

CONFIG_VDF = '''"InstallConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t"CompatToolMapping"
\t\t\t\t{
\t\t\t\t\t"280220"
\t\t\t\t\t{
\t\t\t\t\t\t"name"\t\t"proton_experimental"
\t\t\t\t\t\t"config"\t\t""
\t\t\t\t\t\t"priority"\t\t"250"
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
'''


def varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def ld(no, payload):
    return varint(no << 3 | 2) + varint(len(payload)) + payload


def product(uid, code, path):
    return ld(1, ld(1, uid.encode()) + ld(2, code.encode()) + ld(3, ld(1, path.encode())))


def args(**kw):
    """argparse-style namespace with safe defaults for every command."""
    base = dict(dry_run=False, yes=True, start_steam=False, no_art=True, proton=None,
                proton_log=None, seconds=1, file=None, latest=False, on_desktop=False,
                learn=None, game=None, prune=None, handheld_fix=None)
    base.update(kw)
    return SimpleNamespace(**base)


class FakeSteam:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        root = self.tmp / "Steam"
        (root / "config").mkdir(parents=True)
        (root / "config/config.vdf").write_text(CONFIG_VDF)
        (root / "steamapps/common/Proton 11.0").mkdir(parents=True)
        (root / "steamapps/common/Proton 11.0/proton").write_text("")
        userdir = root / "userdata/4242"
        (userdir / "config").mkdir(parents=True)
        self.env = steam.SteamEnv(root, userdir, self.tmp / "work")
        self.prefix = self.env.work / "prefix"
        self.records = []
        self.aggregate = []
        # a user's own shortcut that must never be touched
        self.other = steam.new_shortcut(111, "My Emulator", "/usr/bin/emu", "")
        # launcher shortcut already pointing at an installed launcher
        launcher = bn.launcher_exe(self.prefix)
        launcher.parent.mkdir(parents=True)
        launcher.write_text("")
        self.launcher = steam.new_shortcut(
            222, "Battle.net", str(launcher), bn.launch_options(self.prefix))
        self.write_shortcuts({"0": self.other, "1": self.launcher})
        self.flush_db()

    def write_shortcuts(self, shortcuts):
        self.env.shortcuts_path.write_bytes(vdf.dumps({"shortcuts": shortcuts}))

    def shortcuts(self):
        return vdf.loads(self.env.shortcuts_path.read_bytes())["shortcuts"]

    def add_game(self, code, uid, folder, name, exe_rel):
        gdir = self.prefix / "pfx/drive_c/Program Files (x86)" / folder
        (gdir / exe_rel).parent.mkdir(parents=True, exist_ok=True)
        (gdir / exe_rel).write_text("")
        win = f"C:/Program Files (x86)/{folder}"
        self.records.append(product(uid, code, win))
        self.aggregate.append({
            "product_id": code, "name": name, "box_art_uri": "", "logo_art_uri": "",
            "icon_path": f"{win}/{exe_rel}"})
        self.flush_db()

    def flush_db(self):
        agent = self.prefix / "pfx/drive_c/ProgramData/Battle.net/Agent"
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "product.db").write_bytes(
            product("agent", "agent", "C:/ProgramData/Battle.net/Agent")
            + product("battle.net", "bna", "C:/Program Files (x86)/Battle.net")
            + b"".join(self.records))
        (agent / "aggregate.json").write_text(json.dumps({"installed": self.aggregate}))

    def cleanup(self):
        self._tmp.cleanup()


PNG = b"\x89PNG\r\n\x1a\n"


def build_pe(images, group_id=1, dib=False):
    """A minimal PE32 file whose resources hold one icon group of `images`
    [(width, height, bits, payload)] (width/height 0 = 256). PNG-compressed unless dib."""
    import struct
    tree = {3: {}, 14: {}}
    entries = b""
    for i, (w, h, bits, payload) in enumerate(images, start=1):
        data = payload if dib else PNG + payload
        tree[3][i] = data
        entries += struct.pack("<BBBBHHIH", w, h, 0, 0, 1, bits, len(data), i)
    tree[14][group_id] = struct.pack("<HHH", 0, 1, len(images)) + entries

    def d(n):               # resource directory header with n ID entries
        return struct.pack("<IIHHHH", 0, 0, 4, 0, 0, n)

    types = sorted(tree)
    off = 16 + 8 * len(types)
    type_off, lang_off, data_off = {}, {}, {}
    for t in types:
        type_off[t] = off
        off += 16 + 8 * len(tree[t])
    for t in types:
        for n in sorted(tree[t]):
            lang_off[t, n] = off
            off += 24
    for t in types:
        for n in sorted(tree[t]):
            data_off[t, n] = off
            off += 16
    blobs, blob_off = b"", {}
    for t in types:
        for n in sorted(tree[t]):
            blob_off[t, n] = off + len(blobs)
            blobs += tree[t][n] + b"\0" * (-len(tree[t][n]) % 4)
    rva = 0x1000
    rsrc = d(len(types)) + b"".join(struct.pack("<II", t, 0x80000000 | type_off[t]) for t in types)
    for t in types:
        rsrc += d(len(tree[t])) + b"".join(
            struct.pack("<II", n, 0x80000000 | lang_off[t, n]) for n in sorted(tree[t]))
    for t in types:
        for n in sorted(tree[t]):
            rsrc += d(1) + struct.pack("<II", 0x409, data_off[t, n])
    for t in types:
        for n in sorted(tree[t]):
            rsrc += struct.pack("<IIII", rva + blob_off[t, n], len(tree[t][n]), 0, 0)
    rsrc += blobs
    dd = bytearray(128)
    struct.pack_into("<II", dd, 16, rva, len(rsrc))
    opt = struct.pack("<H", 0x10B) + b"\0" * 94 + bytes(dd)
    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, len(opt), 0x102)
    sec = b".rsrc\0\0\0" + struct.pack("<IIII", len(rsrc), rva, len(rsrc), 0x400) + b"\0" * 16
    head = b"MZ" + b"\0" * 58 + struct.pack("<I", 64) + b"PE\0\0" + coff + opt + sec
    return head + b"\0" * (0x400 - len(head)) + rsrc
