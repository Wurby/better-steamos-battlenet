"""Guided setup for people who would rather not learn the commands.

Works out where you are (nothing set up yet / waiting for the Battle.net installer / ready to
switch the shortcut to the launcher / ready to add games), does the next step, and explains each
one in plain words, one screen per step. Running it again later adds any games you have
installed since.
"""
import sys
from pathlib import Path

from . import battlenet as bn
from . import cleanup, steam, ui
from .errors import BnetSteamError

STEPS = 4

WELCOME = ("This sets up Battle.net on your Steam Machine or Deck, then adds the Battle.net "
           "games you install to Steam so you can start them like any other game.")
PLAN = ["1. Add Battle.net to Steam",
        "2. Install Battle.net (you click through its installer)",
        "3. Switch Steam over to the installed Battle.net",
        "4. Add your games"]
HEADS_UP = ("Steam will close and reopen a few times while this runs. That is normal. I will "
            "explain each step and ask before I change anything.")

INSTALL_STEPS = ["1. Open your Library and find \"Battle.net\" (look under \"Non-Steam\" if you "
                 "cannot see it).",
                 "2. Press Play. The Battle.net installer opens: click through it and sign in.",
                 "3. When Battle.net's home page shows, close Battle.net."]

DONE = ["To add more games: install them in Battle.net (start \"Battle.net\" from Steam), "
        "close it, then double-click \"Battle.net for Steam\" again.",
        "",
        "Some games (World of Warcraft, Diablo IV) can only be started by the Battle.net "
        "launcher. Their shortcut opens Battle.net; press Play there."]


# friendlier wording for what the commands print while they work
PROGRESS = {"Closing Steam": "Closing Steam...", "backup:": "Backing up your Steam files...",
            "Steam restarted": "Starting Steam again..."}


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


def run(env, io=None, installer_wait=3600, running=battlenet_running, verbose=False):
    io = io or ui.Terminal(verbose, log_path=env.work / "logs" / "wizard-last.log")
    try:
        _run(env, io, installer_wait, running)
    except BnetSteamError as e:
        io.problem(str(e), None if io.verbose else
                   "If you ask someone for help, run this again with -v to see the details.")
        e.shown = True              # the caller need not print it again
        raise


def _run(env, io, installer_wait, running):
    from . import cli          # late: cli imports this module's command
    st, entry = stage(env)
    plan = cleanup.plan(env, steam.load_shortcuts(env)["shortcuts"], cli.load_state(env))
    chose = False               # picking "go" from the menu already says yes to starting
    if st != "install" or cleanup.set_up_before(env, plan):
        if menu(env, io, cli, st, plan) != "go":
            return
        chose = True
    first_time = st != "sync"
    done = []                  # what earlier steps achieved, shown at the top of later screens

    if st == "install" and not chose:
        io.screen("Welcome")
        io.say(WELCOME)
        io.blank()
        io.box(PLAN)
        io.blank()
        io.say(HEADS_UP)
        if not io.confirm("Ready to start?"):
            io.blank()
            io.say("Okay, nothing was changed.")
            return

    if st == "install":
        io.screen("Adding Battle.net to Steam", 1, STEPS)
        io.run("Adding Battle.net to Steam...",
               lambda: cli.cmd_install(env, cli.make_args(yes=True, start_steam=True)), PROGRESS)
        done.append("Battle.net is in your Steam library")
        st, entry = "installer", None

    if st in ("installer", "repoint"):
        # Switching the shortcut restarts Steam, which would kill an installer that is still
        # finishing (or a launcher mid-login), so wait until Battle.net is installed AND closed.
        if entry is None:
            _, entry = stage(env)
        launcher = bn.launcher_exe(bn.prefix_for(env, entry))
        if not launcher.exists() or running():
            io.screen("Install Battle.net", 2, STEPS, done)
            if launcher.exists():
                io.say("Battle.net is installed but still open. Close it and I will carry on.")
            else:
                io.say("Now, in Steam:")
                io.blank()
                io.box(INSTALL_STEPS)
                io.blank()
                io.say("I will carry on by myself once Battle.net is installed and closed. "
                       "Already finished? Press Enter and I will check.")
            io.blank()
            ready = io.wait_for(
                lambda: launcher.exists() and not running(), installer_wait,
                "Waiting for Battle.net to finish installing...",
                on_enter=lambda: io.note("Not yet: Battle.net must be installed and closed."))
            if not ready:
                io.blank()
                io.say("I did not see the install finish. Run this shortcut again after "
                       "Battle.net is installed and closed.")
                return
            done.append("Battle.net is installed")
        io.screen("Switching to Battle.net", 3, STEPS, done)
        io.run("Updating Battle.net's Steam shortcut...",
               lambda: cli.cmd_repoint(env, cli.make_args(yes=True, start_steam=True)), PROGRESS)
        done.append("Steam opens the installed Battle.net")
        st = "sync"

    changed = add_games(env, io, cli, 4 if first_time else None, done)
    try:
        cli.cmd_desktop(env, cli.make_args(on_desktop=True), quiet=True)   # pick up the icon
    except BnetSteamError:
        pass
    if changed is None:
        return
    if first_time:
        io.screen("All done")
        io.good("Battle.net and your games are in your Steam library")
    if changed or first_time:
        io.blank()
        io.box(DONE)


def add_games(env, io, cli, step, done=()):
    """Teach, list, confirm and add. Returns the number of shortcuts changed, or None if the
    person said no."""
    if step:
        io.screen("Adding your games", step, STEPS, done)
    else:
        io.screen("Checking for new games")
    data = steam.load_shortcuts(env)
    _, prefix = cli.require_launcher(env, data)
    state = cli.load_state(env)
    for g in cli.unverified(prefix, bn.installed_games(prefix), state):
        teach(env, io, cli, state, prefix, g)

    plan = []
    heard = lambda kind, name: plan.append((kind, name))
    io.run("Looking for your Battle.net games...", lambda: cli.cmd_sync(
        env, cli.make_args(dry_run=True, learn=False), heard))
    new = [n for k, n in plan if k == "new"]
    changed = [n for k, n in plan if k == "update"]
    gone = [n for k, n in plan if k == "gone"]
    if not (new or changed or gone):
        io.good("Everything is up to date")
        return 0
    for title, names in (("Adding", new), ("Updating", changed)):
        if names:
            io.say(f"{title}:")
            for n in names:
                io.say("+ " + n if title == "Adding" else "~ " + n, indent=4)
    prune = False
    if gone:
        names = ", ".join(gone)
        prune = io.confirm(f"{names}: no longer installed. Remove the Steam shortcut? "
                           "(Your Battle.net install is not affected.)", default=False)
    if (new or changed) and not io.confirm("Add these to Steam now? Steam will close and reopen."):
        io.blank()
        io.say("Okay, nothing was changed.")
        return None
    count = io.run("Updating Steam...", lambda: cli.cmd_sync(
        env, cli.make_args(yes=True, start_steam=True, learn=False, prune=prune)), PROGRESS)
    io.good("Your Steam library is up to date")
    return count


def teach(env, io, cli, state, prefix, game):
    """A game whose start command we can only guess: watch the person press Play once."""
    io.screen(f"Teach me how to start {game['name']}")
    io.say(f"I cannot tell for sure how {game['name']} starts, and a guess might not work. "
           "If you show me once, I will remember.")
    io.blank()
    io.box([f"1. Start \"Battle.net\" from Steam, if it is not open.",
            f"2. Press Play on {game['name']} and let it reach its first screen.",
            "3. Quit the game."])
    io.blank()
    io.say("I will wait up to 3 minutes. Press Enter to skip; the shortcut will use my best guess.")
    io.blank()
    if io.run(f"Waiting for you to press Play on {game['name']}...",
              lambda: cli.learn_games(env, state, prefix, [game], 180,
                                      skip_on_enter=sys.stdin.isatty())):
        io.good(f"Got it: I will remember how to start {game['name']}")
    else:
        io.note(f"Skipped {game['name']}; its shortcut will use a guess")


def menu(env, io, cli, st, plan):
    """Offered once Battle.net has been set up before: carry on, take it away again, or quit.
    Returns "go" to carry on; anything else means this run is over."""
    io.screen("Welcome back")
    go = {"sync": "Check for new games", "install": "Set up Battle.net again"}.get(
        st, "Carry on setting up Battle.net")
    away = "Remove Battle.net from Steam..." if plan.shortcuts else \
        "Clean up what is left on this computer..."
    pick = io.choose("What would you like to do?", [go, away, "Quit"])
    if pick == 0:
        return "go"
    if pick == 1:
        remove(env, io, cli, plan)
    return "done"


def remove(env, io, cli, plan, running=None):
    running = running or battlenet_running
    io.screen("Remove Battle.net")
    size = io.run("Checking what is installed...",
                  lambda: cleanup.tree_size(env.work) if plan.data_exists else 0)
    if plan.shortcuts:
        io.say("In Steam: " + ", ".join(n for _, n in plan.shortcuts))
    if plan.data_exists:
        io.say(f"On this computer: Battle.net, your games and this program "
               f"({cleanup.human(size)})")
    io.blank()
    options = []
    if plan.shortcuts:
        io.say("Shortcuts only: Battle.net and your games stay on this computer, and you can "
               "add them back to Steam any time.")
        options.append("Remove the shortcuts from Steam only")
        io.blank()
    io.say("Everything: also deletes Battle.net, your games and their saved data, and this "
           "program. This cannot be undone.")
    options += ["Remove everything", "Go back"]
    pick = io.choose("What should I remove?", options, default=len(options) - 1)
    if pick == len(options) - 1:
        return
    everything = options[pick] == "Remove everything"
    if everything:
        if running():
            io.blank()
            io.box("Battle.net is still open. Close it, then run this again.")
            return
        if not io.confirm("Delete Battle.net, your games and their saved data for good? "
                          "Saves that are not stored online will be lost.", default=False):
            io.blank()
            io.say("Okay, nothing was changed.")
            return
    io.screen("Removing everything" if everything else "Removing from Steam")

    def work():
        count = cli.cmd_uninstall(env, cli.make_args(yes=True, everything=everything))
        if everything:
            io.log_path = None              # the data folder is gone; do not recreate it
        return count

    io.run("Removing..." if not plan.shortcuts else "Updating Steam...", work, PROGRESS)
    io.screen("All removed" if everything else "Removed from Steam")
    if plan.shortcuts:
        io.good("Battle.net and your games are gone from your Steam library")
    if everything:
        io.good("Battle.net, your games and this program are gone from this computer")
        io.blank()
        io.say("Thanks for trying it.")
    else:
        io.blank()
        io.say("Battle.net and your games are still on this computer. Double-click "
               "\"Battle.net for Steam\" any time to add them back.")
