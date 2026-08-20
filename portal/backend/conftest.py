import os
import sys
import tempfile
from pathlib import Path

# Backend root on sys.path so tests can `from app import ...`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Isolate tests from the production SQLite DB and storage tree. setdefault so
# an already-set DATABASE_URL is honoured, but the _isolate_db fixture below
# refuses to run unless it points inside this session's temp directory.
_TMP = tempfile.mkdtemp(prefix="wf_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("STORAGE_ROOT", _TMP)
# These two must be set before any test module imports app.* (app/config.py
# caches get_settings() with @lru_cache, so whoever imports first wins) and
# must NOT honour an inherited value: a developer who has sourced the deployed
# .env would otherwise get these test-only security opt-ins from the shell
# instead of from the test suite. Plain assignment, not setdefault, on purpose.
# See the comment on KBF_ALLOW_INSECURE_SSO_HEADER below for what this means
# for the rest of the suite. Production leaves KBF_ALLOW_INSECURE_SSO_HEADER
# false and fails closed.
os.environ["KBF_FORWARD_AUTH_SECRET"] = ""
# Every test in this suite trusts the X-KBF-User header as if it came from the
# gateway. Tests that need to exercise fail-closed behaviour must patch
# auth.settings directly (see test_auth_identity.py) rather than change this
# environment variable, which no test module can do anyway (see above).
os.environ["KBF_ALLOW_INSECURE_SSO_HEADER"] = "true"

import pytest


@pytest.fixture(autouse=True)
def _isolate_db():
    """Give every test a clean database. The whole session shares one temp
    SQLite file, so without this a test that leaves rows behind (e.g. a run in
    'running' state) pollutes tests that query globally."""
    from app.database import Base, engine

    # This fixture empties every table before every test, so refuse to run
    # unless the engine really is pointing at this session's throwaway file.
    url = str(engine.url)
    assert url.startswith("sqlite:") and _TMP in url, (
        f"refusing to truncate tables in a non-throwaway database: {url}"
    )

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield
