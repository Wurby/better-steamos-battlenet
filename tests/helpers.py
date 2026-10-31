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
                proton_log=None, seconds=1, file=None, latest=False, on_desktop=False)
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
