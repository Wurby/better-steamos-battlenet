#!/bin/bash
# Run `bnet-steam sync` in a terminal window and keep it open to read the result.
python3 -I $HOME/bnet-steam/bnet_steam.py sync --start-steam
echo
read -r -p "Press Enter to close"
