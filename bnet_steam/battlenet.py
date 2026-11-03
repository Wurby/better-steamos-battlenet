"""Everything specific to Battle.net: its files, its games, how to launch them."""
import json
import os
import re
from pathlib import Path

from .errors import BnetSteamError

SHORTCUT_NAME = "Battle.net"
INSTALLER_URL = "https://downloader.battle.net/download/getInstaller?os=win&installer=Battle.net-Setup.exe"
LAUNCHER_REL = "drive_c/Program Files (x86)/Battle.net/Battle.net.exe"
PRODUCT_DB_REL = "drive_c/ProgramData/Battle.net/Agent/product.db"
AGGREGATE_REL = "drive_c/ProgramData/Battle.net/Agent/aggregate.json"
NON_GAMES = {"agent", "bna"}

# Launch specs verified by watching the launcher's own Play button. "exe" is
# relative to the install dir (None = the exe Battle.net reports); {uid} is the
# product uid, which can differ from the product code (w2 vs w2bn).
VERIFIED = {
    "s1": {"exe": "x86_64/StarCraft.exe", "args": "-launch -uid {uid}"},
    "w2bn": {"exe": None, "args": "-uid {uid}"},
}
DEFAULT_ARGS = "-uid {uid}"     # both verified launches share this form


def find_bnet_shortcut(shortcuts, appid=None):
    """Our launcher/installer shortcut. Matched by the appid we recorded when we made it
    (survives the user renaming it or Steam rewriting the exe), else by exe name
    (case-insensitive: Steam and Wine do not agree on case), else by its name."""
    entries = [e for e in shortcuts.values() if isinstance(e, dict)]
    if appid is not None:
        for e in entries:
            if e.get("appid") == appid:
                return e
    for e in entries:
        exe = os.path.basename(e.get("exe", "").strip('"')).lower()
        if exe in ("battle.net-setup.exe", "battle.net.exe"):
            return e
    for e in entries:
        if e.get("appname") == SHORTCUT_NAME and "bnet-steam" in e.get("LaunchOptions", ""):
            return e
    return None


def prefix_for(env, entry):
    """Wine prefix dir (the one holding pfx/) a shortcut runs in."""
    m = re.search(r'STEAM_COMPAT_DATA_PATH=("[^"]+"|\S+)', entry.get("LaunchOptions", ""))
    if m:
        return Path(m.group(1).strip('"'))
    return env.root / "steamapps/compatdata" / str(entry["appid"])


def in_compatdata(env, prefix):
    """True if Steam would delete this prefix when its shortcut is removed."""
    try:
        return (env.root / "steamapps/compatdata") in prefix.parents
    except ValueError:
        return False


def host_path(prefix, win_path):
    """C:/Program Files (x86)/X -> <prefix>/pfx/drive_c/Program Files (x86)/X"""
    return prefix / "pfx" / "drive_c" / win_path.replace("\\", "/").split(":/", 1)[-1]


def resolve_ci(base, rel):
    """base/rel with every component matched case-insensitively against what is on disk
    (Wine ignores case, Linux may not; Blizzard's process names differ in case from the
    files). Returns the real path, or None if it does not exist."""
    p = Path(base)
    for part in Path(rel).parts:
        try:
            names = os.listdir(p)
        except OSError:
            return None
        hit = part if part in names else next((n for n in names if n.lower() == part.lower()), None)
        if hit is None:
            return None
        p = p / hit
    return p


def art_codes(code):
    """wow_classic_era -> [wow_classic_era, wow_classic, wow]: product ids whose art may
    stand in when Battle.net has not listed the game itself yet."""
    parts = code.split("_")
    return ["_".join(parts[:i]) for i in range(len(parts), 0, -1)]


# flags in a game's start command that mean "log in through the launcher": such a game only works
# when the launcher itself starts it (it hands the game a one-time BDK_LAUNCH_GUID)
LAUNCHER_LOGIN_FLAGS = ("-launcherlogin", "-sso")


def signs_in_via_launcher(args):
    return any(t in LAUNCHER_LOGIN_FLAGS for t in args.split())


def launcher_families(prefix):
    """{uid: family id} read from the launcher's own logs, e.g. {'wow_classic_era': 'WoWC'}.
    The launcher selects games by family id; it logs `Selecting game family by id: X` and then
    `LaunchBinary: uid=Y` whenever the user presses Play, so any game played once is known."""
    logs = prefix / "pfx/drive_c/users/steamuser/AppData/Local/Battle.net/Logs"
    out = {}
    for f in sorted(logs.glob("battle.net-*.log")):
        family = None
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.search(r"Selecting game family by id: (\S+)", line)
            if m:
                family = m.group(1)
                continue
            m = re.search(r"LaunchBinary: uid=(\S+)", line)
            if m and family:
                out[m.group(1)] = family
    return out


# The launcher decides it is on a handheld from the board manufacturer/product Wine reports (it
# sees Valve hardware) and then shows a cut-down handheld UI that hides most games. Wine rebuilds
# those registry values from the real hardware at every start, so we set generic ones for the
# launcher's own session only, just before it starts. "To Be Filled By O.E.M." is the placeholder
# many boards report when the maker set nothing, so we do not pretend to be anyone's hardware.
LAUNCHER_SCRIPT_REL = "drive_c/bnet-steam/launcher.bat"
LAUNCHER_SCRIPT_WIN = "C:\\bnet-steam\\launcher.bat"
LAUNCHER_BAT = "\r\n".join([
    "@echo off",
    "rem Written by bnet-steam; rewritten by `bnet-steam sync`. Do not edit.",
    "rem Report a generic board so Battle.net does not switch to its handheld UI.",
    'reg add "HKLM\\Hardware\\Description\\System\\BIOS" /v BaseBoardManufacturer /t REG_SZ /d "To Be Filled By O.E.M." /f >nul',
    'reg add "HKLM\\Hardware\\Description\\System\\BIOS" /v BaseBoardProduct /t REG_SZ /d "To Be Filled By O.E.M." /f >nul',
    'start "" "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" %*',
    "",
])


def ensure_launcher_script(prefix):
    """Write (or refresh) the launcher batch file inside the prefix; returns its path."""
    path = prefix / "pfx" / LAUNCHER_SCRIPT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != LAUNCHER_BAT.encode():
        path.write_bytes(LAUNCHER_BAT.encode())
    return path


def launcher_exe(prefix):
    return prefix / "pfx" / LAUNCHER_REL


def stage(prefix):
    """(number, description) of how far the Battle.net install has got."""
    pfx = prefix / "pfx"
    if (pfx / LAUNCHER_REL).exists():
        return 3, "launcher installed"
    if (pfx / PRODUCT_DB_REL).exists():
        return 2, "Agent present, launcher NOT installed yet"
    return 2, "installer not run yet"


def launch_options(prefix, logdir=None, args="", wrapper=None):
    """Steam LaunchOptions: shared prefix, optional Proton logging, an optional wrapper
    command (already quoted, e.g. '"/x/run-game.sh" --login'; see wrapper.py), then the args."""
    opts = f'STEAM_COMPAT_DATA_PATH="{prefix}"'
    if logdir:
        opts += f' PROTON_LOG=1 PROTON_LOG_DIR="{logdir}"'
    if wrapper:
        opts += f" {wrapper}"
    return f"{opts} %command% {args}".rstrip()


# --- product.db (protobuf, parsed leniently) -------------------------------------

class ProductDbError(BnetSteamError):
    pass


def _varint(b, i):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def _fields(b):
    """Yield (field_no, value) for one protobuf message; bytes for len-delimited."""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        no, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt in (1, 2, 5):
            if wt == 2:
                n, i = _varint(b, i)
            else:
                n = 8 if wt == 1 else 4
            if i + n > len(b):
                raise ValueError("truncated field")
            v, i = b[i:i + n], i + n
        else:
            raise ValueError(f"bad wire type {wt}")
        yield no, v


def read_product_db(path):
    """[{'uid','code','install_path'}]. Layout (confirmed against real files):
    ProductInstall{1 uid, 2 product_code, 3 settings{1 install_path}}."""
    out = []
    try:
        for no, msg in _fields(Path(path).read_bytes()):
            if no != 1 or not isinstance(msg, bytes):
                continue
            rec = {"uid": "", "code": "", "install_path": ""}
            for fno, v in _fields(msg):
                if fno == 1:
                    rec["uid"] = v.decode("utf-8", "replace")
                elif fno == 2:
                    rec["code"] = v.decode("utf-8", "replace")
                elif fno == 3:
                    for sno, sv in _fields(v):
                        if sno == 1:
                            rec["install_path"] = sv.decode("utf-8", "replace")
            out.append(rec)
    except (IndexError, ValueError, TypeError) as e:
        raise ProductDbError(f"could not read {path} ({e}); Battle.net may be mid-update, "
                             "try again in a moment") from e
    return out


def read_aggregate(prefix):
    """Battle.net's own list of installed games (name, exe, art URLs), by product code."""
    p = prefix / "pfx" / AGGREGATE_REL
    if not p.exists():
        return {}
    try:
        return {g["product_id"]: g for g in json.loads(p.read_text()).get("installed", [])}
    except (ValueError, KeyError, AttributeError):
        return {}           # cosmetic data only: fall back to codes and defaults


def installed_games(prefix):
    """Games in product.db whose install dir exists, enriched from aggregate.json."""
    db = prefix / "pfx" / PRODUCT_DB_REL
    if not db.exists():
        return []
    agg = read_aggregate(prefix)
    out = []
    for r in read_product_db(db):
        if r["code"] in NON_GAMES or not r["install_path"]:
            continue
        if host_path(prefix, r["install_path"]).is_dir():
            a = agg.get(r["code"], {})
            out.append({**r, "name": a.get("name") or r["code"].replace("_", " ").title(),
                        "icon_path": a.get("icon_path", ""),
                        "box_art": a.get("box_art_uri", ""),
                        "logo_art": a.get("logo_art_uri", ""),
                        "art_codes": art_codes(r["code"])})
    return out


# Executables in an install dir that are helpers, not the game (matched case-insensitively).
NOT_THE_GAME = ("launcher", "error", "browser", "crash", "unins", "setup", "update",
                "redist", "helper", "agent", "editor", "cef")


def best_exe(gdir, reported):
    """The game's main exe. Battle.net's reported exe is used unless it is a helper
    (Diablo IV reports its 5 MB launcher stub); then the largest non-helper exe wins."""
    def helper(p):
        return any(w in p.name.lower() for w in NOT_THE_GAME)
    if reported and reported.exists() and not helper(reported):
        return reported
    # prefer the install root; some games (WoW) keep the real exe one folder down
    for pattern in ("*.exe", "*/*.exe"):
        cands = [p for p in gdir.glob(pattern) if not helper(p)]
        if cands:
            return max(cands, key=lambda p: p.stat().st_size)
    return None


def launch_spec(prefix, g, state):
    """(exe host path, args, source) for a game, or None to fall back to the launcher.
    Precedence: learned > verified > derived default."""
    gdir = host_path(prefix, g["install_path"])
    spec, source = state.get("learned", {}).get(g["code"]), "learned"
    if spec is None:
        spec, source = VERIFIED.get(g["code"]), "verified"
    if spec is None:
        spec, source = {"exe": None, "args": DEFAULT_ARGS}, "derived, unverified"
    if spec["exe"]:
        exe = resolve_ci(gdir, spec["exe"])
    else:
        reported = (resolve_ci(prefix / "pfx" / "drive_c", g["icon_path"].split(":/", 1)[-1])
                    if g["icon_path"] else None)
        exe = best_exe(gdir, reported)
    if exe is None or not exe.exists():
        return None
    return exe, spec["args"].format(uid=g["uid"]), source


# --- installer --------------------------------------------------------------------

def download_installer(env, force=False):
    import urllib.error
    import urllib.request
    env.work.mkdir(parents=True, exist_ok=True)
    dest = env.work / "Battle.net-Setup.exe"
    if dest.exists() and not force:
        return dest
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(INSTALLER_URL, headers={"User-Agent": "bnet-steam"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise BnetSteamError(
            f"could not download the Battle.net installer ({e}). Blizzard may have changed "
            f"the URL; download Battle.net-Setup.exe manually into {env.work}") from e
    if tmp.read_bytes()[:2] != b"MZ":
        tmp.unlink()
        raise BnetSteamError("the downloaded file is not a Windows executable; Blizzard "
                             "may have changed the download URL")
    tmp.replace(dest)
    return dest
