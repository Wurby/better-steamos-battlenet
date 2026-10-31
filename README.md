# bnet-steam

Add **Battle.net** and each Battle.net game you install to Steam as separate
non-Steam shortcuts on Linux / SteamOS, running under Proton.

> **Beta.** Developed and tested on one SteamOS machine (native Steam, Proton 11,
> StarCraft and Warcraft II). Read [Limitations](#limitations) before relying on it.
>
> Unofficial. Not affiliated with or endorsed by Blizzard Entertainment or Valve.
> "Battle.net", game names and artwork belong to their owners.

## What it does
1. Downloads the Battle.net installer and adds it to Steam with its **own Wine prefix**
   (outside Steam's `compatdata`, so removing a shortcut can never delete your install).
2. After you run the installer from Steam, repoints that shortcut at the installed launcher.
3. Reads Battle.net's own files to find the games you have installed and keeps **one Steam
   shortcut per game**, each launching the real game executable so Steam tracks playtime
   and the Stop button works.
4. Adds default library art built from Blizzard's own images (needs `ffmpeg`).

## Requirements
- Linux with the **native** Steam client (Flatpak Steam is not supported yet), a Proton
  version installed, and Python 3.9+. No third-party Python packages.
- `ffmpeg` for artwork (optional; ships with SteamOS).
- Run from Desktop Mode. Steam is closed and restarted whenever shortcuts change.

## Quick start
```sh
git clone <this repo> && cd bnet-steam
python3 -m bnet_steam status            # see what was detected
python3 -m bnet_steam install           # adds the installer shortcut
# start "Battle.net" from Steam, run the installer, sign in, then:
python3 -m bnet_steam repoint           # switch the shortcut to the launcher
# install some games in Battle.net, then:
python3 -m bnet_steam sync              # add a shortcut for each installed game
python3 -m bnet_steam desktop --on-desktop   # optional: a "Battle.net Sync" icon
```
Every command that changes Steam supports `--dry-run`.

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
| `sync` | Add/update a shortcut per installed game (`--no-art`, `--proton-log`) |
| `learn` | Press Play on a game in the launcher once; records how it is started |
| `launch` | Start the Battle.net shortcut through Steam |
| `restore` | List or restore backups of Steam's files |
| `desktop` | Add an app-menu entry (and optional desktop icon) that runs `sync` |

Global options: `--steam-root`, `--steam-user`, `--data-dir`.

## How games are launched
Battle.net starts a game as `<game exe> -uid <uid>` (StarCraft also needs `-launch`).
`sync` uses a launch spec in this order: **learned** (via `learn`), **verified** (built in),
then a **derived default** of `-uid <uid>` using the exe Battle.net reports. Games with no
usable exe fall back to asking the launcher to open them. `sync` labels each game with which
one it used.

## Artwork
Blizzard only provides a square key-art tile and a small icon, so portrait, wide and hero
images are built from the tile over a blurred copy of itself. Art that already exists for a
shortcut, in any common format (for example applied with [SGDBoop](https://www.steamgriddb.com/boop)),
is never replaced, so you can pick better art on steamgriddb.com and it will stick.

## Limitations
- Verified games: **StarCraft** and **Warcraft II: Battle.net Edition**. Other games use the
  derived default, which is an extrapolation and may need `learn`.
- Depends on undocumented Battle.net files and Blizzard URLs that can change.
- Native Steam only; one Wine prefix; x86-64 Linux/SteamOS tested only.
- Anti-cheat titles may not work under Proton, and running them there may carry account risk.
  Check each game's support status yourself.
- The Battle.net launcher shortcut has no default art.

## Development
```sh
python3 -m unittest discover -s tests -t . -b
```
See [AGENTS.md](AGENTS.md) for architecture and rules for contributors, and
[TODOS.md](TODOS.md) for open work.

## License
Not yet chosen.
