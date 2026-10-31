# AGENTS.md

Source of truth for anyone (human or AI agent) working in this repo.
`CLAUDE.md` only points here; edit this file, not that one.

## What this is
`bnet-steam` adds Battle.net and each installed Battle.net game to Steam as non-Steam
shortcuts on Linux/SteamOS, running under Proton. It installs the launcher into its own
Wine prefix, then keeps one Steam shortcut per game in sync. Python 3, stdlib only.

## Layout
```
bnet_steam/
  vdf.py        binary VDF codec (shortcuts.vdf); text edit for config.vdf CompatToolMapping
  steam.py      Steam discovery, Proton detection, backups, atomic writes, write_session
  battlenet.py  product.db / aggregate.json parsing, launch specs, installer download
  art.py        default library art via ffmpeg; launcher icon
  learn.py      watch the launcher's Play button to record a game's real start command
  pe_icon.py    pull an exe's main icon out of its PE resources (pure Python)
  cli.py        commands and argument parsing
tests/          unittest suite; helpers.py builds a fake Steam + Battle.net prefix
```
Run: `python3 -m bnet_steam <command>`. Test: `python3 -m unittest discover -s tests -t . -b`.

## Hard rules
- **All Steam-file writes go through `steam.write_session`.** It confirms, closes Steam,
  backs up, gives validated atomic writes, rolls back on error, and restarts Steam.
  Do not write `shortcuts.vdf`/`config.vdf` any other way.
- Plans are computed read-only first and applied to a **fresh** read inside the session
  (Steam rewrites those files on exit).
- Never force-kill Steam. Never delete shortcuts, prefixes, or game files by default.
- Removing a non-Steam shortcut makes Steam delete `steamapps/compatdata/<appid>`. Our
  prefix lives in the data dir, outside `compatdata`. Keep it that way.
- For an existing shortcut, **read the stored appid**; never recompute it. Steam-UI-created
  shortcuts use a different appid scheme. Updates keep the appid.
- Do not overwrite user art or user-set shortcut fields (icon etc.).
- Cleanup (`sync --prune`) only removes shortcuts this tool created (tracked in `state.json`),
  never the launcher, and does nothing if Battle.net's own files look unreadable (a glitch
  would make every game look uninstalled). It is opt-in: asked per game on a terminal,
  `--prune` to do it unasked, report-only when scripted or `--dry-run`.
- Tests must never touch the real Steam dir (use `tests/helpers.FakeSteam`) or the network
  (`tests/__init__.py` disables `urlopen`; mock `_fetch` / `download_installer`).
- Do not commit personal data: Steam IDs, home paths, backups, API keys, `state.json`.
- Do not publish (push, post, release) without the owner's explicit say-so.
- Claims about what works must match what was tested; write "unverified" otherwise.

## Verified facts (on SteamOS, native Steam, Proton 11)
- `shortcuts.vdf` round-trips byte-exact through `vdf.py` on a real 19-entry file.
- Proton 11 internal compat-tool name is `proton_11` (from Steam's logs); `proton_9`,
  `proton_experimental`, `proton_hotfix` seen in config. Other numbered names follow the
  pattern but are unverified.
- Game start commands seen from the launcher's Play button:
  `StarCraft.exe -launch -uid s1` (x86_64 build) and `Warcraft II BNE.exe -uid w2`.
  The `-uid` value is the product **uid**, not the code (`w2` vs `w2bn`).
- In our tests `Battle.net.exe --exec="launch <code>"` only *selected* the game page and did
  not start it (tried on a cold launcher and sent to a running, logged-in one).
- StarCraft's exe, run through its Steam shortcut with the launcher closed, starts. (Not
  separately confirmed for other games.)
- `product.db` is protobuf: `ProductInstall{1 uid, 2 code, 3 settings{1 install_path}}`.
- Diablo IV (code and uid `fenris`) is started as
  `Diablo IV.exe -nostreamline -sso -launch -uid fenris`; Battle.net reports the 5 MB
  `Diablo IV Launcher.exe` stub as its exe, which is not the game. Recorded with `learn`;
  playing it from the Steam shortcut was not confirmed.
- World of Warcraft Classic Era (uid and code `wow_classic_era`) is started as
  `_classic_era_/WoWClassic.exe -launcherlogin -uid wow_classic_era -initialgamemode=cera`.
  The real exe is one folder below the install root (the root only has a launcher stub),
  and the process name's case (`WoWClassic.exe`) differs from the file on disk
  (`WowClassic.exe`): Wine ignores case, Linux may not, so lookups are case-insensitive and
  the shortcut uses the on-disk name. Recorded with `learn`; the game reached its login screen
  when started by the launcher. Starting it from the Steam shortcut was not confirmed.
- Battle.net lists a game in `aggregate.json` lazily; until it does there is no name or art URL.
  Blizzard's art CDN pattern `bnetxboxassets.akamaized.net/<code>/{box,logo}-enUS.webp` held for
  `s1`, `fenris`, `wow`, `wow_classic` but 404s for `wow_classic_era`; we try the code, then its
  parents (`wow_classic_era` -> `wow_classic` -> `wow`).
- Launch args cannot be derived from install metadata, so `sync` guides the user through
  pressing Play once for any game it can only guess at.
- The Battle.net logo is a 256x256 PNG-compressed icon inside `Battle.net.exe`.
- `Agent/aggregate.json` lists installed games: `product_id`, `name`, `icon_path`
  (primary exe, but the x86 build for StarCraft), `box_art_uri`, `logo_art_uri`.
- Steam keeps a game "running" until its whole launch session exits. After StarCraft's
  window closed, `StarCraft.exe` and `Agent.exe` stayed alive about 20 s, and Steam cleared
  ~3 s after the last one exited. It looks like the game's own shutdown; the cause was not
  isolated.

## Unverified / known gaps
- Art is created before the confirmation prompt (new files only, but a cancelled sync can
  still leave images behind). Fix: create art inside the write session, after confirmation.
- Derived default launch args (`-uid <uid>`) for any game other than the verified ones;
  Diablo IV proved the guess can be wrong, hence the guided `learn`.
- Games whose sign-in needs the launcher running (`-sso` hints at it); not yet tested.
- What Steam does on shortcut removal beyond deleting `compatdata/<appid>` (reported, not
  observed by us).
- Flatpak Steam (refused with a message), non-SteamOS distros, Python < 3.14 (tested on 3.14 only).
- SGDBoop URI behaviour (we only coexist with its output; we never call it).
- Blizzard endpoints (`downloader.battle.net`, `bnetxboxassets.akamaized.net`) are
  undocumented and may change.

## License
MIT, copyright "bnet-steam contributors". Contributions are accepted on the same terms. Do not add
Blizzard or Valve artwork or trademarks to the repo.

## Conventions
- Match the surrounding style; comments explain *why*, not *what*.
- Expected failures raise `BnetSteamError`; the CLI prints it and exits 1.
- A bug fix gets a test that fails without it. Mutate-check safety tests when changing them.
- Keep `README.md`, this file, and `TODOS.md` in step with behaviour changes.
