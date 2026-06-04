# Deploy-Runtime Smoke Checks

This doc covers the operator-runnable checks that confirm the
LEGION Dashboard container has everything it needs to create
per-task git worktrees after a fresh `docker compose build app`
+ `docker compose up -d app` cycle.

The dashboard shells out to the in-image tool
`/usr/local/bin/legion-worktree-create` (the repo-owned copy of
`scripts/legion-worktree-create`) to run `git worktree add`
inside the container. `/srv/repo` is bind-mounted read-write so
the call can write the per-worktree metadata it deposits under
`<shared_repo>/.git/worktrees/<name>/`, and `/srv/worktrees` is
bind-mounted so the host can see the resulting worktrees. Git
must also treat `/srv/repo/legion-dashboard` as a safe directory
even though its host owner (root) differs from the in-container
`app` user.

For the per-task worktree creation to actually succeed at
runtime, the container's app user (uid 999, gid 999) must be
able to write to BOTH paths:

1. `/srv/repo/<repo-slug>/.git/` — specifically `.git/worktrees/`
   where `git worktree add` deposits per-worktree metadata.
2. `/srv/worktrees/` — the parent of every per-task worktree.

The host-side preflight enforces both. The checks below verify
each piece.

## .git ownership requirement

A writable bind mount is necessary but not sufficient. The bind
mount preserves host ownership, so if `/srv/repo/legion-dashboard/.git`
on the host is owned by root with mode 0755, the container's
non-root app user still cannot create files under
`.git/worktrees/<name>/` and `git worktree add` fails with
`EACCES`.

The preflight (step 1 below) handles this. When run as root, it
`chown -R 999:999 /srv/repo/<slug>/.git` and `chmod g+w` the
top-level `.git/` so the app user can write. When not run as
root, it verifies the existing owner/mode lets uid 999 / gid 999
write AND traverse — POSIX requires both bits to create entries
inside a directory, so the mode-bit check requires write+execute
in the relevant scope (owner w+x, group w+x, or other w+x). A
`root:999 0775` configuration qualifies via group, even though
neither the owner uid nor the owner gid is 999. A
write-bit-only directory like `0666` is correctly rejected — it
would pass a naive write-bit check but `git worktree add` would
still fail.

The preflight also verifies the `.git/worktrees/` subdirectory
when it already exists. `git worktree add` writes new files
directly into that subdir, so an earlier root-owned worktree
operation that left the subdir mode-restricted would block the
in-container `git worktree add` even when the top-level `.git/`
check passes.

## 1. Host-side preflight (run BEFORE `docker compose build`)

Run on the host that owns the bind-mount source. This:

* creates `/srv/worktrees` if missing and confirms the container
  app user (uid 999, gid 999) can write to it;
* for each known source repo (`/srv/repo/legion-dashboard`,
  `/srv/repo/lgn-hub`), when run as root, `chown -R 999:999` its
  `.git/` directory and `chmod g+w` the top-level `.git/` so the
  container app user can create per-worktree metadata under
  `.git/worktrees/<name>/`;
* verifies each `.git/` is writable+traversable by uid 999 /
  gid 999 using a mode-bit check that requires both the write and
  execute bits (owner w+x, group w+x, or other w+x all qualify);
* verifies the `.git/worktrees/` subdirectory the same way when
  it already exists, since `git worktree add` writes directly
  under it;
* exits 0 on success, non-zero with a clear `FAIL: ...` line on
  unrecoverable failure.

Idempotent.

```bash
sudo /srv/repo/legion-dashboard/scripts/preflight-legion-worktrees.sh
```

Expected output (truncated; the `.git/worktrees` lines appear
only when that subdirectory already exists on the host):

```
[preflight] OK: /srv/worktrees is writable by app user (owner=999:999 mode=775)
[preflight] OK: /srv/repo/legion-dashboard/.git is writable by app user (owner=999:999 mode=775)
[preflight] OK: /srv/repo/legion-dashboard/.git/worktrees is writable by app user (owner=999:999 mode=775)
[preflight] OK: all checks passed; the container app user can create per-task worktrees
```

If the script exits non-zero, follow the `FAIL: ...` hint and
re-run before attempting to build the app image.

## 2. Container-internal smoke (run AFTER `docker compose up -d app`)

Run on the host after the app container is up. This shells into
the `app` service and confirms every prerequisite the worktree
tool depends on. Exits 0 on success and prints `OK`.

```bash
docker compose exec -T app bash -lc '
  set -e
  test -n "$LEGION_WORKTREE_CREATE_TOOL" || (echo "FAIL: LEGION_WORKTREE_CREATE_TOOL unset"; exit 1)
  test -x "$LEGION_WORKTREE_CREATE_TOOL" || (echo "FAIL: $LEGION_WORKTREE_CREATE_TOOL not executable"; exit 1)
  "$LEGION_WORKTREE_CREATE_TOOL" --help >/dev/null || (echo "FAIL: tool --help failed"; exit 1)
  test -d /srv/repo/legion-dashboard || (echo "FAIL: /srv/repo/legion-dashboard missing"; exit 1)
  test -d /srv/worktrees || (echo "FAIL: /srv/worktrees missing"; exit 1)
  test -w /srv/worktrees || (echo "FAIL: /srv/worktrees not writable"; exit 1)
  git -C /srv/repo/legion-dashboard rev-parse --show-toplevel >/dev/null || (echo "FAIL: git dubious ownership or repo inaccessible"; exit 1)
  echo OK
'
```

Each check, briefly:

| Check                                          | Verifies                                                |
| ---------------------------------------------- | ------------------------------------------------------- |
| `LEGION_WORKTREE_CREATE_TOOL` is set           | `docker-compose.yml` env var made it into the container |
| Tool is executable                             | `Dockerfile` `COPY` + `chmod 0755` ran                  |
| `--help` returns 0                             | Tool's Python shebang resolves and the script parses    |
| `/srv/repo/legion-dashboard` exists            | The `:ro` shared-repo bind mount is wired               |
| `/srv/worktrees` exists                        | The read-write worktrees bind mount is wired            |
| `/srv/worktrees` is writable                   | The container's `app` user can create per-task worktrees |
| `git rev-parse --show-toplevel` succeeds       | `git config --system --add safe.directory` applied      |

A clean run prints `OK` and exits 0. Any failure prints a
`FAIL: ...` line naming the missing prerequisite — fix that
specific item and re-run.

## 3. End-to-end: Start Build on a Work Item

After steps 1 and 2 pass, click `Start Build` on a Work Item in
the dashboard. The router calls `ensure_task_worktree`, which
shells out to `/usr/local/bin/legion-worktree-create` and
creates the worktree under `/srv/worktrees/legion-dashboard/...`.
The Kanban card body should reference that path verbatim.

If `Start Build` returns a 409 with
`blocker_code=blocked_worktree_create_failed`, re-run step 2 —
one of the prerequisites was not actually satisfied at runtime.
