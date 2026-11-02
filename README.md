# Battle.net for Steam

Play your Battle.net games from Steam on a Steam Deck or Steam Machine: Battle.net gets set up for
you, and every game you install shows up in your Steam library like any other game.

> **Beta.** Developed and tested on one SteamOS machine (native Steam, Proton 11, StarCraft,
> Warcraft II, and the launcher-only games World of Warcraft Classic Era and Diablo IV). Read
> [Limitations](#limitations) before relying on it.
>
> Unofficial. Not affiliated with or endorsed by Blizzard Entertainment or Valve.
> "Battle.net", game names and artwork belong to their owners.

## Quick start

**You need:** a Steam Deck or Steam Machine running SteamOS, in **Desktop Mode** (not Game
Mode), with Steam signed in at least once.

**1. Open Konsole.** It is the terminal: open the Application Launcher (bottom-left) and type
`Konsole`.

**2. Paste this one line and press Enter.** (Paste with `Ctrl+Shift+V`, or right-click and choose
Paste. On a Deck, `Steam+X` opens the on-screen keyboard.)

```sh
curl -fsSL https://raw.githubusercontent.com/Wurby/better-steamos-battlenet/main/install.sh | bash
```

That downloads this project and puts a **Battle.net for Steam** icon on your desktop. It does not
change Steam or install anything else. (It is a short script. [Read it first](install.sh) if you
like.)

**3. Double-click "Battle.net for Steam".** A window walks you through the rest and asks before
it changes anything:

1. It adds Battle.net to Steam. Steam closes and reopens.
2. In Steam, open your Library, find **Battle.net** (under "Non-Steam" if you cannot see it) and
   press **Play**. Click through the Battle.net installer and sign in, then close Battle.net.
   (Recommended, not required: untick "Run Battle.net when you start your computer" and "Add a
   desktop icon" in the installer's options. You do not need either.)
   The window notices and carries on by itself.
3. It switches the shortcut over to the installed launcher (Steam closes and reopens again) and
   adds any games you have installed.

**Adding games later:** install them in Battle.net (start **Battle.net** from Steam), close it,
then double-click **Battle.net for Steam** again. It adds new games and offers to remove the ones
you have uninstalled.

**Two things to know**
- Some games (World of Warcraft, Diablo IV) can only be started by the Battle.net launcher itself.
  Their shortcut opens Battle.net; press **Play** there.
- Battle.net sees a generic "To Be Filled By O.E.M." board instead of your Valve hardware, so it
  shows its normal layout instead of the cut-down handheld one. [Details](#the-handheld-ui).

**If something goes wrong**
- *"Steam did not close":* close Steam yourself (Steam menu, then Exit) and double-click the icon again.
- *Undo the last change to Steam's shortcuts:* in Konsole run `~/.local/bin/bnet-steam restore --latest`.
- Logs and backups are in `~/.local/share/bnet-steam/`.

**Updating:** paste the same line from step 2 again. Your Battle.net install, games and settings
are kept.

**Removing it:** double-click **Battle.net for Steam** and choose **Remove Battle.net from
Steam**. You can remove just the shortcuts (Battle.net and your games stay on the computer, and you
can add them back later), or everything: Battle.net, the games you downloaded and their saved
data, and this program. It tells you the size and asks first. It only removes what it created and
leaves your other Steam shortcuts and files alone. If you already removed the shortcuts in Steam
yourself, the same menu offers to clean up what is left. From a terminal:
`~/.local/bin/bnet-steam uninstall` (shortcuts only) or `... uninstall --everything`.

## What it does
1. Downloads the Battle.net installer and adds it to Steam with its **own Wine prefix**
   (outside Steam's `compatdata`, so removing a shortcut can never delete your install).
2. After you run the installer from Steam, repoints that shortcut at the installed launcher.
3. Reads Battle.net's own files to find the games you have installed and keeps **one Steam
   shortcut per game**, each launching the real game executable so Steam tracks playtime
   and the Stop button works.
4. Adds default library art built from Blizzard's own images (needs `ffmpeg`), and gives the
   Battle.net shortcut its real logo, read from `Battle.net.exe`.
5. Notices games you have uninstalled and offers to remove their shortcuts.

## For tinkerers
The Quick start installs everything under `~/.local/share/bnet-steam/app` and adds a
`bnet-steam` command. You can also run it straight from a clone:

```sh
git clone https://github.com/Wurby/better-steamos-battlenet.git && cd better-steamos-battlenet
python3 -m bnet_steam status            # see what was detected
python3 -m bnet_steam wizard            # the guided setup the desktop icon runs (add -v for the detailed log)
# or step by step:
python3 -m bnet_steam install           # adds the installer shortcut
# start "Battle.net" from Steam, run the installer, sign in, close it, then:
python3 -m bnet_steam repoint           # switch the shortcut to the launcher
# install some games in Battle.net, then:
python3 -m bnet_steam sync              # add a shortcut for each installed game
python3 -m bnet_steam desktop --on-desktop   # (re)create the desktop icon
```
Every command that changes Steam supports `--dry-run`.

**Requirements:** Linux with the **native** Steam client (Flatpak Steam is not supported yet), a
Proton version installed, Python 3.9+ (no third-party packages), `curl` and `tar` for the
installer, and `ffmpeg` for artwork (optional; ships with SteamOS). Run from Desktop Mode: Steam
is closed and restarted whenever shortcuts change.

## Safety
Anything that edits `shortcuts.vdf` or `config.vdf`:
- asks for confirmation (or `--yes`) before doing anything;
- asks Steam to exit and waits for it. It never force-kills Steam, and aborts without
  changes if Steam will not close;
- backs up both files to `~/.local/share/bnet-steam/backups/` (last 20 kept);
- re-reads the files after Steam has closed, validates its edit, and replaces the
  files atomically;
- restores the backups automatically if anything fails;
- refuses to touch a `shortcuts.vdf` that it cannot reproduce byte for byte.

It never deletes shortcuts, prefixes or game files. Undo the last change with
`python3 -m bnet_steam restore --latest` (`restore` with no arguments lists backups).

## Commands
| Command | Purpose |
|---|---|
| `status` | Show what was detected and how far setup has got (read-only) |
| `download` | Fetch a fresh Battle.net installer |
| `install` | Add the installer shortcut and prefix (`--proton NAME`, `--proton-log`) |
| `repoint` | Point the shortcut at the installed launcher |
| `sync` | Add/update a shortcut per installed game; asks before removing shortcuts of uninstalled ones (`--prune`/`--no-prune`, `--learn`/`--no-learn`, `--no-art`, `--proton-log`) |
| `learn` | Press Play on a game in the launcher once; records how it is started (`--game CODE`) |
| `launch` | Start the Battle.net shortcut through Steam |
| `restore` | List or restore backups of Steam's files |
| `uninstall` | Remove the shortcuts it made from Steam; `--everything` also deletes Battle.net, your games and saves, and this program (`--dry-run` shows what) |
| `wizard` | Guided setup: works out the next step and does it (what the desktop icon runs) |
| `desktop` | Add an app-menu entry (and optional desktop icon) for the guided setup |

Global options: `--steam-root`, `--steam-user`, `--data-dir`.

## How games are launched
Battle.net starts a game as `<game exe> -uid <uid>` (StarCraft also needs `-launch`).
Start commands can't be read from install metadata (Diablo IV, for example, needs
`-nostreamline -sso -launch -uid fenris`). `sync` uses a launch spec in this order:
**learned**, **verified** (built in), then a **derived default** of `-uid <uid>` on the game's
main exe (helper executables such as launcher stubs are skipped). For a game it can only
guess at, an interactive `sync` pauses and asks you to press Play once in the Battle.net
launcher, then records the exact command (Enter skips; scripted runs never prompt). Games
with no usable exe fall back to asking the launcher to open them. `sync` labels each game
with which one it used.

## Games that sign in through the launcher
World of Warcraft Classic Era and Diablo IV (their start commands contain `-launcherlogin` /
`-sso`) cannot be started by a shortcut directly. When the launcher starts such a game it mints
a one-time token (`BDK_LAUNCH_GUID`) and hands it to the game, which uses it to sign in. A game
started any other way, even with the launcher open and logged in, falls back to its own login
and the server answers with an extra verification challenge ("unable to connect"). Nothing we
tried makes the launcher press Play for you (`--exec="launch ..."` and `battlenet://` links only
select the game).

So for these games the shortcut **opens the Battle.net launcher on that game** (using the
launcher's own game id, read from its logs), and you press Play. Games without those flags
(StarCraft, Warcraft II) start straight from their shortcuts.

## The handheld UI
On Valve hardware the launcher decides it is on a handheld (from the board manufacturer and
product that Wine reports) and switches to a cut-down handheld UI whose Games tab hides most games.
`--force-handheld` can only force that mode *on*, so instead the Battle.net shortcut (and the
shortcuts that open the launcher) start it through a tiny batch file, `launcher.bat` in the Wine
prefix, that sets those two registry values to "To Be Filled By O.E.M." (the placeholder many boards
report) for that session only, just before the launcher starts. Wine rebuilds the real values at
every start, so nothing persists, and your real hardware is untouched. The launcher (and its
telemetry) will see a generic board instead of Valve hardware. Turn it off with
`sync --no-handheld-fix`.

## The run-game.sh wrapper
Every shortcut runs through `run-game.sh` (with `--launcher-fix` for the ones that start the
launcher, see above). Proton's `waitforexitandrun` verb (which Steam uses)
first waits for every Wine process in the prefix to exit, so an open launcher would stop a game
from ever starting; when a launcher is running the wrapper uses Proton's `run` verb instead.
With no launcher running it adds about 20 ms and changes nothing. Its log is
`~/.local/share/bnet-steam/logs/run-game.log`.

## Artwork
Blizzard only provides a square key-art tile and a small icon, so portrait, wide and hero
images are built from the tile over a blurred copy of itself. If Battle.net has not listed a
game yet, the art of its parent product is used (WoW Classic Era borrows WoW Classic's).
The Battle.net shortcut gets the launcher's own logo on a gradient, read from your install. Art that already exists for a
shortcut, in any common format (for example applied with [SGDBoop](https://www.steamgriddb.com/boop)),
is never replaced, so you can pick better art on steamgriddb.com and it will stick.

## Limitations
- Verified games: **StarCraft** and **Warcraft II: Battle.net Edition**. Other games use the
  derived default, which is an extrapolation and may need `learn`.
- Depends on undocumented Battle.net files and Blizzard URLs that can change.
- Native Steam only; one Wine prefix; x86-64 Linux/SteamOS tested only.
- Anti-cheat titles may not work under Proton, and running them there may carry account risk.
  Check each game's support status yourself.
- Battle.net lists new games lazily, so a just-installed game may show a rough name and
  parent-product art until the next `sync` after Battle.net catches up (names then update in place).
- Games that sign in through the launcher (see above) need you to press Play in the launcher.
- The handheld fix changes the hardware identity the launcher sees (see above).
- The launcher opens on its Home page; shortcuts that open it select the game but you still pick
  it from your library.

## Development
```sh
python3 -m unittest discover -s tests -t . -b
```
See [AGENTS.md](AGENTS.md) for architecture and rules for contributors, and
[TODOS.md](TODOS.md) for open work.

## License
[MIT](LICENSE). This covers this project's code only. "Battle.net", Blizzard's game names
and artwork, and Valve's trademarks belong to their owners; the tool downloads Blizzard's
images onto your own machine and never redistributes them.
