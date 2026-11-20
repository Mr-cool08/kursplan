"""Only administrators may look up a booked customer's company."""
from unittest.mock import Mock

from flask import Flask, redirect, session, url_for
import pytest

import company_lookup
import database
from admin import admin_page
from users import user_page


@pytest.fixture(params=("sqlite", "postgresql"))
def company_db(request, tmp_path):
    if request.param == "postgresql":
        return request.getfixturevalue("db_path")
    path = tmp_path / "company-lookup.db"
    database.create_database(path)
    return path


@pytest.fixture
def company_app(company_db, project_root, monkeypatch):
    monkeypatch.setenv("send_mail", "0")
    app = Flask("company-route-tests", template_folder=str(project_root / "templates"),
                static_folder=str(project_root / "static"))
    app.config.update(TESTING=True, SECRET_KEY="company-route-test-key", DB_PATH=company_db)
    app.register_blueprint(user_page)
    app.register_blueprint(admin_page)

    @app.route("/logout", endpoint="logout")
    def logout():
        session.clear()
        return redirect(url_for("user_page.index"))

    users = {}
    for role in ("admin", "user"):
        users[role] = database.create_user(
            role, f"company-{role}@example.test", "test-password", "556074-7551", role, company_db,
        )
    booking_id = database.create_booking(
        "Kundnamn", "company-user@example.test", "556074-7551", "HLR", 3,
        "Stockholm", "Sal 1", "2030-05-01", company_db,
    )
    lookup = Mock(return_value=company_lookup.LookupResult("found", "Ångström AB"))
    monkeypatch.setattr(company_lookup.service, "lookup", lookup)
    return app, users, booking_id, lookup


def signed_client(app, user, role):
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(user_id=user[0], user_name=user[1], user_email=user[3], role=role)
    return client


def test_company_lookup_requires_current_database_admin_role(company_app):
    app, users, booking_id, lookup = company_app
    path = f"/admin/bookings/{booking_id}/company"
    response = app.test_client().get(path)
    assert response.status_code == 302
    assert response.location.endswith("/login")
    ordinary_user = signed_client(app, users["user"], "user")
    assert ordinary_user.get(path).status_code == 403
    forged_admin = signed_client(app, users["user"], "admin")
    assert forged_admin.get(path).status_code == 403
    lookup.assert_not_called()


@pytest.mark.parametrize("result", [
    company_lookup.LookupResult("found", "Ångström & Partners AB"),
    company_lookup.LookupResult("not_found"),
    company_lookup.LookupResult("invalid"),
    company_lookup.LookupResult("unavailable"),
])
def test_admin_company_lookup_uses_stored_number_and_preserves_booking(company_app, result):
    app, users, booking_id, lookup = company_app
    lookup.return_value = result
    before = database.get_booking_details_by_id(booking_id, app.config["DB_PATH"])
    client = signed_client(app, users["admin"], "admin")
    response = client.get(f"/admin/bookings/{booking_id}/company?organization_number=5560360793")
    assert response.status_code == 200
    assert response.get_json() == {"status": result.status, "name": result.name}
    assert response.cache_control.private
    assert response.cache_control.no_store
    lookup.assert_called_once_with("556074-7551")
    assert database.get_booking_details_by_id(booking_id, app.config["DB_PATH"]) == before


def test_missing_booking_does_not_call_external_lookup(company_app):
    app, users, booking_id, lookup = company_app
    client = signed_client(app, users["admin"], "admin")
    assert client.get(f"/admin/bookings/{booking_id + 1000}/company").status_code == 404
    lookup.assert_not_called()


def test_only_admin_pages_render_automatic_lookup_without_blocking_html(company_app):
    app, users, booking_id, lookup = company_app
    admin = signed_client(app, users["admin"], "admin")
    for path in ("/admin/", f"/admin/bookings/{booking_id}"):
        response = admin.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert f"/admin/bookings/{booking_id}/company" in html
        assert "admin_companies.js" in html
        assert "556074-7551" in html
    customer = signed_client(app, users["user"], "user")
    for path in ("/dashboard", f"/bookings/{booking_id}"):
        response = customer.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "admin_companies.js" not in html
        assert f"/admin/bookings/{booking_id}/company" not in html
    lookup.assert_not_called()
