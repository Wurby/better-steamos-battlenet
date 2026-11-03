"""Finding Steam, and editing its files safely.

Safety model for every write to shortcuts.vdf / config.vdf:
  1. the user confirms (or passes --yes),
  2. Steam is asked to exit and we wait for it (we never kill it),
  3. both files are backed up,
  4. files are re-read fresh (Steam rewrites them on exit), changed, validated,
     and replaced atomically,
  5. on any error the backups are put back,
  6. Steam is restarted if it was running.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import vdf
from .errors import BnetSteamError

ID64_BASE = 76561197960265728       # SteamID64 = this + account id
BACKUP_KEEP = 20                    # per file


@dataclass(frozen=True)
class SteamEnv:
    root: Path          # Steam install root (contains userdata/, config/, steamapps/)
    userdir: Path       # userdata/<account id>
    work: Path          # our own data dir (prefix, art cache, backups, state)

    @property
    def shortcuts_path(self):
        return self.userdir / "config" / "shortcuts.vdf"

    @property
    def config_path(self):
        return self.root / "config" / "config.vdf"

    @property
    def grid_dir(self):
        return self.userdir / "config" / "grid"

    @property
    def backups_dir(self):
        return self.work / "backups"


# --- discovery ----------------------------------------------------------------

def find_root(explicit=None):
    home = Path.home()
    if explicit:
        root = Path(explicit).expanduser()
        if not (root / "userdata").is_dir():
            raise BnetSteamError(f"{root} does not look like a Steam install (no userdata/)")
        return root
    for c in (home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root"):
        if (c / "userdata").is_dir():
            return c.resolve()
    if (home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata").is_dir():
        raise BnetSteamError(
            "Only Flatpak Steam was found. It is not supported yet (the Flatpak sandbox "
            "cannot see our Wine prefix). Use the native Steam package or pass --steam-root.")
    raise BnetSteamError("Could not find a Steam install. Pass --steam-root.")


def most_recent_account(root):
    """Account id of the last Steam user to log in, from loginusers.vdf, or None."""
    try:
        text = (root / "config/loginusers.vdf").read_text(errors="replace")
    except OSError:
        return None
    for m in re.finditer(r'"(\d{17})"\s*\{(.*?)\n\s*\}', text, re.S):
        if re.search(r'"MostRecent"\s*"1"', m.group(2)):
            return int(m.group(1)) - ID64_BASE
    return None


def find_userdir(root, explicit=None, ours=None, hint=None):
    """The Steam account folder to work in. `ours(shortcuts)` says whether a shortcuts dict holds
    our launcher shortcut; `hint` is the account id we used last. Guessing the account from
    login order alone can pick a different one than the one we installed into (several
    accounts on one device), which makes an existing shortcut look missing."""
    ud = root / "userdata"
    if explicit:
        p = ud / str(explicit)
        if not p.is_dir():
            raise BnetSteamError(f"no Steam user {explicit} under {ud}")
        return p
    users = [p for p in ud.iterdir() if p.name.isdigit() and p.name != "0"]
    if not users:
        raise BnetSteamError(f"no Steam user found under {ud}; log in to Steam once first")
    if len(users) == 1:
        return users[0]
    if ours:
        holding = [p for p in users if _holds(p, ours)]
        if len(holding) == 1:
            return holding[0]
        if holding:
            users = holding
    if hint is not None and (ud / str(hint)) in users:
        return ud / str(hint)
    recent = most_recent_account(root)
    if recent is not None and (ud / str(recent)) in users:
        return ud / str(recent)

    def mtime(p):
        f = p / "config/shortcuts.vdf"
        return f.stat().st_mtime if f.exists() else p.stat().st_mtime
    return max(users, key=mtime)


def _holds(userdir, ours):
    f = userdir / "config" / "shortcuts.vdf"
    try:
        return bool(ours(vdf.loads(f.read_bytes()).get("shortcuts", {})))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def default_work_dir():
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "bnet-steam"


def discover(steam_root=None, steam_user=None, data_dir=None, ours=None):
    root = find_root(steam_root)
    work = Path(data_dir).expanduser() if data_dir else default_work_dir()
    try:
        hint = json.loads((work / "state.json").read_text()).get("userdir")
    except (OSError, ValueError, AttributeError):
        hint = None
    return SteamEnv(root, find_userdir(root, steam_user, ours, hint), work)


# --- Proton -------------------------------------------------------------------

def proton_tool_name(folder):
    """Steam's internal compat-tool name for an official Proton folder name.
    Verified for 'Proton 11.0' (proton_11) and 'Proton 9.0 (Beta)' (proton_9)
    against Steam's own config; the rest follow the same pattern."""
    m = re.match(r"Proton (\d+)\.", folder)
    if m:
        return f"proton_{m.group(1)}"
    return {"Proton - Experimental": "proton_experimental",
            "Proton Hotfix": "proton_hotfix"}.get(folder)


def installed_protons(root):
    """[(tool name, source dir)] of official and custom (compatibilitytools.d) Protons."""
    found = []
    common = root / "steamapps/common"
    if common.is_dir():
        for d in sorted(common.glob("Proton*")):
            name = proton_tool_name(d.name)
            if name and (d / "proton").exists():
                found.append((name, d))
    custom = root / "compatibilitytools.d"
    if custom.is_dir():
        for d in sorted(custom.iterdir()):
            if (d / "proton").exists():
                found.append((d.name, d))
    return found


def pick_proton(root, explicit=None):
    """Compat tool name to use: the explicit one, else the newest official Proton."""
    if explicit:
        return explicit
    tools = installed_protons(root)
    numbered = sorted((int(n.split("_")[1]), n) for n, _ in tools
                      if re.fullmatch(r"proton_\d+", n))
    if numbered:
        return numbered[-1][1]
    for want in ("proton_experimental", "proton_hotfix"):
        if any(n == want for n, _ in tools):
            return want
    raise BnetSteamError("no Proton installed; install one in Steam (Settings > Compatibility) "
                         "or pass --proton NAME")


# --- Steam process --------------------------------------------------------------

def steam_running():
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.read_text().strip() == "steam":
                return True
        except OSError:
            pass
    return False


def stop_steam(timeout=90):
    """Ask Steam to shut down and wait. Never force-kills."""
    try:
        subprocess.run(["steam", "-shutdown"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not steam_running():
            return
        time.sleep(1)
    raise BnetSteamError(f"Steam did not exit within {timeout}s. Nothing was changed. "
                         "Close Steam yourself (Steam menu > Exit) and run this again.")


def start_steam():
    # not attached to our terminal's keyboard, so it can never swallow what the person types
    subprocess.Popen(["steam"], start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- shortcuts ------------------------------------------------------------------

def shortcut_appid(exe, name):
    """Steam's id for a non-Steam shortcut: crc32(exe + name) | 1<<31.
    Shortcuts created through Steam's own UI use a different scheme, so for
    existing entries always read the stored appid instead of recomputing."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def steam_gameid(appid):
    """64-bit id for steam://rungameid/ of a non-Steam shortcut."""
    return (appid << 32) | 0x02000000


class Entry(dict):
    """A shortcut whose field names match case-insensitively (Steam has written both `exe` and
    `Exe`, `appname` and `AppName`). Reads and writes keep whatever spelling the file has, so
    saving never renames a key Steam wrote."""

    def _k(self, key):
        if isinstance(key, str) and not dict.__contains__(self, key):
            low = key.lower()
            for k in dict.keys(self):
                if isinstance(k, str) and k.lower() == low:
                    return k
        return key

    def __getitem__(self, key): return dict.__getitem__(self, self._k(key))
    def __setitem__(self, key, v): dict.__setitem__(self, self._k(key), v)
    def __delitem__(self, key): dict.__delitem__(self, self._k(key))
    def __contains__(self, key): return dict.__contains__(self, self._k(key))
    def get(self, key, default=None): return dict.get(self, self._k(key), default)
    def pop(self, key, *a): return dict.pop(self, self._k(key), *a)
    def setdefault(self, key, default=None): return dict.setdefault(self, self._k(key), default)


def load_shortcuts(env, strict=False):
    """Parsed shortcuts.vdf. With strict=True, refuse a file our codec can't
    reproduce byte for byte (so we never write back something altered)."""
    p = env.shortcuts_path
    if not p.exists():
        return {"shortcuts": {}}
    raw = p.read_bytes()
    data = vdf.loads(raw)
    if strict and vdf.dumps(data) != raw:
        raise BnetSteamError(f"{p} does not round-trip through our VDF codec; refusing to "
                             "modify it. Please report this with a copy of the file.")
    data.setdefault("shortcuts", {})
    for k, e in data["shortcuts"].items():
        if isinstance(e, dict):
            data["shortcuts"][k] = Entry(e)
    return data


def new_shortcut(appid, name, exe, launch_options, start_dir=None, icon=""):
    exe = f'"{exe}"'
    return {
        "appid": appid, "appname": name, "exe": exe,
        "StartDir": start_dir or str(Path(exe.strip('"')).parent) + "/",
        "icon": icon, "ShortcutPath": "", "LaunchOptions": launch_options,
        "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0,
        "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0,
        "LastPlayTime": 0, "FlatpakAppID": "", "sortas": "", "tags": {},
    }


def by_appid(shortcuts):
    return {e["appid"]: e for e in shortcuts.values()}


def next_key(shortcuts):
    return str(max((int(k) for k in shortcuts if k.isdigit()), default=-1) + 1)


# --- backups --------------------------------------------------------------------

def backup(env, path):
    env.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = env.backups_dir / f"{path.name}.bak-{stamp}"
    n = 1
    while dest.exists():
        dest = env.backups_dir / f"{path.name}.bak-{stamp}-{n}"
        n += 1
    shutil.copyfile(path, dest)         # not copy2: mtime must be the backup time
    old = list_backups(env, path.name)[BACKUP_KEEP:]
    for b in old:                       # only ever prunes our own backup copies
        if b.parent == env.backups_dir:
            b.unlink()
    return dest


def list_backups(env, name):
    """Backups of `name` (e.g. 'shortcuts.vdf'), newest first."""
    found = []
    for d in (env.backups_dir, env.work):
        if d.is_dir():
            found += [p for p in d.glob(f"{name}.bak-*") if p.is_file()]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


# --- the write session ------------------------------------------------------------

def confirm(prompt, yes):
    if yes:
        return
    if not sys.stdin.isatty():
        raise BnetSteamError("refusing to modify Steam files without confirmation; "
                             "re-run with --yes")
    if input(f"{prompt} [y/N] ").strip().lower() not in ("y", "yes"):
        raise BnetSteamError("cancelled; nothing was changed")


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".bnet-steam.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Session:
    """Handle given to code inside write_session; only validated writes."""

    def __init__(self, env):
        self.env = env
        self.backups = {}           # target path -> backup path

    def snapshot(self):
        for p in (self.env.shortcuts_path, self.env.config_path):
            if p.exists():
                self.backups[p] = backup(self.env, p)

    def load_shortcuts(self):
        return load_shortcuts(self.env, strict=True)

    def read_config(self):
        return self.env.config_path.read_text()

    def save_shortcuts(self, data):
        raw = vdf.dumps(data)
        if vdf.loads(raw) != data:
            raise BnetSteamError("internal error: shortcuts did not survive serialisation")
        write_atomic(self.env.shortcuts_path, raw)

    def save_config(self, text):
        if not vdf.braces_balanced(text) or "CompatToolMapping" not in text:
            raise BnetSteamError("internal error: refusing to write an invalid config.vdf")
        write_atomic(self.env.config_path, text.encode("utf-8"))

    def restore_file(self, target, backup_path):
        raw = Path(backup_path).read_bytes()
        if target == self.env.shortcuts_path and not vdf.roundtrips(raw):
            raise BnetSteamError(f"{backup_path} is not a valid shortcuts.vdf")
        if target == self.env.config_path and not vdf.braces_balanced(raw.decode("utf-8", "replace")):
            raise BnetSteamError(f"{backup_path} is not a valid config.vdf")
        write_atomic(target, raw)

    def rollback(self):
        for target, b in self.backups.items():
            try:
                shutil.copy2(b, target)
            except OSError as e:
                print(f"WARNING: could not restore {target} from {b}: {e}", file=sys.stderr)
        if self.backups:
            print("An error occurred; your Steam files were restored from backup.",
                  file=sys.stderr)


@contextmanager
def write_session(env, what, yes=False, start=False):
    """Everything that edits Steam's files goes through here. See module docstring."""
    confirm(f"{what}\nThis will close Steam if it is running, back up your shortcut and "
            "config files, then restart Steam. Continue?", yes)
    env.work.mkdir(parents=True, exist_ok=True)
    lock = open(env.work / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise BnetSteamError("another bnet-steam is already running") from None
    was_running = steam_running()
    restart = False             # only restart Steam once we know it is really closed
    try:
        if was_running:
            print("Closing Steam...")
            stop_steam()
        restart = was_running or start
        sess = Session(env)
        try:
            sess.snapshot()
            for p in sess.backups.values():
                print(f"backup: {p}")
            yield sess
        except BaseException:
            sess.rollback()
            raise
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        if restart:
            start_steam()
            print("Steam restarted")
