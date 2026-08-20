from app import models
from app.user_display import display_name_for


def _user(username, email=None, display_name=None):
    return models.User(username=username, password_hash="x", email=email, display_name=display_name)


def test_prefers_the_display_name():
    assert display_name_for(_user("sso:abc", "a@b.c", "Ada")) == "Ada"


def test_falls_back_to_the_email():
    assert display_name_for(_user("sso:abc", "a@b.c")) == "a@b.c"


def test_sso_account_keeps_its_full_subject():
    assert display_name_for(_user("sso:c6de859a-e9c6-4621-a140-064734a9a69a")) == "sso:c6de859a-e9c6-4621-a140-064734a9a69a"


def test_human_readable_sso_subject_survives_intact():
    assert display_name_for(_user("sso:sp3-e2e-tester")) == "sso:sp3-e2e-tester"


def test_local_account_keeps_its_username():
    assert display_name_for(_user("kbfportal")) == "kbfportal"


def test_blank_values_are_skipped_in_favour_of_the_next_source():
    assert display_name_for(_user("sso:abc", "a@b.c", "  ")) == "a@b.c"
    assert display_name_for(_user("sso:c6de859a-e9c6-4621", "   ", "  ")) == "sso:c6de859a-e9c6-4621"


def test_missing_user_is_unknown():
    assert display_name_for(None) == "(unknown)"


def test_blank_username_is_unknown():
    assert display_name_for(_user("   ")) == "(unknown)"


def test_bare_sso_prefix_username_is_unknown():
    assert display_name_for(_user("sso:")) == "(unknown)"
