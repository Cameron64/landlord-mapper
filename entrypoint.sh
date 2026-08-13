#!/usr/bin/env bash
# Sync pipeline CODE from the image into the working directory before running.
#
# Why this exists: runs bind a named volume over /landlord_mapper_etl so the
# downloaded appraisal exports and the _targets store survive between runs.
# Docker seeds a named volume from the image only while the volume is empty, so
# from the second run onward the volume's copy of the code shadows whatever a
# rebuilt image contains -- the pipeline then executes stale sources with no
# warning at all. Copying the pristine code in on every start makes the image the
# single source of truth for code while leaving data untouched.
set -euo pipefail

SRC=/opt/pipeline-src
DEST=/landlord_mapper_etl

if [ -d "$SRC" ]; then
  changed=0
  for f in "$SRC"/*; do
    base=$(basename "$f")
    if ! cmp -s "$f" "$DEST/$base"; then
      cp -f "$f" "$DEST/$base"
      echo "[entrypoint] refreshed $base"
      changed=$((changed + 1))
    fi
  done
  echo "[entrypoint] code sync complete ($changed file(s) refreshed from image)"
else
  echo "[entrypoint] WARNING: $SRC missing, running whatever is in $DEST" >&2
fi

cd "$DEST"
exec "$@"
