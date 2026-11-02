# Battle.net for Steam

Add Battle.net and your Battle.net games to Steam on a Steam Deck or Steam Machine. Each game
appears in your Steam library like any other game.

> **Beta.** Tested on one SteamOS machine (native Steam, Proton 11) with StarCraft, Warcraft II,
> World of Warcraft Classic Era, and Diablo IV. This project is unofficial and isn't affiliated
> with Blizzard Entertainment or Valve. "Battle.net," game names, and artwork belong to their
> owners.

## Install

You need SteamOS in Desktop Mode, with Steam signed in at least once. To install:

1. Open Konsole: click Application Launcher, then search for Konsole.
2. Paste this line and press Enter. To paste, press and hold Control and Shift, and then press V. The
   [script](install.sh) is short, so you can read it first.

   ```sh
   curl -fsSL https://raw.githubusercontent.com/Wurby/better-steamos-battlenet/main/install.sh | bash
   ```

3. Double-click **Battle.net for Steam** on your desktop and follow the steps. It asks before it
   changes anything, and Steam closes and reopens a few times.

When Battle.net's installer opens, sign in, and then close Battle.net. In the installer's options,
you can deselect “Run Battle.net when you start your computer” and “Add a desktop icon.” Neither
is needed.

## Add games

Install the games in Battle.net (open **Battle.net** from your Steam library), and then close
Battle.net. Double-click **Battle.net for Steam** and choose **Check for new games**.

## Update

Paste the install line again. Your Battle.net installation, games, and settings stay in place.

## Remove

Double-click **Battle.net for Steam** and choose **Remove Battle.net from Steam**. You can remove
only the shortcuts, or everything, including Battle.net, your games, and their saved data. It
shows the size and asks before it deletes anything, and it removes only what it created.

In Konsole, run `~/.local/bin/bnet-steam uninstall`. Add `--everything` to remove everything.

## Troubleshooting

If something goes wrong, try the following:

- If you see “Steam did not close,” quit Steam (choose Steam > Exit) and try again.
- To undo the last change to Steam's shortcuts, run `~/.local/bin/bnet-steam restore --latest`.
- To see the detailed log, run `~/.local/bin/bnet-steam -v`.
- Find logs and backups in the bnet-steam folder inside .local/share in your home folder.

## Things to know

Keep these points in mind:

- World of Warcraft and Diablo IV start only from the Battle.net launcher. Their shortcuts open
  Battle.net; press Play there.
- Battle.net sees a generic hardware name instead of Valve hardware, so it shows its regular
  layout instead of the handheld layout. To turn this off, run `bnet-steam sync --no-handheld-fix`.
- It builds game art from Blizzard's images with ffmpeg, and it never replaces art you've already
  set.
- Games without a verified start command use a best guess. Run `bnet-steam learn` to record the
  real one.
- Anti-cheat games might not work under Proton, and running them there might put your account
  at risk. Check each game's support yourself.
- This project depends on undocumented Battle.net files that can change. Flatpak Steam isn't
  supported.

## How it works

The tool does the following:

- Runs Battle.net in its own Wine prefix, outside Steam's compatdata folder, so removing a
  shortcut never deletes your installation.
- Adds one Steam shortcut per installed game that starts the game itself, so Steam tracks
  playtime.
- Asks, closes Steam (it never force-quits), and backs up Steam's shortcut and configuration
  files before it edits them. It keeps the last 20 backups, writes changes atomically, and
  restores the backups if anything fails.

For details, see [AGENTS.md](AGENTS.md).

## Command line

Run `bnet-steam` for the guided setup, or `bnet-steam --help` to see every command, including
`status`, `sync`, `learn`, `restore`, and `uninstall`. Commands that change Steam accept
`--dry-run`.

To run from a clone instead, use `python3 -m bnet_steam`. It needs Python 3.9 or later and no
other packages. The installer also needs `curl` and `tar`. Artwork needs `ffmpeg`, which ships
with SteamOS.

## Development

```sh
python3 -m unittest discover -s tests -t . -b
```

See [AGENTS.md](AGENTS.md) for architecture and contributor rules, and [TODOS.md](TODOS.md) for
open work.

## License

[MIT](LICENSE). The license covers this project's code only. Blizzard's and Valve's names,
artwork, and trademarks belong to their owners. The tool downloads Blizzard's images to your
machine and doesn't redistribute them.
