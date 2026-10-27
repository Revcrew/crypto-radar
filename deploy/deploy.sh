#!/usr/bin/env bash
# Deploy crypto-crime-radar to the VDS and verify file integrity via md5.
#
# Secrets (.env, proxy.env) are NEVER deployed from this repo — they are
# created directly on the VDS with mode 600.
#
# Usage: deploy.sh <user@host> [remote_dir]
set -euo pipefail

HOST="${1:?usage: deploy.sh <user@host> [remote_dir]}"
REMOTE_DIR="${2:-/opt/crypto-crime-radar}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$LOCAL_DIR"
FILES="radar tests README.md PLAN.md .env.example deploy/crypto-crime-radar.service deploy/proxy.env.example"

echo "== creating remote dir =="
ssh "$HOST" "mkdir -p '$REMOTE_DIR'"

echo "== copying files =="
# shellcheck disable=SC2086
tar cf - $FILES | ssh "$HOST" "tar xf - -C '$REMOTE_DIR'"

echo "== verifying md5 (local vs remote) =="
LOCAL_SUMS="$(mktemp)"
REMOTE_SUMS="$(mktemp)"
trap 'rm -f "$LOCAL_SUMS" "$REMOTE_SUMS"' EXIT
# shellcheck disable=SC2086
find $FILES -type f | sort | xargs md5sum > "$LOCAL_SUMS"
# shellcheck disable=SC2086
ssh "$HOST" "cd '$REMOTE_DIR' && find $FILES -type f | sort | xargs md5sum" > "$REMOTE_SUMS"

if diff <(awk '{print $1, $2}' "$LOCAL_SUMS") <(awk '{print $1, $2}' "$REMOTE_SUMS") > /dev/null; then
  echo "OK: remote files match local files"
else
  echo "ERROR: file mismatch between local and remote" >&2
  diff <(awk '{print $1, $2}' "$LOCAL_SUMS") <(awk '{print $1, $2}' "$REMOTE_SUMS") >&2 || true
  exit 1
fi

echo "== next (manual, phase 6) =="
echo "  1. place .env and proxy.env (mode 600) in $REMOTE_DIR"
echo "  2. sudo cp $REMOTE_DIR/deploy/crypto-crime-radar.service /etc/systemd/system/"
echo "  3. sudo systemctl daemon-reload && sudo systemctl enable --now crypto-crime-radar"
echo "  4. verify per TZ section 6 checklist"
