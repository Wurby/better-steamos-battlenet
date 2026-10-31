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


CDN = "https://bnetxboxassets.akamaized.net/{code}/{kind}-enUS.webp"


def _fetch(url, dest, quiet=False):
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bnet-steam"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        return True
    except OSError as e:
        if not quiet:
            print(f"  art    download failed: {url} ({e})")
        return False


def _asset(g, kind, dest):
    """Download a game's box/logo image. Uses the URL Battle.net listed; if it listed none,
    tries the game's product code then its parents (wow_classic_era -> wow_classic -> wow)."""
    listed = g["box_art"] if kind == "box" else g["logo_art"]
    urls = [listed] if listed else [CDN.format(code=c, kind=kind) for c in g.get("art_codes", [])]
    for i, url in enumerate(urls):
        if _fetch(url, dest, quiet=i < len(urls) - 1):
            return True
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
    if not shutil.which("ffmpeg") or not (g["box_art"] or g.get("art_codes")):
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
        if missing and _asset(g, "box", box):
            for stem in missing:
                w, h, fg = sizes[stem]
                _compose(box, grid / f"{stem}.jpg", w, h, fg)
                written.append(grid / f"{stem}.jpg")
        if not have_icon and _asset(g, "logo", logo):
            out = grid / f"{icon_stem}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(logo), "-frames:v", "1",
                            str(out)], check=True)
            written.append(out)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"  art    failed for {g['name']}: {e}")
    found = _existing(grid, icon_stem)
    return (str(found) if found else ""), written


# The launcher has no key art of its own, so its images are the logo (read from the user's own
# Battle.net.exe) on a dark-to-blue gradient.
LAUNCHER_BG = ("0x06142b", "0x0b4a9e")


def _brand_compose(icon, dest, w, h, fg):
    """Gradient w x h with the icon centred at fg x fg (fg=0: gradient only)."""
    bg = (f"gradients=s={w}x{h}:c0={LAUNCHER_BG[0]}:c1={LAUNCHER_BG[1]}:x0=0:y0=0:x1={w}:y1={h}"
          ":n=2:d=1")
    if fg:
        cmd = ["-f", "lavfi", "-i", bg, "-i", str(icon), "-filter_complex",
               f"[1:v]scale={fg}:{fg}:flags=lanczos[fg];[0:v][fg]overlay=(W-w)/2:(H-h)/2,"
               "format=yuvj420p"]
    else:
        cmd = ["-f", "lavfi", "-i", bg, "-vf", "format=yuvj420p"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd, "-frames:v", "1", "-q:v", "3",
                    str(dest)], check=True)


def ensure_launcher_art(env, appid, exe, dry_run=False):
    """Default art for the Battle.net shortcut: icon, logo, portrait, wide and hero images.
    Anything that already exists, in any format, is left alone.
    Returns (icon path or "", [files written])."""
    grid = env.grid_dir
    sizes = {f"{appid}p": (600, 900, 380), f"{appid}": (920, 430, 300),
             f"{appid}_hero": (3840, 1240, 0)}          # hero is background only
    icon_stem, logo_stem = f"{appid}_icon", f"{appid}_logo"
    have_icon = _existing(grid, icon_stem)
    missing = [k for k in sizes if not _existing(grid, k)]
    need_logo = not _existing(grid, logo_stem)
    if have_icon and not missing and not need_logo:
        return str(have_icon), []
    if dry_run:
        n = len(missing) + need_logo + (not have_icon)
        print(f"  art    would create {n} Battle.net image(s) from its executable")
        return str(have_icon or grid / f"{icon_stem}.png"), []
    src = env.work / "art" / "battlenet-icon.png"
    written = []
    try:
        if not src.exists():
            pe_icon.write_icon_png(exe, src)
        grid.mkdir(parents=True, exist_ok=True)
        for stem, wanted in ((icon_stem, not have_icon), (logo_stem, need_logo)):
            if wanted:
                shutil.copyfile(src, grid / f"{stem}.png")
                written.append(grid / f"{stem}.png")
        if missing and not shutil.which("ffmpeg"):
            print("  art    ffmpeg not found; skipping the Battle.net portrait/wide/hero images")
            missing = []
        for stem in missing:
            w, h, fg = sizes[stem]
            _brand_compose(src, grid / f"{stem}.jpg", w, h, fg)
            written.append(grid / f"{stem}.jpg")
    except (BnetSteamError, OSError, subprocess.CalledProcessError) as e:
        print(f"  art    Battle.net artwork failed: {e}")
    found = _existing(grid, icon_stem)
    return (str(found) if found else ""), written
