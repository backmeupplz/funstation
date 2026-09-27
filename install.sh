#!/bin/bash
# Run from your computer: pushes pi/ to the funstation and runs setup there. Re-run after any change.
# Usage: ./install.sh [ssh-host]   (defaults to FUN_HOST)
set -euo pipefail
cd "$(dirname "$0")"
. ./config.env
HOST=${1:-${FUN_HOST:-funstation}}
tar -C pi -c . | ssh "$HOST" 'rm -rf /tmp/funstation && mkdir /tmp/funstation && tar -C /tmp/funstation -x'
scp -q config.env "$HOST:/tmp/funstation/funstation.env"
ssh "$HOST" 'sudo bash /tmp/funstation/setup.sh'
# ROMs + saves: only copies files the Pi doesn't have yet, so saves made on the Pi are never overwritten
[ -n "${LOCAL_ROMS:-}" ] && tar -C "$LOCAL_ROMS" -c . | ssh "$HOST" "mkdir -p '$ROMS_DIR' && tar -C '$ROMS_DIR' -x --skip-old-files"
echo "installed on $HOST"
