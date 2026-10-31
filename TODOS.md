# TODOs

## Before going public
- [x] Repo with AGENTS.md / CLAUDE.md / TODOS.md
- [x] Safety pass: confirm prompt, backups, restore command, atomic writes, rollback,
      strict round-trip check, tests (64, mutation-checked)
- [x] Guided `learn` during `sync`, real-game-exe heuristic, cleanup (`--prune`), launcher icon
- [x] Generalization pass: Steam root/user detection, Proton detection, XDG data dir,
      generated desktop entries (no hard-coded paths)
- [x] License: MIT, "bnet-steam contributors" (the owner wants to hand the project to the community)
- [ ] Set a real git identity and rewrite author on the history (currently a placeholder)
- [ ] Audit the history for personal data before any push
- [ ] Test on a second machine and a second Steam user
- [ ] Test the minimum Python version we claim (3.9); only 3.14 has been run
- [ ] Check prior art (BoilR, Steam ROM Manager, Lutris) before claiming novelty
- [ ] CI that runs the unit tests

- [ ] Create art inside the write session, after the user confirms (currently before)
- [ ] Confirm WoW Classic Era starts from its Steam shortcut (the launcher reaches the login
      screen with the same command; the shortcut itself is untested)

## Later
- [ ] Flatpak Steam support
- [ ] Verify launch arguments for more Blizzard games; grow `VERIFIED`
- [ ] Optional SteamGridDB API key for portrait/hero/logo art (incl. the launcher shortcut)
- [ ] After `--prune`, also remove art we generated and the compat-tool mapping for the removed
      shortcut (currently left behind; harmless)
- [ ] Confirm Diablo IV starts from its Steam shortcut with the launcher closed; if sign-in
      needs the launcher, add a wrapper that starts it first
- [ ] Games left on disk after Battle.net uninstalls them are ignored; offer to report them
- [ ] Handle Battle.net updates that change `product.db` layout
- [ ] Screenshots / short demo for the README
