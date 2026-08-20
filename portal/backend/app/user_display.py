from __future__ import annotations

from . import models
from .auth import SSO_USERNAME_PREFIX

_SUBJECT_PREVIEW_LENGTH = 8


def display_name_for(user: models.User | None) -> str:
    """A label a human can recognise for a portal account.

    SSO accounts are keyed by the OIDC subject, so `username` alone reads as
    'sso:c6de859a-e9c6-...' in an admin list. Prefer what the identity provider
    told us at login and fall back to a shortened subject.
    """
    if user is None:
        return "(unknown)"
    for value in ((user.display_name or ""), (user.email or "")):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    username = (user.username or "").strip()
    if username.startswith(SSO_USERNAME_PREFIX):
        subject = username[len(SSO_USERNAME_PREFIX):]
        return f"{SSO_USERNAME_PREFIX}{subject[:_SUBJECT_PREVIEW_LENGTH]}"
    return username
