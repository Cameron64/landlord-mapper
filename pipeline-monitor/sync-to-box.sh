#!/usr/bin/env bash
# Rsync pipeline-monitor/ to the box. Dry-run by default.
#
# Structural rule this script exists to enforce (PLAN.md §0, §1): this project
# never touches web/ or the lm_work volume. That is enforced here as a visible
# guard, not left as a convention someone has to remember.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOX_HOST="${LM_BOX_HOST:-cam@cam-cloudripper.local}"
BOX_DEST="${LM_BOX_DEST:-~/landlord-mapper-fork/pipeline-monitor}"
SSH_KEY="${LM_SSH_KEY:-$HOME/.ssh/id_ed25519_cloudripper}"

APPLY=0

usage() {
  cat <<'EOF'
Usage: ./sync-to-box.sh [--apply]

  (no args)   Dry run only (rsync -n). Shows what would transfer, changes nothing.
  --apply     Actually transfer the files.

Env overrides:
  LM_BOX_HOST   default: cam@cam-cloudripper.local
  LM_BOX_DEST   default: ~/landlord-mapper-fork/pipeline-monitor
  LM_SSH_KEY    default: ~/.ssh/id_ed25519_cloudripper
EOF
}

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 1 ;;
  esac
done

# --- Guard: this script may only ever sync pipeline-monitor/, to pipeline-monitor/. ---
# Checked every run, not just at write time, so a future edit to SRC/DEST can't
# silently widen the blast radius to web/ or the lm_work volume.
SRC="$SCRIPT_DIR/"

case "$SRC" in
  */pipeline-monitor/) ;;
  *)
    echo "Refusing to sync: source dir does not end in pipeline-monitor/ ($SRC)" >&2
    exit 1
    ;;
esac

case "$SRC" in
  */web/*|*/lm_work/*)
    echo "Refusing to sync: source path passes through web/ or lm_work/ ($SRC)" >&2
    exit 1
    ;;
esac

case "$BOX_DEST" in
  *web*|*lm_work*)
    echo "Refusing to sync: destination path mentions web/ or lm_work ($BOX_DEST)" >&2
    exit 1
    ;;
esac

RSYNC_ARGS=(
  -av
  --delete
  -e "ssh -i $SSH_KEY"
  # Never let a local venv/cache/git dir leak onto the box.
  --exclude ".git/"
  --exclude "__pycache__/"
  --exclude "*.pyc"
  --exclude ".pytest_cache/"
)

if [[ "$APPLY" -eq 0 ]]; then
  RSYNC_ARGS+=(-n)
  echo "Dry run (pass --apply to actually transfer):"
fi

echo "Syncing: $SRC -> $BOX_HOST:$BOX_DEST"
rsync "${RSYNC_ARGS[@]}" "$SRC" "$BOX_HOST:$BOX_DEST"
