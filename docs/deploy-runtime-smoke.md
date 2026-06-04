# Deploy-Runtime Smoke Checks

This doc covers the operator-runnable checks that confirm the
LEGION Dashboard container has everything it needs to create
per-task git worktrees after a fresh `docker compose build app`
+ `docker compose up -d app` cycle.

The dashboard cannot create worktrees on its own — `/srv/repo` is
mounted read-only — so it shells out to the in-image tool
`/usr/local/bin/legion-worktree-create`. That tool is the
repo-owned copy of `scripts/legion-worktree-create`. The
container also needs `/srv/worktrees` bind-mounted so the host
can see the resulting worktrees, and git must treat
`/srv/repo/legion-dashboard` as a safe directory even though
its host owner (root) differs from the in-container `app` user.

The checks below verify each of those pieces.

## 1. Host-side preflight (run BEFORE `docker compose build`)

Run on the host that owns the bind-mount source. This creates
`/srv/worktrees` if missing and confirms it is writable by the
docker user. Idempotent.

```bash
sudo /srv/repo/legion-dashboard/scripts/preflight-legion-worktrees.sh
```

Expected output:

```
[preflight] OK: /srv/worktrees is writable
```

If the script exits non-zero, fix the directory ownership
(`chown` to the user docker runs as) and re-run before
attempting to build the app image.

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
