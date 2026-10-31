#!/usr/bin/env python3
"""bnet-steam: manage Battle.net and its games as non-Steam shortcuts.

Stdlib only. Stage 1 is read-only (`status`); writers come later and always
back up shortcuts.vdf first.
"""
import argparse
import os
import re
import struct
import sys
import zlib
from pathlib import Path

STEAM = Path.home() / ".local/share/Steam"
LAUNCHER_REL = "drive_c/Program Files (x86)/Battle.net/Battle.net.exe"
PRODUCT_DB_REL = "drive_c/ProgramData/Battle.net/Agent/product.db"


# --- binary VDF (shortcuts.vdf) -------------------------------------------
# 0x00 map, 0x01 string, 0x02 uint32, 0x08 end-of-map. Dicts keep order.

def vdf_loads(data):
    obj, pos = _read_map(data, 0)
    return obj


def _read_map(data, pos):
    out = {}
    while True:
        t = data[pos]
        pos += 1
        if t == 0x08:
            return out, pos
        end = data.index(b"\x00", pos)
        key = data[pos:end].decode("utf-8")
        pos = end + 1
        if t == 0x00:
            out[key], pos = _read_map(data, pos)
        elif t == 0x01:
            end = data.index(b"\x00", pos)
            out[key] = data[pos:end].decode("utf-8")
            pos = end + 1
        elif t == 0x02:
            out[key] = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            raise ValueError(f"unknown VDF type {t:#x} at {pos - 1}")


def vdf_dumps(obj):
    return _write_map(obj) + b"\x08"


def _write_map(obj):
    out = bytearray()
    for key, val in obj.items():
        k = key.encode("utf-8") + b"\x00"
        if isinstance(val, dict):
            out += b"\x00" + k + _write_map(val) + b"\x08"
        elif isinstance(val, int):
            out += b"\x02" + k + struct.pack("<I", val)
        else:
            out += b"\x01" + k + val.encode("utf-8") + b"\x00"
    return bytes(out)


# --- Steam paths / appids --------------------------------------------------

def steam_userdir():
    ids = [p for p in (STEAM / "userdata").iterdir() if p.name.isdigit() and p.name != "0"]
    if not ids:
        sys.exit("no Steam user found under userdata/")
    return max(ids, key=lambda p: (p / "config/shortcuts.vdf").stat().st_mtime
               if (p / "config/shortcuts.vdf").exists() else 0)


def shortcut_appid(exe, name):
    """Steam's id for a non-Steam shortcut: crc32(exe + name) | 1<<31."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def load_shortcuts(userdir):
    path = userdir / "config/shortcuts.vdf"
    if not path.exists():
        return {"shortcuts": {}}
    return vdf_loads(path.read_bytes())


def prefix_for(entry):
    """Wine prefix dir (the one holding pfx/) a shortcut will run in."""
    m = re.search(r'STEAM_COMPAT_DATA_PATH=("[^"]+"|\S+)', entry.get("LaunchOptions", ""))
    if m:
        return Path(m.group(1).strip('"'))
    return STEAM / "steamapps/compatdata" / str(entry["appid"])


# --- product.db (protobuf, parsed leniently) ---------------------------------

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
        elif wt == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"bad wire type {wt}")
        yield no, v


def read_product_db(path):
    """Return [{'uid','code','install_path'}]. Layout per community notes:
    ProductInstall{1 uid, 2 product_code, 3 settings{1 install_path}}.
    Unverified for real games until one is installed here."""
    out = []
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
    return out


# --- commands ---------------------------------------------------------------

def find_bnet_shortcut(shortcuts):
    for key, e in shortcuts.items():
        exe = os.path.basename(e.get("exe", "").strip('"'))
        if exe in ("Battle.net-Setup.exe", "Battle.net.exe"):
            return key, e
    return None, None


def cmd_status(args):
    userdir = steam_userdir()
    sc = load_shortcuts(userdir)["shortcuts"]
    print(f"Steam user dir : {userdir}")
    key, e = find_bnet_shortcut(sc)
    if not e:
        print("Stage          : 1 (no Battle.net shortcut yet)")
        return
    pre = prefix_for(e)
    print(f"Shortcut       : {e['appname']!r} appid={e['appid']} exe={e['exe']}")
    print(f"Prefix         : {pre}  (managed by Steam compatdata: "
          f"{'YES - removing the shortcut deletes it' if STEAM in pre.parents else 'no'})")
    pfx = pre / "pfx"
    launcher = pfx / LAUNCHER_REL
    if launcher.exists():
        stage = "3/4 (launcher installed)"
    elif (pfx / PRODUCT_DB_REL).exists():
        stage = "2 (Agent present, launcher NOT installed yet)"
    else:
        stage = "2 (installer not run yet)"
    print(f"Stage          : {stage}")
    db = pfx / PRODUCT_DB_REL
    if db.exists():
        for r in read_product_db(db):
            print(f"  product      : {r}")


WORK = Path.home() / ".local/share/bnet-steam"
INSTALLER_URL = "https://downloader.battle.net/download/getInstaller?os=win&installer=Battle.net-Setup.exe"


def cmd_download(args):
    import urllib.request
    WORK.mkdir(parents=True, exist_ok=True)
    dest = WORK / "Battle.net-Setup.exe"
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(INSTALLER_URL, headers={"User-Agent": "bnet-steam"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    if tmp.read_bytes()[:2] != b"MZ":
        tmp.unlink()
        sys.exit("download is not a Windows executable")
    tmp.replace(dest)
    print(f"saved {dest} ({dest.stat().st_size} bytes)")


PROTON_TOOL = "proton_11"
SHORTCUT_NAME = "Battle.net"
PREFIX = WORK / "prefix"      # outside steamapps/compatdata: survives shortcut removal
LOGDIR = WORK / "logs"


def steam_running():
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.read_text().strip() == "steam":
                return True
        except OSError:
            pass
    return False


def stop_steam():
    import subprocess
    import time
    subprocess.run(["steam", "-shutdown"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)
    for _ in range(90):
        if not steam_running():
            return
        time.sleep(1)
    sys.exit("Steam did not exit; aborting before any write")


def start_steam():
    import subprocess
    subprocess.Popen(["steam"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def backup(path):
    import shutil
    import time
    WORK.mkdir(parents=True, exist_ok=True)
    dest = WORK / f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dest)
    return dest


def new_shortcut(appid, name, exe, launch_options):
    exe = f'"{exe}"'
    return {
        "appid": appid, "appname": name, "exe": exe,
        "StartDir": str(Path(exe.strip('"')).parent) + "/",
        "icon": "", "ShortcutPath": "", "LaunchOptions": launch_options,
        "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0,
        "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0,
        "LastPlayTime": 0, "FlatpakAppID": "", "sortas": "", "tags": {},
    }


def set_compat_tool(text, appid, tool):
    """Add a CompatToolMapping entry to config.vdf text (idempotent)."""
    start = text.index('"CompatToolMapping"')
    open_ = text.index("{", start)
    depth, i = 0, open_
    while True:
        depth += text[i] == "{"
        depth -= text[i] == "}"
        if depth == 0:
            break
        i += 1
    if f'"{appid}"' in text[open_:i]:
        return text
    line0 = text.rfind("\n", 0, start) + 1
    ind = text[line0:start]
    e = ind + "\t"
    block = (f'{e}"{appid}"\n{e}{{\n{e}\t"name"\t\t"{tool}"\n'
             f'{e}\t"config"\t\t""\n{e}\t"priority"\t\t"250"\n{e}}}\n')
    pos = text.index("\n", open_) + 1
    return text[:pos] + block + text[pos:]


def cmd_install(args):
    userdir = steam_userdir()
    shp = userdir / "config/shortcuts.vdf"
    cfgp = STEAM / "config/config.vdf"
    data = load_shortcuts(userdir)
    sc = data["shortcuts"]
    if find_bnet_shortcut(sc)[1]:
        sys.exit("a Battle.net shortcut already exists; run `status`")
    installer = WORK / "Battle.net-Setup.exe"
    if not installer.exists():
        cmd_download(args)
    appid = shortcut_appid(f'"{installer}"', SHORTCUT_NAME)
    opts = (f'STEAM_COMPAT_DATA_PATH="{PREFIX}" PROTON_LOG=1 '
            f'PROTON_LOG_DIR="{LOGDIR}" %command%')
    entry = new_shortcut(appid, SHORTCUT_NAME, str(installer), opts)
    print(f"appid {appid}  name {SHORTCUT_NAME!r}  tool {PROTON_TOOL}")
    print(f"exe    {entry['exe']}\nprefix {PREFIX}\nopts   {opts}")
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    was_running = steam_running()
    if was_running:
        print("stopping Steam...")
        stop_steam()
    print("backups:", backup(shp), backup(cfgp))
    PREFIX.mkdir(parents=True, exist_ok=True)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    sc[str(len(sc))] = entry
    shp.write_bytes(vdf_dumps(data))
    cfgp.write_text(set_compat_tool(cfgp.read_text(), appid, PROTON_TOOL))
    import json
    (WORK / "state.json").write_text(json.dumps(
        {"appid": appid, "name": SHORTCUT_NAME, "prefix": str(PREFIX)}, indent=2))
    print("shortcut + compat tool written")
    if was_running or args.start_steam:
        start_steam()
        print("Steam restarted")


def cmd_repoint(args):
    userdir = steam_userdir()
    shp = userdir / "config/shortcuts.vdf"
    data = load_shortcuts(userdir)
    key, e = find_bnet_shortcut(data["shortcuts"])
    if not e:
        sys.exit("no Battle.net shortcut; run `install` first")
    launcher = prefix_for(e) / "pfx" / LAUNCHER_REL
    if not launcher.exists():
        sys.exit(f"launcher not installed yet ({launcher}); finish the installer first")
    new_exe = f'"{launcher}"'
    if e["exe"] == new_exe:
        print("already points at the launcher")
        return
    print(f"appid {e['appid']} (unchanged)\n  exe {e['exe']}\n   -> {new_exe}")
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    was_running = steam_running()
    if was_running:
        print("stopping Steam...")
        stop_steam()
    print("backup:", backup(shp))
    e["exe"] = new_exe
    e["StartDir"] = str(launcher.parent) + "/"
    shp.write_bytes(vdf_dumps(data))
    print("repointed")
    if was_running or args.start_steam:
        start_steam()
        print("Steam restarted")


NON_GAMES = {"agent", "bna"}
# Launch specs verified by watching the launcher's own Play button. "exe" is
# relative to the install dir (None = the exe Battle.net reports); {uid} is the
# product uid, which can differ from the product code (w2 vs w2bn).
VERIFIED = {
    "s1": {"exe": "x86_64/StarCraft.exe", "args": "-launch -uid {uid}"},
    "w2bn": {"exe": None, "args": "-uid {uid}"},
}
DEFAULT_ARGS = "-uid {uid}"     # both verified launches share this form


def load_state():
    import json
    p = WORK / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(state):
    import json
    (WORK / "state.json").write_text(json.dumps(state, indent=2))


def host_path(prefix, win_path):
    """C:/Program Files (x86)/X -> <prefix>/pfx/drive_c/Program Files (x86)/X"""
    return prefix / "pfx" / "drive_c" / win_path.replace("\\", "/").split(":/", 1)[-1]


def read_aggregate(prefix):
    """Battle.net's own list of installed games: name, exe, art URLs, by product code."""
    import json
    p = prefix / "pfx/drive_c/ProgramData/Battle.net/Agent/aggregate.json"
    if not p.exists():
        return {}
    return {g["product_id"]: g for g in json.loads(p.read_text()).get("installed", [])}


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
            out.append({**r, "name": a.get("name") or r["code"].upper(),
                        "icon_path": a.get("icon_path", ""),
                        "box_art": a.get("box_art_uri", ""),
                        "logo_art": a.get("logo_art_uri", "")})
    return out


def launch_spec(prefix, g, state):
    """(exe host path, args, source) for a game, or None to fall back to the launcher.
    Precedence: learned > verified > derived default."""
    gdir = host_path(prefix, g["install_path"])
    spec, source = state.get("learned", {}).get(g["code"]), "learned"
    if spec is None:
        spec, source = VERIFIED.get(g["code"]), "verified"
    if spec is None:
        spec, source = {"exe": None, "args": DEFAULT_ARGS}, "derived, unverified"
    exe = gdir / spec["exe"] if spec["exe"] else (
        host_path(prefix, g["icon_path"]) if g["icon_path"] else None)
    if exe is None or not exe.exists():
        return None
    return exe, spec["args"].format(uid=g["uid"]), source


ART_CACHE = WORK / "art"
ART_WRITTEN = []        # grid files created this run (Steam shows them after a restart)


def _fetch(url, dest):
    import urllib.request
    if dest.exists():
        return True
    ART_CACHE.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bnet-steam"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        return True
    except OSError as e:
        print(f"  art    download failed: {url} ({e})")
        return False


def _compose(src, dest, w, h, fg):
    """Centre the square art over a blurred, cover-scaled copy of itself."""
    import subprocess
    flt = (f"[0:v]format=rgba,split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio="
           f"increase,crop={w}:{h},boxblur=25:2[bg];[b]scale={fg}:{fg}[fg];"
           f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuvj420p")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", flt,
                    "-frames:v", "1", "-q:v", "3", str(dest)], check=True)


ART_EXTS = ("jpg", "jpeg", "png", "webp", "ico")


def _existing(grid, stem):
    """First grid file for this stem in any common format (e.g. art applied by SGDBoop)."""
    for ext in ART_EXTS:
        if (grid / f"{stem}.{ext}").exists():
            return grid / f"{stem}.{ext}"
    return None


def ensure_art(userdir, appid, g, dry_run=False):
    """Create default Steam grid images for a shortcut; return the icon path (or "").
    Art that already exists in any format is never replaced, so images applied by
    SGDBoop or by hand win. With dry_run, only report what would be created."""
    import shutil
    import subprocess
    grid = userdir / "config/grid"
    # stem -> (width, height, foreground size); the icon comes from the logo asset
    sizes = {f"{appid}p": (600, 900, 600), f"{appid}": (920, 430, 430),
             f"{appid}_hero": (3840, 1240, 1240)}
    icon_stem = f"{appid}_icon"
    missing = [k for k in sizes if not _existing(grid, k)]
    need_icon = not _existing(grid, icon_stem)
    have_icon = _existing(grid, icon_stem)
    if not missing and not need_icon:
        return str(have_icon)
    if not shutil.which("ffmpeg") or not g["box_art"]:
        return str(have_icon) if have_icon else ""
    if dry_run:
        print(f"  art    would create {len(missing) + need_icon} default image(s) for {g['name']}")
        return str(have_icon or grid / f"{icon_stem}.png")
    grid.mkdir(parents=True, exist_ok=True)
    box = ART_CACHE / f"{g['code']}-box.webp"
    logo = ART_CACHE / f"{g['code']}-logo.webp"
    try:
        if missing and _fetch(g["box_art"], box):
            for stem in missing:
                w, h, fg = sizes[stem]
                _compose(box, grid / f"{stem}.jpg", w, h, fg)
                ART_WRITTEN.append(grid / f"{stem}.jpg")
        if need_icon and g["logo_art"] and _fetch(g["logo_art"], logo):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(logo), "-frames:v", "1",
                            str(grid / f"{icon_stem}.png")], check=True)
            ART_WRITTEN.append(grid / f"{icon_stem}.png")
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"  art    failed for {g['name']}: {e}")
    found = _existing(grid, icon_stem)
    return str(found) if found else ""


def cmd_sync(args):
    userdir = steam_userdir()
    shp = userdir / "config/shortcuts.vdf"
    cfgp = STEAM / "config/config.vdf"
    data = load_shortcuts(userdir)
    sc = data["shortcuts"]
    _, launcher = find_bnet_shortcut(sc)
    if not launcher or not launcher["exe"].strip('"').endswith("Battle.net.exe"):
        sys.exit("launcher shortcut not set up; run install / repoint first")
    prefix = prefix_for(launcher)
    state = load_state()
    mine = state.setdefault("games", {})            # uid -> appid we created
    games = installed_games(prefix)
    todo, fix = [], []
    by_appid = {e["appid"]: e for e in sc.values()}
    base = f'STEAM_COMPAT_DATA_PATH="{prefix}" PROTON_LOG=1 PROTON_LOG_DIR="{LOGDIR}" %command%'
    for g in games:
        spec = launch_spec(prefix, g, state)
        if spec:    # run the game exe itself so Steam tracks the real process
            exe_p, gargs, source = spec
            exe, start, opts = f'"{exe_p}"', str(exe_p.parent) + "/", f"{base} {gargs}"
        else:       # no usable exe: ask the launcher to open the game
            exe, start, source = launcher["exe"], launcher["StartDir"], "launcher fallback"
            opts = f'{base} --exec="launch {g["code"]}"'
        entry = new_shortcut(shortcut_appid(exe, g["name"]), g["name"], exe.strip('"'), opts)
        entry["StartDir"] = start
        cur = by_appid.get(mine.get(g["uid"], entry["appid"]))
        appid = cur["appid"] if cur else entry["appid"]
        icon = (ensure_art(userdir, appid, g, args.dry_run) if not args.no_art
                else (cur["icon"] if cur else ""))
        entry["icon"] = icon
        label = f"{g['name']} ({g['code']}) [{source}]"
        if cur is None:
            print(f"  NEW    {label} appid={entry['appid']}")
            todo.append((g, entry))
        elif (cur["appname"], cur["exe"], cur["StartDir"], cur["LaunchOptions"], cur["icon"]) != \
                (g["name"], exe, start, opts, icon):
            print(f"  UPDATE {label} appid={cur['appid']} (kept)")
            fix.append((cur, g["name"], exe, start, opts, icon))
        else:
            print(f"  have   {label}")
    gone = [u for u in mine if u not in {g["uid"] for g in games}]
    for u in gone:
        print(f"  note   {u} no longer installed; its shortcut is left in place")
    if ART_WRITTEN:
        print(f"  art    wrote {len(ART_WRITTEN)} image(s)")
    if not todo and not fix:
        print("nothing to do")
        return
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    was_running = steam_running()
    if was_running:
        print("stopping Steam...")
        stop_steam()
    print("backups:", backup(shp), backup(cfgp))
    cfg = cfgp.read_text()
    for g, entry in todo:
        sc[str(len(sc))] = entry
        cfg = set_compat_tool(cfg, entry["appid"], PROTON_TOOL)
        mine[g["uid"]] = entry["appid"]
    for cur, name, exe, start, opts, icon in fix:
        cur["appname"], cur["exe"], cur["StartDir"] = name, exe, start
        cur["LaunchOptions"], cur["icon"] = opts, icon
    shp.write_bytes(vdf_dumps(data))
    cfgp.write_text(cfg)
    save_state(state)
    print(f"added {len(todo)}, updated {len(fix)} shortcut(s)")
    if was_running or args.start_steam:
        start_steam()
        print("Steam restarted")


def cmd_learn(args):
    """Watch for a game started by the launcher's Play button and record its command line."""
    import subprocess
    import time
    _, launcher = find_bnet_shortcut(load_shortcuts(steam_userdir())["shortcuts"])
    if not launcher:
        sys.exit("no Battle.net shortcut")
    prefix = prefix_for(launcher)
    games = installed_games(prefix)
    state = load_state()
    print(f"Press Play on a game in the Battle.net launcher (waiting {args.seconds}s)...")
    end = time.time() + args.seconds
    while time.time() < end:
        for line in subprocess.run(["ps", "-eo", "args"], capture_output=True,
                                   text=True).stdout.splitlines():
            for g in games:
                win = g["install_path"].replace("/", "\\") + "\\"
                i = line.lower().find(".exe")
                if not line.startswith(win) or i < 0:
                    continue
                gargs = line[i + 4:].strip()
                if g["uid"] not in gargs.split():      # helpers don't get -uid <uid>
                    continue
                rel = line[len(win):i + 4].replace("\\", "/")
                state.setdefault("learned", {})[g["code"]] = {
                    "exe": rel, "args": gargs.replace(g["uid"], "{uid}")}
                save_state(state)
                print(f"learned {g['name']} ({g['code']}): {rel} {gargs}")
                print("run `sync` to apply it")
                return
        time.sleep(1)
    print("nothing seen")


def steam_gameid(appid):
    """64-bit id for steam://rungameid/ of a non-Steam shortcut."""
    return (appid << 32) | 0x02000000


def cmd_launch(args):
    import subprocess
    _, e = find_bnet_shortcut(load_shortcuts(steam_userdir())["shortcuts"])
    if not e:
        sys.exit("no Battle.net shortcut")
    subprocess.Popen(["steam", f"steam://rungameid/{steam_gameid(e['appid'])}"],
                     start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(prog="bnet-steam")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show detected stage (read-only)")
    sub.add_parser("download", help="fetch a fresh Battle.net installer")
    p = sub.add_parser("install", help="create the installer shortcut + prefix")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--start-steam", action="store_true")
    p = sub.add_parser("repoint", help="point the shortcut at the installed launcher")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--start-steam", action="store_true")
    p = sub.add_parser("sync", help="add shortcuts for newly installed games")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-art", action="store_true", help="skip artwork")
    p.add_argument("--start-steam", action="store_true")
    p = sub.add_parser("learn", help="record how the launcher starts a game")
    p.add_argument("--seconds", type=int, default=300)
    sub.add_parser("launch", help="start the Battle.net shortcut through Steam")
    args = ap.parse_args()
    {"status": cmd_status, "download": cmd_download, "install": cmd_install,
     "repoint": cmd_repoint, "sync": cmd_sync, "learn": cmd_learn,
     "launch": cmd_launch}[args.cmd](args)


if __name__ == "__main__":
    main()
