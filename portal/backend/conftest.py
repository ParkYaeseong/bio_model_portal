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
