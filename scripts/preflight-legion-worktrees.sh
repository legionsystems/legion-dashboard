#!/usr/bin/env bash
# Host-side preflight for the LEGION Dashboard worktree directory.
#
# The dashboard app container creates per-task git worktrees under
# /srv/worktrees/<repo-slug>/<task>/. Docker bind-mounts this
# directory into the container at the same path, so it must exist
# on the host AND be writable by the uid/gid the container's `app`
# user maps to through the bind mount. The Dockerfile creates an
# `app:app` user with uid 999 / gid 999; the bind mount preserves
# host ownership, so the host directory must be owned by 999:999
# (or world-writable) for the container's app user to create task
# directories in it.
#
# Run this on the host BEFORE `docker compose build app` (or
# before the first deploy on a new host). The script is
# idempotent: it creates /srv/worktrees if missing, fixes
# ownership to 999:999 when run as root, verifies writability,
# and fails clearly otherwise.
set -euo pipefail

WT_DIR="${LEGION_WORKTREES_DIR:-/srv/worktrees}"

# The uid/gid the app container runs as (see Dockerfile: app:app).
APP_UID=999
APP_GID=999

if [ ! -d "$WT_DIR" ]; then
  echo "[preflight] creating $WT_DIR"
  mkdir -p "$WT_DIR"
fi

# When running as root, fix ownership to the container's app
# uid/gid so the bind mount is writable by the in-container user.
# A directory created by sudo/root with mode 0755 is NOT writable
# by uid 999, and `touch` below would pass (because root can write
# anywhere) but the container would still fail at runtime.
if [ "$(id -u)" -eq 0 ]; then
  echo "[preflight] running as root: chown $WT_DIR to ${APP_UID}:${APP_GID} (container app user) and chmod 0775"
  chown "${APP_UID}:${APP_GID}" "$WT_DIR"
  chmod 0775 "$WT_DIR"
fi

if ! touch "$WT_DIR/.preflight-write-test" 2>/dev/null; then
  echo "[preflight] FAIL: $WT_DIR is not writable by the current user (uid=$(id -u))"
  echo "[preflight] hint: ensure $WT_DIR is owned by ${APP_UID}:${APP_GID}"
  echo "          (the container's app user), or run this preflight as root."
  exit 2
fi
rm -f "$WT_DIR/.preflight-write-test"

# Additional verification: explicitly check the directory is owned
# by ${APP_UID}:${APP_GID} (or is world-writable). A root-owned
# directory with mode 0755 will pass the touch above but fail at
# container runtime — this guard catches that case.
owner_uid=$(stat -c '%u' "$WT_DIR")
owner_gid=$(stat -c '%g' "$WT_DIR")
dir_mode=$(stat -c '%a' "$WT_DIR")
if [ "$owner_uid" != "$APP_UID" ] || [ "$owner_gid" != "$APP_GID" ]; then
  # Not owned by app:app. Only OK if world-writable (e.g. 0777).
  case "$dir_mode" in
    *7) : ;;  # mode ends in 7 -> world-writable
    *)
      echo "[preflight] FAIL: $WT_DIR is owned by ${owner_uid}:${owner_gid} mode ${dir_mode}"
      echo "          but the container's app user is uid=${APP_UID} gid=${APP_GID}."
      echo "          Re-run this preflight as root, or chown manually:"
      echo "             sudo chown ${APP_UID}:${APP_GID} $WT_DIR && sudo chmod 0775 $WT_DIR"
      exit 3
      ;;
  esac
fi

echo "[preflight] OK: $WT_DIR is writable (owner=${owner_uid}:${owner_gid} mode=${dir_mode})"
