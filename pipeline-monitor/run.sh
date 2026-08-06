#!/usr/bin/env bash
# Start the pipeline monitor on the box (Ubuntu, bash, python3 at /usr/bin/python3).
#
# Preferred posture: a one-time `setfacl` grant on the lm_work volume so the monitor
# reads unprivileged. `sudo -n` reads are the documented fallback, not the default.
# See pipeline-monitor/PLAN.md §3 for the full rationale.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOLUME_ROOT="/media/cam/ImageProcessing/docker/volumes/lm_work/_data"

LM_MONITOR_PORT="${LM_MONITOR_PORT:-8098}"
LM_MONITOR_HOST="${LM_MONITOR_HOST:-0.0.0.0}"

usage() {
  cat <<'EOF'
Usage: ./run.sh [--print-mcp-config]

  (no args)             Check the ACL grant, then start the monitor in the
                         foreground: python3 -m monitor.server

  --print-mcp-config    Print the .mcp.json block for the Windows laptop MCP
                         client and exit. Does not check the ACL or start
                         anything.
EOF
}

print_mcp_config() {
  # Windows laptop side: interpreter is `python`, not `python3`.
  cat <<EOF
{
  "mcpServers": {
    "lm-pipeline": {
      "command": "python",
      "args": ["-m", "lm_mcp.mcp_server"],
      "cwd": "<abs path to>/landlord-mapper-fork/pipeline-monitor",
      "env": { "LM_MONITOR_URL": "http://cam-cloudripper.local:${LM_MONITOR_PORT}" }
    }
  }
}
EOF
}

# Checks that `cam` has the DEFAULT ACL entry on the volume root, not merely that
# today's files happen to be readable. An access-only ACL (missing -d) looks
# identical to the working configuration until `targets` recreates
# `_targets/meta/progress` or drops a new `owner_data_part_<pid>.csv` — both of
# which are brand-new root-owned inodes that only a DEFAULT ACL propagates to.
# A read-only check against files that exist right now cannot tell the two apart.
check_acl() {
  if ! command -v getfacl >/dev/null 2>&1; then
    echo "getfacl not found. Install acl (apt install acl) to use the unprivileged posture," >&2
    echo "or rely on the sudo -n fallback (this script does not check that path)." >&2
    return 1
  fi

  # Look for a default-ACL entry granting `cam` r-x on the volume root.
  if getfacl -p "$VOLUME_ROOT" 2>/dev/null | grep -q '^default:user:cam:r-x$'; then
    return 0
  fi

  cat >&2 <<EOF
The default ACL grant for 'cam' on the lm_work volume is missing.

Without it, the monitor breaks the moment a real run starts: 'targets'
recreates _targets/meta/progress as a new inode at run start, and every
owner_data_part_<pid>.csv is a brand-new root-created file mid-scrape.
Neither inherits an access-only ACL. An access-only grant would look fine
against files that exist right now and then fail on the next real run.

Run this once as Cam (needs sudo), then re-run ./run.sh:

    V=${VOLUME_ROOT}
    sudo setfacl -R    -m u:cam:rX "\$V" "\$V/_targets"   # existing files
    sudo setfacl -R -d -m u:cam:rX "\$V" "\$V/_targets"   # DEFAULT ACL: inherited by NEW files

This script will not silently escalate to sudo reads in its place.
EOF
  return 1
}

main() {
  if [[ "${1:-}" == "--print-mcp-config" ]]; then
    print_mcp_config
    exit 0
  fi
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  if ! check_acl; then
    exit 1
  fi

  export LM_MONITOR_PORT
  export LM_MONITOR_HOST

  cd "$SCRIPT_DIR"
  exec /usr/bin/python3 -m monitor.server
}

main "$@"
