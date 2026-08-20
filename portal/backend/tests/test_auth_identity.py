import unittest

from fastapi import HTTPException  # noqa: E402

from app import auth, models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402

Base.metadata.create_all(bind=engine)


class HeaderIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()

    def tearDown(self) -> None:
        self.db.close()

    def test_provisions_user_from_sso_header(self) -> None:
        user = auth.get_current_user(
            db=self.db, token=None, x_kbf_user="sub-abc", x_kbf_email="a@b.c", x_kbf_name="A"
        )
        self.assertIsNotNone(user.id)
        self.assertIn("sub-abc", user.username)
        # password login must be impossible for SSO-provisioned accounts
        self.assertFalse(auth.verify_password("", user.password_hash))

    def test_same_sub_reuses_user(self) -> None:
        u1 = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-xyz")
        u2 = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-xyz")
        self.assertEqual(u1.id, u2.id)

    def test_different_subs_are_isolated(self) -> None:
        u1 = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-1")
        u2 = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-2")
        self.assertNotEqual(u1.id, u2.id)

    def test_header_takes_precedence_over_jwt(self) -> None:
        # A stale shared-account JWT must not override the verified SSO header.
        local = models.User(username="kbfportal", password_hash=auth.hash_password("pw"))
        self.db.add(local)
        self.db.commit()
        self.db.refresh(local)
        token = auth.create_access_token({"sub": "kbfportal"})
        user = auth.get_current_user(db=self.db, token=token, x_kbf_user="sub-real")
        self.assertIn("sub-real", user.username)
        self.assertNotEqual(user.id, local.id)

    def test_no_header_no_token_rejected(self) -> None:
        with self.assertRaises(HTTPException):
            auth.get_current_user(db=self.db, token=None, x_kbf_user=None)

    def test_sso_prefixed_header_rejected(self) -> None:
        # A sub that already carries the reserved prefix must never be honored
        # (prevents identity smuggling / double-prefix confusion).
        with self.assertRaises(HTTPException):
            auth.get_current_user(db=self.db, token=None, x_kbf_user="sso:victim-sub")

    def test_identity_header_requires_gateway_secret_when_configured(self) -> None:
        original = auth.settings.kbf_forward_auth_secret
        auth.settings.kbf_forward_auth_secret = "gateway-secret"
        try:
            # Missing secret -> forged header is rejected.
            with self.assertRaises(HTTPException):
                auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-forged", x_kbf_auth=None)
            # Wrong secret -> rejected.
            with self.assertRaises(HTTPException):
                auth.get_current_user(
                    db=self.db, token=None, x_kbf_user="sub-forged", x_kbf_auth="wrong"
                )
            # Correct secret -> honored.
            user = auth.get_current_user(
                db=self.db, token=None, x_kbf_user="sub-ok", x_kbf_auth="gateway-secret"
            )
            self.assertIn("sub-ok", user.username)
        finally:
            auth.settings.kbf_forward_auth_secret = original

    def test_fails_closed_when_no_secret_and_no_dev_optin(self) -> None:
        # Misconfig (secret env missing) must NOT silently trust X-KBF-User.
        orig_secret = auth.settings.kbf_forward_auth_secret
        orig_flag = auth.settings.kbf_allow_insecure_sso_header
        auth.settings.kbf_forward_auth_secret = ""
        auth.settings.kbf_allow_insecure_sso_header = False
        try:
            with self.assertRaises(HTTPException):
                auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-x", x_kbf_auth=None)
        finally:
            auth.settings.kbf_forward_auth_secret = orig_secret
            auth.settings.kbf_allow_insecure_sso_header = orig_flag

    def test_non_ascii_gateway_secret_header_is_rejected_not_raised(self) -> None:
        # Starlette latin-1-decodes headers, so a non-ASCII X-KBF-Auth value
        # reaches hmac.compare_digest as a str outside the ASCII range. That
        # must fail closed (401), not blow up with an unhandled TypeError.
        original = auth.settings.kbf_forward_auth_secret
        auth.settings.kbf_forward_auth_secret = "gateway-secret"
        try:
            with self.assertRaises(HTTPException):
                auth.get_current_user(
                    db=self.db, token=None, x_kbf_user="sub-forged", x_kbf_auth="\xc3bad"
                )
        finally:
            auth.settings.kbf_forward_auth_secret = original

    def test_admin_header_marks_the_user_as_admin(self) -> None:
        user = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-admin", x_kbf_admin="true")
        self.assertTrue(user.is_admin)

    def test_admin_header_ignored_unless_exactly_true(self) -> None:
        user = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-notadmin", x_kbf_admin="1")
        self.assertFalse(user.is_admin)

    def test_missing_admin_header_defaults_to_not_admin(self) -> None:
        user = auth.get_current_user(db=self.db, token=None, x_kbf_user="sub-plain")
        self.assertFalse(user.is_admin)

    def test_jwt_path_is_never_admin(self) -> None:
        local = models.User(username="bob", password_hash=auth.hash_password("pw"))
        self.db.add(local)
        self.db.commit()
        self.db.refresh(local)
        token = auth.create_access_token({"sub": "bob"})
        user = auth.get_current_user(db=self.db, token=token, x_kbf_user=None)
        self.assertFalse(user.is_admin)

    def test_jwt_still_works_without_header(self) -> None:
        local = models.User(username="alice", password_hash=auth.hash_password("pw"))
        self.db.add(local)
        self.db.commit()
        self.db.refresh(local)
        token = auth.create_access_token({"sub": "alice"})
        user = auth.get_current_user(db=self.db, token=token, x_kbf_user=None)
        self.assertEqual(user.id, local.id)


if __name__ == "__main__":
    unittest.main()
