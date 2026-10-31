"""Learn how Battle.net starts a game by watching its Play button.

Games can't be launched from install metadata alone: Diablo IV, for instance, is started
as `Diablo IV.exe -nostreamline -sso -launch -uid fenris`. We watch the process list while
the user presses Play once, and record the exact command.
"""
import select
import subprocess
import sys
import time


def ps_lines():
    return subprocess.run(["ps", "-eo", "args"], capture_output=True,
                          text=True).stdout.splitlines()


def parse_launch(line, games):
    """If a `ps` args line is a game started by Battle.net, return
    (game, exe path relative to its install dir, args). Helper processes
    (crash reporters, browsers, agents) never carry `-uid <uid>`, so they are ignored."""
    i = line.lower().find(".exe")
    if i < 0:
        return None
    for g in games:
        win = g["install_path"].replace("/", "\\") + "\\"
        if not line.startswith(win):
            continue
        gargs = line[i + 4:].strip()
        if g["uid"] not in gargs.split():
            continue
        return g, line[len(win):i + 4].replace("\\", "/"), gargs
    return None


def to_spec(game, rel, gargs):
    """The stored launch spec: the uid becomes a placeholder so it stays reusable."""
    toks = ["{uid}" if t == game["uid"] else t for t in gargs.split()]
    return {"exe": rel, "args": " ".join(toks)}


def watch(games, seconds, skip_on_enter=False, lines=ps_lines, now=time.time,
          sleep=time.sleep):
    """Wait up to `seconds` for one of `games` to be started. Returns
    (game, rel exe, args), or None on timeout or when the user presses Enter."""
    end = now() + seconds
    while now() < end:
        for line in lines():
            hit = parse_launch(line, games)
            if hit:
                return hit
        if skip_on_enter:
            if select.select([sys.stdin], [], [], 1)[0]:
                sys.stdin.readline()
                return None
        else:
            sleep(1)
    return None
