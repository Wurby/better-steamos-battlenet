#!/usr/bin/env bash
# Battle.net for Steam: installer. It is short; read it before you run it if you like.
#
#   curl -fsSL https://raw.githubusercontent.com/Wurby/better-steamos-battlenet/main/install.sh | bash
#
# What it does: checks the basics, downloads the project into ~/.local/share/bnet-steam/app,
# and puts a "Battle.net for Steam" icon on your desktop and in your application menu. It does
# not touch Steam or install any games; the icon's guided setup does that, step by step, and
# asks before it changes anything. Run it again any time to update.
set -euo pipefail

URL="${BNET_STEAM_URL:-https://github.com/Wurby/better-steamos-battlenet/archive/refs/heads/main.tar.gz}"
DATA="${BNET_STEAM_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/bnet-steam}"
APP="$DATA/app"
PY="${BNET_STEAM_PYTHON:-python3}"

say()  { printf '%s\n' "$*"; }
fail() { printf '\nSorry, I could not finish.\n%s\n\n' "$*" >&2; exit 1; }

say "Battle.net for Steam: installing..."

command -v curl >/dev/null || fail "'curl' is missing, and I need it to download the project."
command -v tar  >/dev/null || fail "'tar' is missing, and I need it to unpack the project."
command -v "$PY" >/dev/null || fail "Python 3 is missing. On SteamOS it is normally built in."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null ||
    fail "Your Python is too old: I need Python 3.9 or newer."

if [ ! -d "$HOME/.local/share/Steam/userdata" ] && [ ! -d "$HOME/.steam/steam/userdata" ]; then
    if [ -d "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/userdata" ]; then
        fail "You seem to have the Flatpak version of Steam, which is not supported yet."
    fi
    fail "I cannot find Steam. Open Steam once and sign in, then run this again.
(You also need to be in Desktop Mode, not Game Mode.)"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
say "Downloading..."
curl -fsSL "$URL" -o "$tmp/src.tar.gz" ||
    fail "The download failed. Check your internet connection and try again."
mkdir "$tmp/src"
tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components=1 2>/dev/null ||
    fail "The download was not a valid archive. Try again in a few minutes."
[ -f "$tmp/src/bnet_steam/__init__.py" ] || fail "The download does not look like this project."

# swap the new version in only now that it is known to be good, so a failed update changes nothing
mkdir -p "$DATA" "$HOME/.local/bin"
rm -rf "$APP.new"
mv "$tmp/src" "$APP.new"
rm -rf "$APP"
mv "$APP.new" "$APP"

# Start the app from its own folder, whatever directory the user is in (running `python -m`
# would put the current directory first and could pick up some other "bnet_steam" folder).
RUN='import sys; sys.path.insert(0, sys.argv.pop(1)); from bnet_steam.cli import main; main()'
run_app() { "$PY" -c "$RUN" "$APP" "$@"; }

cat >"$HOME/.local/bin/bnet-steam" <<EOF
#!/bin/bash
exec $PY -c '$RUN' "$APP" "\$@"
EOF
chmod +x "$HOME/.local/bin/bnet-steam"

run_app desktop --on-desktop ||
    fail "I downloaded everything but could not create the desktop icon."

say ""
say "All set."
say ""
say "  Double-click \"Battle.net for Steam\" on your desktop (or find it in the application menu)."
say "  It walks you through the rest and asks before it changes anything."
say ""
if [ -t 1 ] && [ -r /dev/tty ]; then
    printf 'Open it now? [Y/n] '
    read -r answer </dev/tty || answer=n
    case "${answer:-y}" in
        [Nn]*) ;;
        *) run_app wizard </dev/tty ;;
    esac
fi
