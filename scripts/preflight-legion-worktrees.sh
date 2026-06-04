#!/usr/bin/env bash
# Host-side preflight for the LEGION Dashboard per-task worktrees.
#
# For the dashboard's per-task worktree creation to work in a fresh
# `docker compose build app && docker compose up -d app` cycle on
# this host, the container's app user (uid 999, gid 999) must be
# able to write to BOTH:
#
#   1. /srv/repo/<repo-slug>/.git/  — specifically the
#      .git/worktrees/ metadata directory that ``git worktree add``
#      creates per worktree. Without write access here the call
#      fails with EACCES and ``Start Build`` 409s before the
#      orchestrator can surface the real error.
#
#   2. /srv/worktrees/  — the parent of every per-task worktree
#      the dashboard creates. The container bind-mounts this
#      directory at the same path, so the in-container app user
#      must be able to create new subdirectories here.
#
# Git's safe.directory configuration for /srv/repo/legion-dashboard
# is the third leg; that one is set in the Dockerfile so it does
# not need a host-side preflight.
#
# This script verifies (1) and (2) and, when run as root, fixes
# them in place. Idempotent. Run on the host BEFORE
# `docker compose build app` (or before the first deploy on a new
# host).
set -euo pipefail

WT_DIR="${LEGION_WORKTREES_DIR:-/srv/worktrees}"

# Source repos whose .git/ must be writable by the container's app
# user. Both repos run through ``git worktree add`` from inside
# the dashboard container (legion-dashboard via the dashboard
# orchestrator; lgn-hub via the same orchestrator with a hub
# target_app).
REPO_GIT_DIRS=(
  "/srv/repo/legion-dashboard/.git"
  "/srv/repo/lgn-hub/.git"
)

# The uid/gid the app container runs as (see Dockerfile: app:app).
APP_UID=999
APP_GID=999

# Return 0 if the app user (uid=$APP_UID, gid=$APP_GID) can both
# WRITE and TRAVERSE $1. On POSIX, the write bit alone is not
# sufficient to create entries inside a directory — the matching
# execute bit is also required (write to mutate the directory entry
# table, execute to traverse and look up names). A path with mode
# 0666 is "writable" by a write-bit-only check but cannot accept
# ``mkdir`` calls; for the dashboard's per-worktree creation we
# need both, so this helper requires write+execute in the scope
# that matches the app user's uid/gid:
#   - other w+x set                  -> anyone can write+traverse
#   - owner is APP_UID, owner w+x    -> app user as owner
#   - group is APP_GID, group w+x    -> app user via group
# A directory like root:999 mode 0775 is therefore accepted; the
# 0666 / 0644 "writable on paper but unusable in practice" shapes
# are correctly rejected.
_writable_traversable() {
  local path="$1"
  local owner_uid owner_gid mode mode_oct
  owner_uid=$(stat -c '%u' "$path")
  owner_gid=$(stat -c '%g' "$path")
  mode=$(stat -c '%a' "$path")
  # ``8#`` forces the integer literal to be interpreted as octal so
  # bash's bitwise tests below see the same bits ``stat -c %a``
  # reports.
  mode_oct=$((8#${mode}))
  # Other w+x: app user can write+traverse regardless of ownership.
  if (( (mode_oct & 0003) == 0003 )); then
    return 0
  fi
  # Owner is the app uid AND owner w+x set.
  if [ "$owner_uid" = "$APP_UID" ] && (( (mode_oct & 0300) == 0300 )); then
    return 0
  fi
  # Group is the app gid AND group w+x set. The common
  # "root:999 0775" case satisfies this clause.
  if [ "$owner_gid" = "$APP_GID" ] && (( (mode_oct & 0030) == 0030 )); then
    return 0
  fi
  return 1
}

# Print a stable owner/mode summary for the operator log.
_describe() {
  local path="$1"
  printf 'owner=%s:%s mode=%s' \
    "$(stat -c '%u' "$path")" \
    "$(stat -c '%g' "$path")" \
    "$(stat -c '%a' "$path")"
}

# --- (2) /srv/worktrees ---------------------------------------------------

if [ ! -d "$WT_DIR" ]; then
  echo "[preflight] creating $WT_DIR"
  mkdir -p "$WT_DIR"
fi

# When running as root, normalise ownership/mode so the bind mount
# is writable by the in-container app user. A directory created by
# sudo/root with mode 0755 is NOT writable by uid 999, and the
# downstream worktree-create call would fail at container runtime.
if [ "$(id -u)" -eq 0 ]; then
  echo "[preflight] running as root: chown $WT_DIR to ${APP_UID}:${APP_GID} (container app user) and chmod 0775"
  chown "${APP_UID}:${APP_GID}" "$WT_DIR"
  chmod 0775 "$WT_DIR"
fi

if ! _writable_traversable "$WT_DIR"; then
  echo "[preflight] FAIL: $WT_DIR is not writable by the container app user (uid=$APP_UID gid=$APP_GID); $(_describe "$WT_DIR")"
  echo "          Re-run this preflight as root, or chown manually so the"
  echo "          app user can write — e.g."
  echo "             sudo chown ${APP_UID}:${APP_GID} $WT_DIR && sudo chmod 0775 $WT_DIR"
  exit 2
fi
echo "[preflight] OK: $WT_DIR is writable by app user ($(_describe "$WT_DIR"))"

# --- (1) /srv/repo/<slug>/.git --------------------------------------------

# git worktree add writes new files under .git/worktrees/<name>/.
# The mount is now rw on the app service (see docker-compose.yml),
# but the bind mount preserves host ownership: if the source
# repo's .git is owned by host root the in-container app user
# still cannot create files inside it. When running as root, fix
# the ownership in place; otherwise verify the existing mode/owner
# is one the app user can write to.
git_fail=0
for git_dir in "${REPO_GIT_DIRS[@]}"; do
  if [ ! -d "$git_dir" ]; then
    echo "[preflight] skip: $git_dir (no such directory on this host)"
    continue
  fi
  if [ "$(id -u)" -eq 0 ]; then
    echo "[preflight] running as root: chown -R $git_dir to ${APP_UID}:${APP_GID} (container app user)"
    chown -R "${APP_UID}:${APP_GID}" "$git_dir"
    # Ensure the top-level .git is group-writable so the app user
    # can create .git/worktrees/ if it does not exist yet.
    chmod g+w "$git_dir"
  fi
  if ! _writable_traversable "$git_dir"; then
    echo "[preflight] FAIL: $git_dir is not writable by the container app user (uid=$APP_UID gid=$APP_GID); $(_describe "$git_dir")"
    echo "          Without write access here, ``git worktree add`` in the dashboard"
    echo "          container cannot create .git/worktrees/<name>/ and Start Build will"
    echo "          409 with blocked_worktree_create_failed. Re-run this preflight as"
    echo "          root, or chown manually:"
    echo "             sudo chown -R ${APP_UID}:${APP_GID} $git_dir"
    git_fail=1
    continue
  fi
  echo "[preflight] OK: $git_dir is writable by app user ($(_describe "$git_dir"))"

  # P2: when .git/worktrees already exists, ``git worktree add``
  # writes new files DIRECTLY into it. Even if the top-level
  # .git is writable+traversable, a mode-restricted or
  # wrong-owner worktrees/ subdir blocks creation (the recursive
  # chown above normalises ownership; this check is the
  # non-root verification path). Skipped silently when the
  # subdir does not yet exist — git creates it on first use,
  # inheriting the (now-corrected) parent ownership.
  git_worktrees_dir="${git_dir}/worktrees"
  if [ -d "$git_worktrees_dir" ]; then
    if ! _writable_traversable "$git_worktrees_dir"; then
      echo "[preflight] FAIL: $git_worktrees_dir is not writable by the container app user (uid=$APP_UID gid=$APP_GID); $(_describe "$git_worktrees_dir")"
      echo "          ``git worktree add`` writes per-worktree metadata directly"
      echo "          under .git/worktrees/<name>/. With this subdir restricted, the"
      echo "          top-level .git writability check passes but the worktree"
      echo "          create still fails. Re-run this preflight as root, or chown"
      echo "          manually:"
      echo "             sudo chown -R ${APP_UID}:${APP_GID} $git_worktrees_dir"
      git_fail=1
      continue
    fi
    echo "[preflight] OK: $git_worktrees_dir is writable by app user ($(_describe "$git_worktrees_dir"))"
  fi
done

if [ "$git_fail" -ne 0 ]; then
  exit 4
fi

echo "[preflight] OK: all checks passed; the container app user can create per-task worktrees"
