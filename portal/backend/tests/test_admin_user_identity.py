from urllib.parse import quote

# NOTE: the app.database / app.auth imports below are deliberately deferred
# into each test function rather than hoisted to module level. This file
# collects alphabetically before tests/test_auth_identity.py, which relies on
# being the FIRST module to import app.database so its own DATABASE_URL
# override (set before that import) wins. A module-level import here would
# import app.database during collection before test_auth_identity.py gets a
# chance to set its env var, silently repointing every test at the wrong
# throwaway DB. Deferring avoids that ordering trap without touching the
# other file.


def test_alter_table_helper_is_safe_to_run_twice():
    from sqlalchemy import inspect

    from app import models  # noqa: F401 - registers the users table on Base.metadata
    from app.database import Base, engine, ensure_added_columns

    Base.metadata.create_all(bind=engine)
    ensure_added_columns()
    ensure_added_columns()

    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"email", "display_name"} <= columns


def test_sso_login_stores_email_and_name():
    from app import auth
    from app.database import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = auth.provision_sso_user(db, "sub-identity-1", "a@b.c", "Ada Lovelace")
        assert user.email == "a@b.c"
        assert user.display_name == "Ada Lovelace"


def test_sso_login_percent_decodes_the_headers():
    from app import auth
    from app.database import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = auth.provision_sso_user(db, "sub-identity-2", quote("a@b.c"), quote("홍길동"))
        assert user.email == "a@b.c"
        assert user.display_name == "홍길동"


def test_sso_login_updates_a_changed_name():
    from app import models, auth
    from app.database import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-3", "old@b.c", "Old Name")
        user = auth.provision_sso_user(db, "sub-identity-3", "new@b.c", "New Name")
        assert user.email == "new@b.c"
        assert user.display_name == "New Name"
        assert db.query(models.User).filter_by(username="sso:sub-identity-3").count() == 1


def test_missing_headers_do_not_wipe_stored_identity():
    from app import auth
    from app.database import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-4", "keep@b.c", "Keep Me")
        user = auth.provision_sso_user(db, "sub-identity-4", None, None)
        assert user.email == "keep@b.c"
        assert user.display_name == "Keep Me"
