#!/usr/bin/env bash
# Host-side preflight for the LEGION Dashboard worktree directory.
#
# The dashboard app container creates per-task git worktrees under
# /srv/worktrees/<repo-slug>/<task>/. Docker bind-mounts this
# directory into the container at the same path, so it must exist
# on the host AND be writable by whatever uid/gid the container's
# `app` user maps to through the bind mount.
#
# Run this on the host BEFORE `docker compose build app` (or
# before the first deploy on a new host). The script is
# idempotent: it creates /srv/worktrees if missing, tests
# writability, and fails clearly if not writable.
set -euo pipefail

WT_DIR="${LEGION_WORKTREES_DIR:-/srv/worktrees}"

if [ ! -d "$WT_DIR" ]; then
  echo "[preflight] creating $WT_DIR"
  mkdir -p "$WT_DIR"
fi

if ! touch "$WT_DIR/.preflight-write-test" 2>/dev/null; then
  echo "[preflight] FAIL: $WT_DIR is not writable by the current user (uid=$(id -u))"
  echo "[preflight] hint: ensure the docker daemon's user can write $WT_DIR,"
  echo "          or run this preflight as the docker user."
  exit 2
fi
rm -f "$WT_DIR/.preflight-write-test"

echo "[preflight] OK: $WT_DIR is writable"
