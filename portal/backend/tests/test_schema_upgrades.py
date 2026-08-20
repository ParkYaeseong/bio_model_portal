"""The hand-rolled column upgrade that stands in for a migration tool."""

from sqlalchemy import create_engine, inspect, text

from app import database


def _legacy_users_engine(path):
    """A users table in the shape the production database is in today:
    no email, no display_name, and rows already in it."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL,"
            " password_hash VARCHAR(255) NOT NULL, created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text("INSERT INTO users (username, password_hash) VALUES ('sso:legacy', 'x')"))
    return engine


def test_adds_the_columns_to_a_legacy_users_table(tmp_path, monkeypatch):
    legacy = _legacy_users_engine(tmp_path / "legacy.db")
    monkeypatch.setattr(database, "engine", legacy)

    database.ensure_user_identity_columns()

    assert {"email", "display_name"} <= {c["name"] for c in inspect(legacy).get_columns("users")}


def test_running_it_again_changes_nothing_and_keeps_the_rows(tmp_path, monkeypatch):
    legacy = _legacy_users_engine(tmp_path / "legacy.db")
    monkeypatch.setattr(database, "engine", legacy)

    database.ensure_user_identity_columns()
    database.ensure_user_identity_columns()

    with legacy.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
        assert conn.execute(text("SELECT username FROM users")).scalar() == "sso:legacy"

    assert {"email", "display_name"} <= {c["name"] for c in inspect(legacy).get_columns("users")}


def test_does_nothing_when_the_table_does_not_exist_yet(tmp_path, monkeypatch):
    empty = create_engine(f"sqlite:///{tmp_path}/empty.db")
    monkeypatch.setattr(database, "engine", empty)

    database.ensure_user_identity_columns()  # must not raise

    assert "users" not in inspect(empty).get_table_names()
