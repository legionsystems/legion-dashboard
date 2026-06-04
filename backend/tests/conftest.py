import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"

os.environ["DATABASE_URL"] = SQLALCHEMY_DATABASE_URL
os.environ["LEGION_SKIP_SEED"] = "1"

# The orchestrator resolves LEGION_WORKTREE_CREATE_TOOL at import
# time and falls back to the in-image path
# /usr/local/bin/legion-worktree-create. On the dev host the same
# tool also lives at a legacy operator path. Tests that exercise
# the real ensure_task_worktree (rather than stubbing
# _worktree_create_result) need a real executable on disk; if the
# in-image path is not present, fall back to the legacy operator
# path before importing the app so the orchestrator captures a
# working tool path. Honors any externally-set override (CI,
# container) by checking the var first.
if not os.environ.get("LEGION_WORKTREE_CREATE_TOOL"):
    for _candidate in (
        "/usr/local/bin/legion-worktree-create",
        "/root/.hermes/LEGION_TOOLS/bin/legion-worktree-create",
    ):
        if os.path.isfile(_candidate):
            os.environ["LEGION_WORKTREE_CREATE_TOOL"] = _candidate
            break

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db_engine():
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture()
def client(db_engine, db_session_factory):
    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def db_session(client, db_session_factory):
    """Test-engine session for direct DB seeding inside tests."""
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()
