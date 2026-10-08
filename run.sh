#!/bin/bash
# Created: WIB 2026-10-08 22:4x — one-command launcher (my-hl)
# ./run.sh           -> testnet on port 8770 (UI + API + chain)
# PORT=9000 ./run.sh -> custom port
cd "$(dirname "$0")"
echo "starting my-hl testnet..."
PORT=${PORT:-8770} python3 run_server.py
