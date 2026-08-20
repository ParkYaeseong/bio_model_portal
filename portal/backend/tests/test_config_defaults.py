"""The production defaults for the SSO trust boundary.

These read the field definitions rather than a resolved Settings instance,
because conftest sets both variables in the environment for the whole suite —
a test that constructed Settings() would just read the test values back.
"""

from app.config import Settings


def test_the_insecure_sso_header_opt_in_is_off_by_default():
    assert Settings.model_fields["kbf_allow_insecure_sso_header"].default is False


def test_there_is_no_default_gateway_secret():
    assert Settings.model_fields["kbf_forward_auth_secret"].default == ""
