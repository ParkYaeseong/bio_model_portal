import os
import sys
import tempfile
from pathlib import Path

# Backend root on sys.path so tests can `from app import ...`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Isolate tests from the production SQLite DB and storage tree. setdefault so an
# explicit env (or a test module that sets its own) still wins.
_TMP = tempfile.mkdtemp(prefix="wf_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("STORAGE_ROOT", _TMP)

import pytest


@pytest.fixture(autouse=True)
def _isolate_db():
    """Give every test a clean database. The whole session shares one temp
    SQLite file, so without this a test that leaves rows behind (e.g. a run in
    'running' state) pollutes tests that query globally."""
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield
