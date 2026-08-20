from app import models
from app.user_display import display_name_for


def _user(username, email=None, display_name=None):
    return models.User(username=username, password_hash="x", email=email, display_name=display_name)


def test_prefers_the_display_name():
    assert display_name_for(_user("sso:abc", "a@b.c", "Ada")) == "Ada"


def test_falls_back_to_the_email():
    assert display_name_for(_user("sso:abc", "a@b.c")) == "a@b.c"


def test_shortens_a_bare_sso_subject():
    assert display_name_for(_user("sso:c6de859a-e9c6-4621-a140-064734a9a69a")) == "sso:c6de859a"


def test_local_account_keeps_its_username():
    assert display_name_for(_user("kbfportal")) == "kbfportal"


def test_blank_values_are_ignored():
    assert display_name_for(_user("sso:abc", "   ", "  ")) == "sso:abc"
