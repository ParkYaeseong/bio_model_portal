from admin_helpers import client_as, make_user
from app.main import app


def test_non_admin_is_refused():
    try:
        response = client_as(make_user(is_admin=False)).get("/api/admin/activity")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_me_reports_the_flag():
    try:
        assert client_as(make_user(is_admin=True)).get("/api/admin/me").json() == {"is_admin": True}
    finally:
        app.dependency_overrides.clear()


def test_non_admin_me_reports_false_without_403():
    try:
        response = client_as(make_user(is_admin=False)).get("/api/admin/me")
        assert response.status_code == 200
        assert response.json() == {"is_admin": False}
    finally:
        app.dependency_overrides.clear()
