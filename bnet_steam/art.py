"""Default Steam library art built from Blizzard's own images, via ffmpeg.

Blizzard only supplies a square key-art tile and a small icon per game. We centre
the tile over a blurred copy of itself to fill Steam's portrait/wide/hero shapes.
Art that already exists in any common format (e.g. applied by SGDBoop) is never
replaced.
"""
import shutil
import subprocess
import urllib.request

from . import pe_icon
from .errors import BnetSteamError

ART_EXTS = ("jpg", "jpeg", "png", "webp", "ico")


def _existing(grid, stem):
    for ext in ART_EXTS:
        if (grid / f"{stem}.{ext}").exists():
            return grid / f"{stem}.{ext}"
    return None


def _fetch(url, dest):
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bnet-steam"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        return True
    except OSError as e:
        print(f"  art    download failed: {url} ({e})")
        return False


def _compose(src, dest, w, h, fg):
    flt = (f"[0:v]format=rgba,split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio="
           f"increase,crop={w}:{h},boxblur=25:2[bg];[b]scale={fg}:{fg}[fg];"
           f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuvj420p")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", flt,
                    "-frames:v", "1", "-q:v", "3", str(dest)], check=True)


def ensure_art(env, appid, g, dry_run=False):
    """Create missing default grid images for a shortcut.
    Returns (icon path or "", [files written])."""
    grid = env.grid_dir
    # stem -> (width, height, foreground size)
    sizes = {f"{appid}p": (600, 900, 600), f"{appid}": (920, 430, 430),
             f"{appid}_hero": (3840, 1240, 1240)}
    icon_stem = f"{appid}_icon"
    missing = [k for k in sizes if not _existing(grid, k)]
    have_icon = _existing(grid, icon_stem)
    if not missing and have_icon:
        return str(have_icon), []
    if not shutil.which("ffmpeg") or not g["box_art"]:
        return (str(have_icon) if have_icon else ""), []
    if dry_run:
        print(f"  art    would create {len(missing) + (not have_icon)} default image(s) "
              f"for {g['name']}")
        return str(have_icon or grid / f"{icon_stem}.png"), []
    cache = env.work / "art"
    box, logo = cache / f"{g['code']}-box.webp", cache / f"{g['code']}-logo.webp"
    written = []
    grid.mkdir(parents=True, exist_ok=True)
    try:
        if missing and _fetch(g["box_art"], box):
            for stem in missing:
                w, h, fg = sizes[stem]
                _compose(box, grid / f"{stem}.jpg", w, h, fg)
                written.append(grid / f"{stem}.jpg")
        if not have_icon and g["logo_art"] and _fetch(g["logo_art"], logo):
            out = grid / f"{icon_stem}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(logo), "-frames:v", "1",
                            str(out)], check=True)
            written.append(out)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"  art    failed for {g['name']}: {e}")
    found = _existing(grid, icon_stem)
    return (str(found) if found else ""), written


def ensure_launcher_icon(env, appid, exe, dry_run=False):
    """Default icon for the Battle.net shortcut, taken from the launcher's own exe.
    Returns (icon path or "", [files written])."""
    have = _existing(env.grid_dir, f"{appid}_icon")
    if have:
        return str(have), []
    dest = env.grid_dir / f"{appid}_icon.png"
    if dry_run:
        print("  art    would extract the Battle.net icon from its executable")
        return str(dest), []
    try:
        pe_icon.write_icon_png(exe, dest)
    except BnetSteamError as e:
        print(f"  art    launcher icon failed: {e}")
        return "", []
    return str(dest), [dest]
