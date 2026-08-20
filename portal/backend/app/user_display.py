from __future__ import annotations

from . import models
from .auth import SSO_USERNAME_PREFIX

_UNKNOWN = "(unknown)"


def display_name_for(user: models.User | None) -> str:
    """A label a human can recognise for a portal account.

    SSO accounts are keyed by the OIDC subject, so `username` reads as
    'sso:c6de859a-e9c6-...'. Prefer what the identity provider told us at
    login. The subject is returned whole rather than shortened: truncating a
    UUID makes it no more recognisable and stops an admin pasting it back to
    look the account up, and some subjects are already readable names that
    truncation would mangle. Narrowing the column is the UI's job.
    """
    if user is None:
        return _UNKNOWN
    for value in (user.display_name, user.email, user.username):
        cleaned = (value or "").strip()
        if cleaned and cleaned != SSO_USERNAME_PREFIX:
            return cleaned
    return _UNKNOWN
