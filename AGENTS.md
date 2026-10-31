# AGENTS.md

Source of truth for anyone (human or AI agent) working in this repo.
`CLAUDE.md` only points here.

## What this is
`bnet-steam` adds Battle.net and each installed Battle.net game to Steam as
non-Steam shortcuts on Linux/SteamOS, running under Proton. It installs the
launcher into its own Wine prefix, then keeps one Steam shortcut per game in sync.

## Status
Prototype state: a single script, `bnet_steam.py`, proven on one SteamOS
machine with StarCraft and Warcraft II. See `TODOS.md` for the open work.

## Hard rules
- Steam files (`shortcuts.vdf`, `config.vdf`) may only be edited with Steam closed,
  after a backup. Never kill Steam; ask it to shut down and wait.
- Removing a non-Steam shortcut makes Steam delete `steamapps/compatdata/<appid>`.
  Keep our Wine prefix outside `compatdata` and never delete shortcuts by default.
- Do not commit personal data: Steam IDs, home paths, backups, API keys, state files.
- Do not publish (push, post, release) without the owner's explicit say-so.
- Claims about what works must match what was actually tested; say "unverified" otherwise.
