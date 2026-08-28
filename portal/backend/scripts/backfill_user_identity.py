"""Fill in email/display_name for accounts that logged in before those columns existed.

The columns arrived with the admin activity views, so every account created
earlier carries only its OIDC subject. `display_name_for` then falls back to
`username`, and the admin tables read "sso:0de140cb-eaee-..." instead of a
name. Logging in fixes one account at a time -- `provision_sso_user` refreshes
both fields from the gateway headers on every request -- so this script does
the same thing for everyone at once, straight from Keycloak.

Only accounts the portal already knows are touched: a Keycloak user who has
never signed in here has nothing to label, and inventing a row for them would
put strangers in the per-user table.

Reads the Keycloak admin credentials out of the running container, the same way
kbf-infra/fix_kc_redirect.sh does, so no secret is duplicated into this file.

Usage (from portal/backend):
    .venv/bin/python scripts/backfill_user_identity.py            # dry run
    .venv/bin/python scripts/backfill_user_identity.py --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.auth import SSO_USERNAME_PREFIX, provision_sso_user  # noqa: E402
from app.database import SessionLocal  # noqa: E402

KEYCLOAK = "http://127.0.0.1:8080"
CONTAINER = "kbf-infra-keycloak-1"
REALM = "kbf"


def _container_env(name: str) -> str:
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "printenv", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _admin_token() -> str:
    user = _container_env("KC_BOOTSTRAP_ADMIN_USERNAME") or _container_env("KEYCLOAK_ADMIN")
    password = _container_env("KC_BOOTSTRAP_ADMIN_PASSWORD") or _container_env(
        "KEYCLOAK_ADMIN_PASSWORD"
    )
    if not user or not password:
        raise SystemExit(f"Could not read Keycloak admin credentials from {CONTAINER}")
    body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": user,
            "password": password,
        }
    ).encode()
    request = urllib.request.Request(
        f"{KEYCLOAK}/realms/master/protocol/openid-connect/token", data=body
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)["access_token"]


def _realm_users(token: str) -> dict[str, dict]:
    """Every realm user, keyed by subject. One page of 1000 covers this realm."""
    request = urllib.request.Request(
        f"{KEYCLOAK}/admin/realms/{REALM}/users?max=1000",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return {u["id"]: u for u in json.load(response) if u.get("id")}


def _display_name(kc_user: dict) -> str:
    """What Keycloak would put in the `name` claim: given name, then family."""
    parts = [(kc_user.get("firstName") or "").strip(), (kc_user.get("lastName") or "").strip()]
    return " ".join(part for part in parts if part)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()

    kc_users = _realm_users(_admin_token())
    session = SessionLocal()
    try:
        accounts = (
            session.query(models.User)
            .filter(models.User.username.like(f"{SSO_USERNAME_PREFIX}%"))
            .all()
        )
        planned: list[tuple[str, str, str]] = []
        missing: list[str] = []

        for account in accounts:
            sub = account.username[len(SSO_USERNAME_PREFIX) :]
            kc_user = kc_users.get(sub)
            if kc_user is None:
                missing.append(sub)
                continue
            email = (kc_user.get("email") or "").strip()
            name = _display_name(kc_user)
            # Same rule as provision_sso_user: a blank value never wipes what we
            # already know, and an unchanged value is not a change.
            if (email and account.email != email) or (name and account.display_name != name):
                planned.append((sub, email, name))

        label = "적용" if args.apply else "예정(미적용)"
        print(f"SSO 계정 {len(accounts)}개 중 갱신 {label}: {len(planned)}개")
        for sub, email, name in planned:
            print(f"  {sub}  ->  display_name={name or '(없음)'}  email={email or '(없음)'}")
        if missing:
            print(f"Keycloak에 없는 계정 {len(missing)}개는 건너뜁니다(삭제된 계정으로 보입니다).")

        if not args.apply:
            print("\n--apply 를 붙이면 실제로 반영합니다.")
            return 0

        for sub, email, name in planned:
            provision_sso_user(session, sub, email or None, name or None)
        print(f"\n{len(planned)}개 계정을 갱신했습니다.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
