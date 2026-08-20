from urllib.parse import quote

from app import auth, models
from app.database import SessionLocal


def test_sso_login_stores_email_and_name():
    with SessionLocal() as db:
        user = auth.provision_sso_user(db, "sub-identity-1", "a@b.c", "Ada Lovelace")
        assert user.email == "a@b.c"
        assert user.display_name == "Ada Lovelace"


def test_sso_login_percent_decodes_the_headers():
    with SessionLocal() as db:
        user = auth.provision_sso_user(db, "sub-identity-2", quote("a@b.c"), quote("홍길동"))
        assert user.email == "a@b.c"
        assert user.display_name == "홍길동"


def test_sso_login_updates_a_changed_name():
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-3", "old@b.c", "Old Name")
        user = auth.provision_sso_user(db, "sub-identity-3", "new@b.c", "New Name")
        assert user.email == "new@b.c"
        assert user.display_name == "New Name"
        assert db.query(models.User).filter_by(username="sso:sub-identity-3").count() == 1


def test_missing_headers_do_not_wipe_stored_identity():
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-4", "keep@b.c", "Keep Me")
        user = auth.provision_sso_user(db, "sub-identity-4", None, None)
        assert user.email == "keep@b.c"
        assert user.display_name == "Keep Me"


def test_a_blank_header_does_not_wipe_a_stored_value():
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-5", "keep@b.c", "Keep Me")
        user = auth.provision_sso_user(db, "sub-identity-5", "   ", "%20")
        assert user.email == "keep@b.c"
        assert user.display_name == "Keep Me"


def test_an_unchanged_login_does_not_commit_again():
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-6", "same@b.c", "Same Name")

        commits = 0
        original_commit = db.commit

        def counting_commit():
            nonlocal commits
            commits += 1
            original_commit()

        db.commit = counting_commit
        user = auth.provision_sso_user(db, "sub-identity-6", "same@b.c", "Same Name")

        assert commits == 0, "an unchanged login must not write"
        assert user.email == "same@b.c"
        assert user.display_name == "Same Name"


def test_a_non_string_header_sentinel_is_ignored():
    # get_current_user called outside FastAPI's dependency injection hands the
    # Header(...) default straight through; it must never reach the database.
    from fastapi import Header

    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-7", "real@b.c", "Real Name")
        user = auth.provision_sso_user(
            db, "sub-identity-7", Header(default=None), Header(default=None)
        )
        assert user.email == "real@b.c"
        assert user.display_name == "Real Name"
