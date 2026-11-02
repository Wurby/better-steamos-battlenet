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
  ui.py         terminal screens (progress, boxes, spinner); hides command output unless -v
  cleanup.py    uninstall: plan what we created, data-folder safety checks, deletion
  wizard.py     guided setup for non-technical users: what the desktop icon runs
  wrapper.py    run-game.sh: the launch-option wrapper every shortcut runs through (verb swap)
  cli.py        commands and argument parsing
install.sh      one-line installer (curl | bash): downloads the repo, adds the desktop icon
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
- Proton's `waitforexitandrun` verb (what Steam uses) first runs `wineserver -w`, i.e. waits for
  EVERY Wine process in the prefix to exit, then runs the game (see the `proton` script). With
  the Battle.net launcher open in the same prefix the game would never start. Seen directly:
  the wrapper prototype hung for 200 s until killed. `run-game.sh` swaps the verb to `run` when a
  launcher is up.
- Games that start with `-launcherlogin` (WoW Classic Era) or `-sso` (Diablo IV) cannot be started
  by us. When the launcher starts a game its log shows `LaunchBinary` then `Setting launch env var
  BDK_LAUNCH_GUID to <fresh GUID>` plus account launch options; the game presents that GUID to
  prove the launch. Started directly (launcher closed, or open and logged in with the wrapper
  having started it) WoW sends an SSO login and the server replies `BattleNet External Challenge`
  -> `Glue Fatal Error 2147483956` -> 'unable to connect, please log in again'. Ruled out: the
  launcher merely running. The GUID is per launch; we cannot mint or replay it.
- Nothing we tried makes the launcher press Play: `--exec="launch WoWC"`, `--exec="launch
  wow_classic_era"` and `battlenet://WoWC` each only logged `Selecting game family by id: ...`
  (never `LaunchBinary`). The launcher selects games by *family id* (`WoWC`, `S1`, `Pro`), which
  only appears in cached server data and in the launcher log when the user presses Play.
- `%command%` in a Steam launch option expands to the WHOLE launch chain
  (`steam-launch-wrapper -- reaper SteamLaunch ... -- _v2-entry-point ... proton waitforexitandrun
  exe`), so a wrapper runs on the host, outside Steam's runtime container, and anything it
  starts (e.g. a launcher via `proton run`) runs there too; that launcher's window rendered
  broken (the launcher started by Steam's own runtime for a shortcut does not). Steam's environment also has `LD_PRELOAD` of a 32-bit overlay library, which makes every
  spawned process slower and noisy: the first wrapper forked per process and took ~20 s per game
  start. Keep wrapper scripts to a handful of spawns.
- Battle.net's own log (`AppData/Local/Battle.net/Logs/battle.net-*.log`, CRLF line endings)
  records `GSStateTracker State changed to: Ready` once logged in and loaded (~7-15 s).
- `proton run taskkill /F /IM Battle.net.exe` closes the launcher cleanly.
- Run against the real launcher with a stand-in game, `run-game.sh --login` started the launcher,
  saw Ready after 7 s, swapped the verb, ran the game and closed the launcher (11 s total). That
  mechanism works but does not make login games work (see above), so the `--login` mode was removed.
- Confirmed by the owner on SteamOS: StarCraft starts from its shortcut with the launcher open
  (the wrapper logged the verb swap), and the launcher started by a shortcut renders normally.
  On Valve hardware it opened on a "handheld" games tab that hides games like WoW Classic Era (its
  log: `Handheld Detected: T ... Baseboard Manufacturer: Valve`, user-agent `(retail;handheld)`).
- Handheld mode: `--force-handheld` is a command-line switch that can only force it ON
  (`=false` / `=0` still logged `Handheld Detected: T`). Detection reads the registry values
  `HKLM\Hardware\Description\System\BIOS\BaseBoardManufacturer` and `BaseBoardProduct`, which Wine
  rebuilds from SMBIOS at every wineserver start. Setting them (`reg add`) in the SAME Wine session
  just before the launcher starts gave `Handheld Detected: F` and user-agent `(retail)`; verified
  with real values (a real board name) and with "To Be Filled By O.E.M.". Started from a Steam
  shortcut through `run-game.sh --launcher-fix` -> `cmd.exe /c C:\bnet-steam\launcher.bat`, the
  launcher logged `Handheld Detected: F`, reached Ready, and logged no rendering errors.
  Do not claim to be a real vendor's hardware; the launcher reports board details in telemetry.
  Confirmed on screen by the owner: the launcher then opens on its normal Home page (and
  `--exec="launch <family>"` does not navigate to the game's page).
- `run-game.sh --launcher-fix` rewrites the launcher exe argument in the Steam command into
  `cmd.exe /c C:\bnet-steam\launcher.bat <args>`, so shortcuts keep pointing at `Battle.net.exe`
  (our detection of the launcher shortcut depends on that) and Steam's runtime chain is untouched.
- Games whose start command signs in through the launcher get a shortcut that runs the launcher
  (like the Battle.net shortcut) with `--exec="launch <family id>"`; family ids (`WoWC`, `Fen`,
  `W2`, `S1`) are read from `Selecting game family by id: X` / `LaunchBinary: uid=Y` pairs in the
  launcher's logs, so a game played once is known. Started from Steam this way the launcher
  reached `Ready` in ~5 s, selected `WoWC`, and logged no rendering errors; that the window
  looks right and Play then works was not confirmed by us.
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
- `run-game.sh` inside Steam's runtime container: whether it sees the launcher's processes, and
  whether a game started in the container shares a Wine server with a launcher the wrapper
  started outside it. Its log (`<data dir>/logs/run-game.log`) records argv, container and
  process visibility to answer this.
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

## Install flow (non-technical users)
`install.sh` -> `~/.local/share/bnet-steam/app` + `~/.local/bin/bnet-steam` + a desktop icon whose
script runs `wizard`. The wizard works out the stage (install / waiting for the Battle.net installer /
switch the shortcut / add games), does the next step, and waits until Battle.net is installed AND
closed before it switches the shortcut, because that restarts Steam and would kill a running
installer. One screen per step (`ui.Terminal`); the commands' own printed log is captured, saved to
`logs/wizard-last.log` and shown only with `-v`, so wizard code must never rely on a command's
stdout being visible or on `input()` inside a captured run (pass `yes=True`; ask via `io.confirm`).
Output stays plain when not a terminal or `NO_COLOR` is set. Entry points must not depend on the current directory (`python -m` puts cwd first on
`sys.path`), so they insert the app folder explicitly. `install.sh` validates a download before it
replaces the installed copy and never touches the data dir (prefix, backups, state). Keep it short
and readable: users are told to read it.

## Uninstall
`uninstall` removes only what we can show we created: the launcher shortcut and the game shortcuts
in `state.json`, their Steam config entries (via `write_session`, like every Steam edit), and with
`--everything` their art, the data folder, our command and icons. Never the user's other
shortcuts or art. `check_data_dir` refuses home (or a parent), `/`, anything overlapping the Steam
folder, or a folder with none of our marker files, and each rule has its own test (mutation
checked: put a marker in the bad folder so the marker rule can't be what stops it). Deletion
happens only after the Steam edit succeeded, never while Battle.net is running, and the wizard
must not write into the data folder afterwards (it clears `io.log_path`). The icon's menu appears
once a setup has happened (a shortcut, a prefix or `state.json`). Tests must use `fake_home` so
nothing real is read or deleted.

## Conventions
- Docs and user-facing text follow the Apple Style Guide; where it has no rule, the IBM style
  (for example, sentence-style headings, simple present tense). Notable Apple rules: "choose" for
  menu items, "deselect" not "untick", "press and hold" not "hold down", code font only for what
  the user types (not folder names), a colon-ended lead-in before every list, active voice.
- Match the surrounding style; comments explain *why*, not *what*.
- Expected failures raise `BnetSteamError`; the CLI prints it and exits 1.
- A bug fix gets a test that fails without it. Mutate-check safety tests when changing them.
- Keep `README.md`, this file, and `TODOS.md` in step with behaviour changes.
