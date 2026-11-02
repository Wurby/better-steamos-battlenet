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

# colour and tick marks only on a real terminal; plain words otherwise
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    B=$'\033[1;38;5;39m'; G=$'\033[38;5;78m'; D=$'\033[38;5;245m'; R=$'\033[38;5;203m'; N=$'\033[0m'
    TICK="${G}✓${N}"
else
    B=""; G=""; D=""; R=""; N=""; TICK="OK"
fi
say()  { printf '  %s\n' "$*"; }
step() { printf '  %s %s\n' "$TICK" "$*"; }
fail() { printf '\n  %sSorry, I could not finish.%s\n' "$R" "$N" >&2
         printf '%s\n' "$*" | sed 's/^/  /' >&2; printf '\n' >&2; exit 1; }

printf '\n  %sBattle.net for Steam%s\n  %sInstalling...%s\n\n' "$B" "$N" "$D" "$N"

command -v curl >/dev/null || fail "'curl' is missing, and I need it to download the project."
command -v tar  >/dev/null || fail "'tar' is missing, and I need it to unpack the project."
command -v "$PY" >/dev/null || fail "Python 3 is missing. On SteamOS it is normally built in."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null ||
    fail "Your Python is too old: I need Python 3.9 or newer."
step "Your system has what I need"

if [ ! -d "$HOME/.local/share/Steam/userdata" ] && [ ! -d "$HOME/.steam/steam/userdata" ]; then
    if [ -d "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/userdata" ]; then
        fail "You seem to have the Flatpak version of Steam, which is not supported yet."
    fi
    fail "I cannot find Steam. Open Steam once and sign in, then run this again.
(You also need to be in Desktop Mode, not Game Mode.)"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$URL" -o "$tmp/src.tar.gz" ||
    fail "The download failed. Check your internet connection and try again."
mkdir "$tmp/src"
tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components=1 2>/dev/null ||
    fail "The download was not a valid archive. Try again in a few minutes."
[ -f "$tmp/src/bnet_steam/__init__.py" ] || fail "The download does not look like this project."
step "Downloaded"

# swap the new version in only now that it is known to be good, so a failed update changes nothing
mkdir -p "$DATA" "$HOME/.local/bin"
rm -rf "$APP.new"
mv "$tmp/src" "$APP.new"
rm -rf "$APP"
mv "$APP.new" "$APP"
step "Installed"

# Start the app from its own folder, whatever directory the user is in (running `python -m`
# would put the current directory first and could pick up some other "bnet_steam" folder).
RUN='import sys; sys.path.insert(0, sys.argv.pop(1)); from bnet_steam.cli import main; main()'
run_app() { "$PY" -c "$RUN" "$APP" "$@"; }

cat >"$HOME/.local/bin/bnet-steam" <<EOF
#!/bin/bash
exec $PY -c '$RUN' "$APP" "\$@"
EOF
chmod +x "$HOME/.local/bin/bnet-steam"

run_app desktop --on-desktop >/dev/null ||
    fail "I downloaded everything but could not create the desktop icon."
step "Added the \"Battle.net for Steam\" icon to your desktop"

printf '\n'
say "All set. Double-click \"Battle.net for Steam\" on your desktop (or find it in the"
say "application menu). It walks you through the rest and asks before it changes anything."
printf '\n'
if [ -t 1 ] && [ -r /dev/tty ]; then
    printf '  %sOpen it now? [Y/n]%s ' "$B" "$N"
    read -r answer </dev/tty || answer=n
    case "${answer:-y}" in
        [Nn]*) ;;
        *) run_app wizard </dev/tty ;;
    esac
fi
