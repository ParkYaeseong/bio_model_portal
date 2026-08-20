from admin_helpers import client_as, make_user


def test_non_admin_is_refused():
    response = client_as(make_user(is_admin=False)).get("/api/admin/activity")
    assert response.status_code == 403


def test_admin_me_reports_the_flag():
    assert client_as(make_user(is_admin=True)).get("/api/admin/me").json() == {"is_admin": True}


def test_non_admin_me_reports_false_without_403():
    response = client_as(make_user(is_admin=False)).get("/api/admin/me")
    assert response.status_code == 200
    assert response.json() == {"is_admin": False}


def test_a_user_that_never_went_through_auth_is_not_admin():
    # get_current_user is what attaches is_admin; an object that skipped it has
    # no such attribute, and the gate must treat that as "not an admin".
    from app import models

    response = client_as(models.User(username="never-authenticated", password_hash="x")).get(
        "/api/admin/activity"
    )
    assert response.status_code == 403
