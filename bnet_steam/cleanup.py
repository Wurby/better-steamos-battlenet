"""Take Battle.net back out: the Steam shortcuts this tool made, and optionally everything else.

Only things we can show we created are touched: the launcher shortcut and the game shortcuts
recorded in state.json, the art named after their appids, the data folder, our command and icons.
Steam's own files change only through steam.write_session (done by the caller).
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import art
from . import battlenet as bn
from .errors import BnetSteamError

ART_SUFFIXES = ("", "p", "_hero", "_icon", "_logo")


@dataclass
class Plan:
    shortcuts: list = field(default_factory=list)      # [(appid, name)]
    art: list = field(default_factory=list)            # files in Steam's grid folder
    data_dir: Path = None
    data_exists: bool = False
    extras: list = field(default_factory=list)         # our command and icons

    @property
    def appids(self):
        return {a for a, _ in self.shortcuts}

    def empty(self):
        return not (self.shortcuts or self.art or self.data_exists or self.extras)


def set_up_before(env, plan):
    """True if a setup was started at some point: a shortcut, a prefix or a saved state."""
    return bool(plan.shortcuts or (env.work / "prefix").exists()
                or (env.work / "state.json").exists())


def desktop_dir():
    import subprocess
    try:
        d = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                           text=True).stdout.strip()
    except OSError:
        d = ""
    return Path(d or Path.home() / "Desktop")


def our_files():
    """The command and icons install.sh / `desktop` create."""
    return [Path.home() / ".local/bin/bnet-steam",
            Path.home() / ".local/share/applications/bnet-steam.desktop",
            desktop_dir() / "Battle.net for Steam.desktop"]


def plan(env, shortcuts, state):
    p = Plan(data_dir=env.work)
    existing = {e["appid"]: e for e in shortcuts.values()}
    launcher = bn.find_bnet_shortcut(shortcuts)
    if launcher:
        p.shortcuts.append((launcher["appid"], launcher["appname"]))
    for appid in state.get("games", {}).values():
        if appid in existing and appid != (launcher or {}).get("appid"):
            p.shortcuts.append((appid, existing[appid]["appname"]))
    for appid in sorted(p.appids):
        for suffix in ART_SUFFIXES:
            for ext in art.ART_EXTS:
                f = env.grid_dir / f"{appid}{suffix}.{ext}"
                if f.exists():
                    p.art.append(f)
    p.data_exists = any((env.work / m).exists() for m in MARKERS)
    p.extras = [f for f in our_files() if f.exists()]
    return p


# what marks a folder as ours; we refuse to delete one that has none of these
MARKERS = ("state.json", "prefix", "app", "run-game.sh", "bnet-steam.sh")


def tree_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def check_data_dir(env):
    """Refuse to delete a folder that is not clearly ours."""
    d = env.work.resolve()
    home = Path.home().resolve()
    if d in (home, Path(d.anchor)) or d in home.parents or len(d.parts) < 3:
        raise BnetSteamError(f"refusing to delete {d}: it is not a bnet-steam data folder")
    root = env.root.resolve()
    if root == d or d in root.parents or root in d.parents:
        raise BnetSteamError(f"refusing to delete {d}: it overlaps your Steam folder")
    if not any((d / m).exists() for m in MARKERS):
        raise BnetSteamError(f"refusing to delete {d}: it does not look like a bnet-steam "
                             "data folder")
    return d


def delete_art(files):
    n = 0
    for f in files:
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def delete_everything(env, plan_):
    """Data folder first (so a half-finished run leaves the command to try again), then the
    command and icons. Returns the list of what was removed."""
    import shutil
    d = check_data_dir(env)
    removed = []
    if plan_.data_exists:
        shutil.rmtree(d)
        removed.append(str(d))
    for f in plan_.extras:
        try:
            f.unlink()
            removed.append(str(f))
        except OSError:
            pass
    return removed
