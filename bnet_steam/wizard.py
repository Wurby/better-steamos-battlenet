"""Guided setup for people who would rather not learn the commands.

Works out where you are (nothing set up yet / waiting for the Battle.net installer / ready to
switch the shortcut to the launcher / ready to add games), does the next step, and explains each
one in plain words. Running it again later adds any games you have installed since.
"""
import select
import sys
import time
from pathlib import Path

from . import battlenet as bn
from . import steam
from .errors import BnetSteamError

INTRO = """\
Battle.net for Steam
====================
This sets up Battle.net on your Steam Machine or Deck, then adds the Battle.net games you
install to Steam so you can start them like any other game.

Steam will close and reopen a few times while this runs. That is normal. I will explain each
step and ask before I change anything.
"""

INTRO_RETURNING = """\
Battle.net for Steam
====================
Checking for new Battle.net games. If there are changes I will ask before I make them.
"""

AFTER_ADD = """\
Battle.net has been added to Steam. Now, in Steam:

  1. Open your Library and find "Battle.net" (look under "Non-Steam" if you cannot see it).
  2. Press Play. The Battle.net installer opens: click through it and sign in.
  3. When Battle.net's home page shows, close Battle.net.

I will wait here and carry on by myself once Battle.net is installed and closed.
(If you have already done that, press Enter.)
"""

DONE = """\
All done.

  * To add more games: install them in Battle.net (start "Battle.net" from Steam), close it,
    then double-click "Battle.net for Steam" again.
  * Some games (World of Warcraft, Diablo IV) can only be started by the Battle.net launcher.
    Their shortcut opens Battle.net; press Play there.
"""


class Console:
    """Talks to a person at a terminal. Tests replace this."""

    def say(self, text=""):
        print(text, flush=True)

    def confirm(self, prompt, default=True):
        hint = "[Y/n]" if default else "[y/N]"
        ans = input(f"{prompt} {hint} ").strip().lower()
        return default if not ans else ans in ("y", "yes")

    def wait_for(self, condition, seconds, poll=5, on_enter=None):
        """Wait until condition() is true. Pressing Enter re-checks it right away (and calls
        on_enter first). Returns True when the condition held, False on timeout."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if condition():
                return True
            if sys.stdin.isatty() and select.select([sys.stdin], [], [], poll)[0]:
                sys.stdin.readline()
                if condition():
                    return True
                if on_enter:
                    on_enter()
            else:
                time.sleep(poll)
        return False


def battlenet_running(proc=Path("/proc")):
    """True while the Battle.net installer or launcher is running (it may be mid-install)."""
    for f in proc.glob("[0-9]*/cmdline"):
        try:
            cmd = f.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").lower()
        except OSError:
            continue
        if cmd.startswith("c:\\program files (x86)\\battle.net\\battle.net.exe") or \
                "battle.net-setup.exe" in cmd.split(" ")[0]:
            return True
    return False


def stage(env):
    """('install' | 'installer' | 'repoint' | 'sync', shortcut or None)."""
    entry = bn.find_bnet_shortcut(steam.load_shortcuts(env)["shortcuts"])
    if entry is None:
        return "install", None
    if entry["exe"].strip('"').endswith("Battle.net-Setup.exe"):
        launcher = bn.launcher_exe(bn.prefix_for(env, entry))
        return ("repoint" if launcher.exists() else "installer"), entry
    return "sync", entry


def run(env, io=None, installer_wait=3600, running=battlenet_running):
    from . import cli          # late: cli imports this module's command
    io = io or Console()
    st, entry = stage(env)
    io.say(INTRO_RETURNING if st == "sync" else INTRO)

    if st == "install":
        if not io.confirm("Add Battle.net to Steam now? (Steam will close and reopen.)"):
            io.say("Okay, nothing was changed.")
            return
        cli.cmd_install(env, cli.make_args(yes=True, start_steam=True))
        io.say()
        io.say(AFTER_ADD)
        st, entry = "installer", None

    if st in ("installer", "repoint"):
        # Switching the shortcut restarts Steam, which would kill an installer that is still
        # finishing (or a launcher mid-login), so wait until Battle.net is installed AND closed.
        if entry is None:
            _, entry = stage(env)
        launcher = bn.launcher_exe(bn.prefix_for(env, entry))
        if not launcher.exists() or running():
            if launcher.exists():
                io.say("Battle.net is installed but still open. Close it and I will carry on.")
            else:
                io.say("Waiting for Battle.net to finish installing...")
            ready = io.wait_for(lambda: launcher.exists() and not running(), installer_wait,
                                on_enter=lambda: io.say(
                                    "Not yet: Battle.net must be installed and closed."))
            if not ready:
                io.say("I did not see the install finish. Run this shortcut again after "
                       "Battle.net is installed and closed.")
                return
        io.say("\nBattle.net is installed. Updating its Steam shortcut "
               "(Steam will close and reopen)...")
        cli.cmd_repoint(env, cli.make_args(yes=True, start_steam=True))
        st = "sync"

    if st == "sync":
        io.say("\nLooking for your Battle.net games...")
        cli.cmd_sync(env, cli.make_args(start_steam=True))
        try:
            cli.cmd_desktop(env, cli.make_args(on_desktop=True), quiet=True)   # pick up the icon
        except BnetSteamError:
            pass
        io.say()
        io.say(DONE)
