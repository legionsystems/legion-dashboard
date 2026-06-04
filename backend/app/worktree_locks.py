"""Per-work-item in-process locks for ``_create_builder_task``.

The LEGION Dashboard is a single-operator local control plane: one
human operator plus automated worker/agent processes. The
worktree-isolated build path allocates a per-task worktree
under ``/srv/worktrees/`` and a per-attempt feature branch
keyed on ``builder_task.id``. That alone prevents two builds
from colliding on the same worktree path/branch even under
concurrent worker retries (the autoincrement id is unique).

The remaining correctness concern is duplicate Hermes tasks:
two concurrent ``start-build`` calls for the same work item
can both pass the active-task check before either commits the
BuilderTask row. Each then allocates a distinct worktree path
(and therefore a distinct ``repo_path`` lock target) and
creates a separate Hermes builder task for the same work
item.

This module provides a tiny in-process serialiser: a
``threading.Lock`` per ``work_item_id``. It is held only for
the duration of the active-task check + the BuilderTask stub
insert; the lock is released before the slow operations
(``ensure_task_worktree``, the safety check, the Hermes
``POST``) run. By the time the stub is committed, a second
concurrent call will see the row and return 409.

This is in-process only. For multi-process correctness the
committed BuilderTask row is the source of truth (the
active-task filter ``hermes_status NOT IN ['archived',
'done']``); a second process that races past the Python
lock still cannot create a duplicate Hermes task because the
uniqueness of the ``BuilderTask.id`` autoincrement + the
active-task check on a committed row provide the same
guarantee at the DB level.
"""
from __future__ import annotations

import threading
from typing import Dict


# Module-level lock table. We never evict entries because the
# set of work item ids is bounded by the DB and the entries
# are tiny (one threading.Lock each). For a single-operator
# local control plane this is fine; for a multi-user SaaS
# product a different design (e.g. a TTL'd LRU or
# ``threading.Lock`` keyed on a hashed user id) would be
# required.
_LOCKS: Dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def acquire_work_item_lock(work_item_id: int) -> threading.Lock:
    """Return the per-work-item lock, creating it on first use.

    The caller must call ``lock.release()`` exactly once.
    Using a context manager is recommended::

        with acquire_work_item_lock(work_item_id):
            ...
    """
    with _LOCKS_GUARD:
        lock = _LOCKS.get(work_item_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[work_item_id] = lock
        return lock
