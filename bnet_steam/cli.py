"""Command-line interface."""
import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, art, learn, steam, vdf
from . import battlenet as bn
from .errors import BnetSteamError


# --- small helpers -------------------------------------------------------------

def load_state(env):
    p = env.work / "state.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        raise BnetSteamError(f"{p} is corrupt; delete it (it only caches what we created) "
                             "and run again") from None


def save_state(env, state):
    env.work.mkdir(parents=True, exist_ok=True)
    steam.write_atomic(env.work / "state.json", json.dumps(state, indent=2).encode())


def require_launcher(env, data):
    """(launcher shortcut, its prefix) once the launcher shortcut targets Battle.net.exe."""
    launcher = bn.find_bnet_shortcut(data["shortcuts"])
    if not launcher or not launcher["exe"].strip('"').endswith("Battle.net.exe"):
        raise BnetSteamError("the launcher shortcut is not set up yet; run `install`, "
                             "finish the installer, then `repoint`")
    return launcher, bn.prefix_for(env, launcher)


# --- commands ------------------------------------------------------------------

def cmd_status(env, args):
    print(f"Steam root     : {env.root}")
    print(f"Steam user     : {env.userdir.name}")
    print(f"Data dir       : {env.work}")
    tools = [n for n, _ in steam.installed_protons(env.root)]
    print(f"Proton         : {', '.join(tools) or 'none found'}")
    print(f"ffmpeg (art)   : {'yes' if shutil.which('ffmpeg') else 'NO - default art disabled'}")
    if env.shortcuts_path.exists() and not vdf.roundtrips(env.shortcuts_path.read_bytes()):
        print("WARNING        : shortcuts.vdf does not round-trip through our codec; "
              "write commands will refuse to run")
    data = steam.load_shortcuts(env)
    e = bn.find_bnet_shortcut(data["shortcuts"])
    if not e:
        print("Stage          : 1 (no Battle.net shortcut yet; run `install`)")
        return
    prefix = bn.prefix_for(env, e)
    print(f"Shortcut       : {e['appname']!r} appid={e['appid']} exe={e['exe']}")
    safe = "NO" if bn.in_compatdata(env, prefix) else "yes"
    print(f"Prefix         : {prefix}  (safe from Steam's shortcut-removal cleanup: {safe})")
    num, desc = bn.stage(prefix)
    print(f"Stage          : {num} ({desc})")
    state = load_state(env)
    if num == 3 and e["exe"].strip('"').endswith("Battle.net-Setup.exe"):
        print("               : run `repoint` to switch the shortcut to the launcher")
    for g in bn.installed_games(prefix):
        spec = bn.launch_spec(prefix, g, state)
        print(f"  game         : {g['name']} ({g['code']}) [{spec[2] if spec else 'launcher fallback'}]")


def cmd_download(env, args):
    print(f"saved {bn.download_installer(env, force=True)}")


def cmd_install(env, args):
    data = steam.load_shortcuts(env, strict=True)
    if bn.find_bnet_shortcut(data["shortcuts"]):
        raise BnetSteamError("a Battle.net shortcut already exists; run `status`")
    tool = steam.pick_proton(env.root, args.proton)
    installer = bn.download_installer(env)
    prefix = env.work / "prefix"
    logdir = env.work / "logs" if args.proton_log else None
    opts = bn.launch_options(prefix, logdir)
    appid = steam.shortcut_appid(f'"{installer}"', bn.SHORTCUT_NAME)
    entry = steam.new_shortcut(appid, bn.SHORTCUT_NAME, str(installer), opts)
    print(f"appid {appid}  name {bn.SHORTCUT_NAME!r}  compat tool {tool}")
    print(f"exe    {entry['exe']}\nprefix {prefix}\nopts   {opts}")
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    prefix.mkdir(parents=True, exist_ok=True)
    if logdir:
        logdir.mkdir(parents=True, exist_ok=True)
    with steam.write_session(env, "Add the Battle.net installer shortcut.",
                             args.yes, args.start_steam) as s:
        data = s.load_shortcuts()
        if appid not in steam.by_appid(data["shortcuts"]):
            data["shortcuts"][steam.next_key(data["shortcuts"])] = entry
        s.save_shortcuts(data)
        s.save_config(vdf.set_compat_tool(s.read_config(), appid, tool))
    state = load_state(env)
    state.update(appid=appid, name=bn.SHORTCUT_NAME, prefix=str(prefix), proton=tool,
                 proton_log=bool(args.proton_log))
    save_state(env, state)
    print("shortcut + compat tool written; launch it from Steam to run the installer")


def cmd_repoint(env, args):
    data = steam.load_shortcuts(env, strict=True)
    e = bn.find_bnet_shortcut(data["shortcuts"])
    if not e:
        raise BnetSteamError("no Battle.net shortcut; run `install` first")
    launcher = bn.launcher_exe(bn.prefix_for(env, e))
    if not launcher.exists():
        raise BnetSteamError(f"the launcher is not installed yet ({launcher}); finish the "
                             "installer first")
    new_exe = f'"{launcher}"'
    if e["exe"] == new_exe:
        print("already points at the launcher")
        return
    print(f"appid {e['appid']} (unchanged)\n  exe {e['exe']}\n   -> {new_exe}")
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    with steam.write_session(env, "Point the Battle.net shortcut at the installed launcher.",
                             args.yes, args.start_steam) as s:
        data = s.load_shortcuts()
        cur = steam.by_appid(data["shortcuts"]).get(e["appid"])
        if cur is None:
            raise BnetSteamError("the Battle.net shortcut disappeared while Steam was closing")
        cur["exe"], cur["StartDir"] = new_exe, str(launcher.parent) + "/"
        s.save_shortcuts(data)
    print("repointed")


def cmd_sync(env, args):
    data = steam.load_shortcuts(env, strict=True)
    launcher, prefix = require_launcher(env, data)
    state = load_state(env)
    if args.proton_log is not None:
        state["proton_log"] = args.proton_log
    logdir = env.work / "logs" if state.get("proton_log") else None
    tool = state.get("proton") or steam.pick_proton(env.root, args.proton)
    mine = state.setdefault("games", {})            # uid -> appid we created
    games = bn.installed_games(prefix)
    guide_unverified(env, state, prefix, games, args)
    existing = steam.by_appid(data["shortcuts"])
    adds, updates, art_written = [], [], 0
    if not args.no_art and bn.launcher_exe(prefix).exists():
        icon, written = art.ensure_launcher_icon(env, launcher["appid"],
                                                 bn.launcher_exe(prefix), args.dry_run)
        art_written += len(written)
        if icon and not launcher.get("icon"):       # never replace an icon the user chose
            print(f"  UPDATE Battle.net launcher icon appid={launcher['appid']} (kept)")
            updates.append((launcher["appid"], {"icon": icon}))
    for g in games:
        spec = bn.launch_spec(prefix, g, state)
        if spec:    # run the game exe itself so Steam tracks the real process
            exe_p, gargs, source = spec
            exe, start = f'"{exe_p}"', str(exe_p.parent) + "/"
        else:       # no usable exe: ask the launcher to open the game
            exe, start, source = launcher["exe"], launcher["StartDir"], "launcher fallback"
            gargs = f'--exec="launch {g["code"]}"'
        opts = bn.launch_options(prefix, logdir, gargs)
        new_id = steam.shortcut_appid(exe, g["name"])
        cur = existing.get(mine.get(g["uid"], new_id))
        appid = cur["appid"] if cur else new_id
        icon = cur.get("icon", "") if cur else ""
        if not args.no_art:
            found, written = art.ensure_art(env, appid, g, args.dry_run)
            icon, art_written = found or icon, art_written + len(written)
        label = f"{g['name']} ({g['code']}) [{source}]"
        if cur is None:
            print(f"  NEW    {label} appid={appid}")
            adds.append((g, steam.new_shortcut(appid, g["name"], exe.strip('"'), opts, start, icon)))
            continue
        want = {"appname": g["name"], "exe": exe, "StartDir": start,
                "LaunchOptions": opts, "icon": icon}
        if any(cur.get(k) != v for k, v in want.items()):
            print(f"  UPDATE {label} appid={appid} (kept)")
            updates.append((appid, want))
        else:
            print(f"  have   {label}")
    removes = plan_prune(prefix, launcher, state, games, existing, args)
    # forget shortcuts that are already gone from Steam (e.g. removed by hand)
    stale = [u for u, a in mine.items()
             if u not in {g["uid"] for g in games} and a not in existing]
    if stale and not args.dry_run:
        for u in stale:
            mine.pop(u)
        save_state(env, state)
    if art_written:
        print(f"  art    wrote {art_written} image(s); restart Steam to see them")
    if not adds and not updates and not removes:
        print("nothing to do")
        return
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    summary = f"Add {len(adds)}, update {len(updates)} and remove {len(removes)} shortcut(s)."
    with steam.write_session(env, summary, args.yes, args.start_steam) as s:
        data = s.load_shortcuts()
        sc, cfg = data["shortcuts"], s.read_config()
        cur_by = steam.by_appid(sc)
        for g, entry in adds:
            if entry["appid"] not in cur_by:
                sc[steam.next_key(sc)] = entry
            cfg = vdf.set_compat_tool(cfg, entry["appid"], tool)
        for appid, want in updates:
            if appid in cur_by:
                cur_by[appid].update(want)
        if removes:
            drop = {appid for appid, _, _ in removes}
            kept = [e for e in sc.values() if e["appid"] not in drop]
            data["shortcuts"] = {str(i): e for i, e in enumerate(kept)}     # keep keys contiguous
        s.save_shortcuts(data)
        s.save_config(cfg)
    for g, entry in adds:
        mine[g["uid"]] = entry["appid"]
    for _, _, uid in removes:
        mine.pop(uid, None)
    save_state(env, state)
    print(f"added {len(adds)}, updated {len(updates)}, removed {len(removes)} shortcut(s)")


def plan_prune(prefix, launcher, state, games, existing, args):
    """Shortcuts this tool created for games that are no longer installed.
    Returns [(appid, name, uid)] to remove. Removal is opt-in: asked about on a terminal,
    done without asking only with --prune, otherwise just reported."""
    installed = {g["uid"] for g in games}
    gone = [(appid, existing[appid]["appname"], uid)
            for uid, appid in state.get("games", {}).items()
            if uid not in installed and appid in existing and appid != launcher["appid"]]
    if not gone:
        return []
    # if Battle.net's files are unreadable every game would look uninstalled; remove nothing
    if not ((prefix / "pfx" / bn.PRODUCT_DB_REL).exists() and bn.launcher_exe(prefix).exists()):
        print("  note   cannot tell what is installed (Battle.net files are missing); "
              "not removing anything")
        return []
    if args.prune is False:
        return []
    if args.prune is None:
        if args.dry_run or args.yes or not sys.stdin.isatty():
            for _, name, _ in gone:
                print(f"  gone   {name}: no longer installed; `sync --prune` removes its shortcut")
            return []
        print("Some games are no longer installed. Removing a shortcut makes Steam delete its "
              "own compat folder for it; your Battle.net install is not affected.")
        return [g for g in gone if input(f"Remove the Steam shortcut for {g[1]}? [y/N] "
                                         ).strip().lower() in ("y", "yes")]
    for appid, name, _ in gone:
        print(f"  REMOVE {name} appid={appid} (no longer installed)")
    return gone


def learn_games(env, state, prefix, games, seconds, skip_on_enter=False):
    """Watch for the launcher starting one of `games`; record and save what it runs."""
    hit = learn.watch(games, seconds, skip_on_enter)
    if not hit:
        return None
    g, rel, gargs = hit
    state.setdefault("learned", {})[g["code"]] = learn.to_spec(g, rel, gargs)
    save_state(env, state)
    print(f"learned {g['name']} ({g['code']}): {rel} {gargs}")
    return g


def cmd_learn(env, args):
    """Press Play on a game in the launcher; record how it is started."""
    _, prefix = require_launcher(env, steam.load_shortcuts(env))
    games = bn.installed_games(prefix)
    if args.game:
        games = [g for g in games if args.game in (g["code"], g["uid"])]
        if not games:
            raise BnetSteamError(f"no installed game {args.game!r}")
    print(f"Press Play on a game in the Battle.net launcher (waiting {args.seconds}s)...")
    if learn_games(env, load_state(env), prefix, games, args.seconds):
        print("run `sync` to apply it")
    else:
        print("nothing seen")


def guide_unverified(env, state, prefix, games, args):
    """Walk the user through teaching us games whose start command we can only guess."""
    todo = []
    for g in games:
        spec = bn.launch_spec(prefix, g, state)
        if spec is None or spec[2].startswith("derived"):
            todo.append(g)
    if not todo:
        return
    scripted = args.yes or not sys.stdin.isatty()
    wanted = args.learn if args.learn is not None else not scripted
    if args.dry_run or not wanted:
        for g in todo:
            print(f"  note   {g['name']}: start command is a guess; run `learn --game "
                  f"{g['code']}` or `sync --learn` to record the real one")
        return
    for g in todo:
        print(f"\n{g['name']} has not been verified, so its start command would be a guess.")
        print("Open the Battle.net launcher (start the 'Battle.net' shortcut in Steam if it is")
        print(f"not open), press Play on {g['name']}, let it reach its first screen, then quit.")
        print("Waiting up to 3 minutes; press Enter here to skip and use the guess.")
        if not learn_games(env, state, prefix, [g], 180, skip_on_enter=sys.stdin.isatty()):
            print(f"skipped {g['name']}; its shortcut will use a guess")


def cmd_launch(env, args):
    e = bn.find_bnet_shortcut(steam.load_shortcuts(env)["shortcuts"])
    if not e:
        raise BnetSteamError("no Battle.net shortcut")
    subprocess.Popen(["steam", f"steam://rungameid/{steam.steam_gameid(e['appid'])}"],
                     start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def cmd_restore(env, args):
    names = {"shortcuts.vdf": env.shortcuts_path, "config.vdf": env.config_path}
    if args.file:
        b = Path(args.file).expanduser()
        target = next((t for n, t in names.items() if b.name.startswith(n + ".bak-")), None)
        if not b.is_file() or target is None:
            raise BnetSteamError("expected a backup file like shortcuts.vdf.bak-YYYYMMDD-HHMMSS")
        picks = [(target, b)]
    elif args.latest:
        picks = [(t, bl[0]) for n, t in names.items() if (bl := steam.list_backups(env, n))]
        if not picks:
            raise BnetSteamError("no backups found")
    else:
        for n in names:
            bl = steam.list_backups(env, n)
            print(f"{n}: {len(bl)} backup(s)")
            for b in bl:
                print(f"  {b}")
        print("Restore with: restore --latest   or   restore <backup file>")
        return
    for t, b in picks:
        print(f"restore {t}\n   from {b}")
    if args.dry_run:
        print("[dry-run] nothing written")
        return
    with steam.write_session(env, "Restore Steam files from backup (current files are "
                             "backed up first, so this can be undone).",
                             args.yes, args.start_steam) as s:
        for t, b in picks:
            s.restore_file(t, b)
    print("restored")


def cmd_desktop(env, args):
    """Install an app-menu entry (and optionally a desktop icon) that runs `sync`."""
    env.work.mkdir(parents=True, exist_ok=True)
    pkg_root = Path(__file__).resolve().parent.parent
    script = env.work / "sync.sh"
    script.write_text(
        "#!/bin/bash\n"
        "# Run `bnet-steam sync` in a terminal and keep it open to read the result.\n"
        f"PYTHONPATH={shlex.quote(str(pkg_root))} {shlex.quote(sys.executable)} "
        f"-m bnet_steam --data-dir {shlex.quote(str(env.work))} "
        f"--steam-root {shlex.quote(str(env.root))} sync --start-steam\n"
        'echo\nread -r -p "Press Enter to close"\n')
    script.chmod(0o755)
    exec_path = f'"{script}"' if " " in str(script) else str(script)
    entry = ("[Desktop Entry]\nType=Application\nName=Battle.net Sync\n"
             "Comment=Add newly installed Battle.net games to Steam\n"
             f"Exec={exec_path}\nTerminal=true\nIcon=steam\nCategories=Game;\n")
    apps = Path.home() / ".local/share/applications"
    apps.mkdir(parents=True, exist_ok=True)
    targets = [apps / "bnet-steam-sync.desktop"]
    if args.on_desktop:
        d = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
        targets.append(Path(d or Path.home() / "Desktop") / "Battle.net Sync.desktop")
    for t in targets:
        t.write_text(entry)
        t.chmod(0o755)
        print(f"wrote {t}")


# --- entry point ----------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="bnet-steam",
        description="Add Battle.net and its installed games to Steam as non-Steam shortcuts.")
    ap.add_argument("--version", action="version", version=f"bnet-steam {__version__}")
    ap.add_argument("--steam-root", help="Steam install dir (default: auto-detect)")
    ap.add_argument("--steam-user", help="Steam account id under userdata/ (default: most recent)")
    ap.add_argument("--data-dir", help="where we keep the Wine prefix, art cache and backups "
                                       "(default: ~/.local/share/bnet-steam)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    write = argparse.ArgumentParser(add_help=False)
    write.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    write.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    write.add_argument("--start-steam", action="store_true",
                       help="start Steam afterwards even if it was not running")

    sub.add_parser("status", help="show what was detected and how far the setup has got")
    sub.add_parser("download", help="download a fresh Battle.net installer")
    p = sub.add_parser("install", parents=[write], help="add the installer shortcut + prefix")
    p.add_argument("--proton", help="compat tool name (default: newest installed Proton)")
    p.add_argument("--proton-log", action="store_true", help="enable Proton logging (large files)")
    sub.add_parser("repoint", parents=[write], help="point the shortcut at the installed launcher")
    p = sub.add_parser("sync", parents=[write], help="add/update shortcuts for installed games")
    p.add_argument("--proton", help="compat tool name if not set at install time")
    p.add_argument("--no-art", action="store_true", help="skip default artwork")
    p.add_argument("--prune", action=argparse.BooleanOptionalAction, default=None,
                   help="remove shortcuts for games that are no longer installed "
                        "(default: ask on a terminal)")
    p.add_argument("--learn", action=argparse.BooleanOptionalAction, default=None,
                   help="walk through recording unverified games (default: when interactive)")
    p.add_argument("--proton-log", action=argparse.BooleanOptionalAction, default=None,
                   help="turn Proton logging on/off for game shortcuts")
    p = sub.add_parser("learn", help="record how the launcher starts a game")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--game", help="only watch for this product code or uid")
    sub.add_parser("launch", help="start the Battle.net shortcut through Steam")
    p = sub.add_parser("restore", parents=[write], help="list or restore Steam file backups")
    p.add_argument("file", nargs="?", help="backup file to restore")
    p.add_argument("--latest", action="store_true", help="restore the newest backup of each file")
    p = sub.add_parser("desktop", help="add an app-menu entry that runs `sync`")
    p.add_argument("--on-desktop", action="store_true", help="also put an icon on the desktop")
    return ap


COMMANDS = {"status": cmd_status, "download": cmd_download, "install": cmd_install,
            "repoint": cmd_repoint, "sync": cmd_sync, "learn": cmd_learn,
            "launch": cmd_launch, "restore": cmd_restore, "desktop": cmd_desktop}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        env = steam.discover(args.steam_root, args.steam_user, args.data_dir)
        COMMANDS[args.cmd](env, args)
    except BnetSteamError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
