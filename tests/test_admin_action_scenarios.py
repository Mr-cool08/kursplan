"""Felvägar, åtkomst och sidoeffekter för varje administrativ bokningsåtgärd."""
from html.parser import HTMLParser
import time
from unittest.mock import Mock

from itsdangerous import TimestampSigner, URLSafeTimedSerializer
import pytest

from admin import routes as admin_routes
import database
import gen_func


ADMIN_EMAIL = "admin-scenarios@example.test"
CUSTOMER_EMAIL = "customer-scenarios@example.test"
PASSWORD = "admin-scenario-test-password"
PROPOSAL = {
    "proposed_date": "2030-08-24", "proposed_city": "Växjö", "proposed_venue": "Ågatan 1",
}
ADMIN_ROUTES = [
    ("get", "/admin/"), ("get", "/admin/booking"), ("post", "/admin/booking"),
    ("get", "/admin/bookings/{id}"), ("get", "/admin/bookings/{id}/company"),
    ("post", "/admin/bookings"), ("post", "/admin/bookings/{id}/status"),
    ("post", "/admin/bookings/{id}/notes"),
    ("post", "/admin/bookings/{id}/proposal/preview"),
    ("post", "/admin/bookings/{id}/proposal"),
    ("post", "/admin/bookings/{id}/proposals/999999/retry"),
]
MUTATIONS = [path for method, path in ADMIN_ROUTES if method == "post" and path != "/admin/booking"]


class _Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and "name" in values:
            self.values[values["name"]] = values.get("value", "")


def _token(client):
    response = client.get("/booking")
    assert response.status_code == 200
    return _Inputs(response.get_data(as_text=True)).values["csrf_token"]


def _signed_client(app, user):
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(user_id=user[0], user_name=user[1], user_email=user[3], role="admin")
    return client


@pytest.fixture
def admin_scenario(scenario_app, all_db_path, monkeypatch):
    admin = database.create_user("Administratör", ADMIN_EMAIL, PASSWORD, "5566778899", "admin", all_db_path)
    customer = database.create_user("Kund Åsa", CUSTOMER_EMAIL, PASSWORD, "5566778899", "user", all_db_path)
    booking_id = database.create_booking(
        customer[1], customer[3], customer[2], "HLR", 7, "Örebro", "Kundens lokal",
        "2030-08-20", all_db_path,
    )
    # Any accidental SMTP access makes these route tests fail immediately.
    smtp = Mock(side_effect=AssertionError("Riktigt mejlutskick i test"))
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    yield scenario_app, _signed_client(scenario_app, admin), _signed_client(scenario_app, customer), booking_id, admin
    smtp.assert_not_called()


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
@pytest.mark.parametrize("actor", ["anonymous", "customer"])
def test_every_admin_route_checks_current_role_before_actions(admin_scenario, all_db_path, method, path, actor):
    app, admin, customer, booking_id, _ = admin_scenario
    client = app.test_client() if actor == "anonymous" else customer
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    history = database.get_booking_history(booking_id, all_db_path, include_private=True)
    response = getattr(client, method)(path.format(id=booking_id), data={
        "target": "registered", "note": "must not be saved", **PROPOSAL, "role": "admin",
    })
    assert response.status_code == (302 if actor == "anonymous" else 403)
    if actor == "anonymous":
        assert response.location.endswith("/login")
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before
    assert database.get_booking_history(booking_id, all_db_path, include_private=True) == history
    assert database.get_booking_proposals(booking_id, all_db_path) == []


@pytest.mark.parametrize("path", MUTATIONS)
@pytest.mark.parametrize("submitted", [None, "forged-token"])
def test_every_admin_mutation_requires_csrf(admin_scenario, all_db_path, path, submitted):
    _, admin, _, booking_id, _ = admin_scenario
    _token(admin)
    data = {**PROPOSAL, "target": "registered", "note": "must not be saved"}
    if submitted is not None:
        data["csrf_token"] = submitted
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    history = database.get_booking_history(booking_id, all_db_path, include_private=True)
    assert admin.post(path.format(id=booking_id), data=data).status_code == 400
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before
    assert database.get_booking_history(booking_id, all_db_path, include_private=True) == history


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/admin/bookings/{id}"), ("get", "/admin/bookings/{id}/company"),
    *[("post", path) for path in MUTATIONS if "{id}" in path],
])
def test_unknown_booking_returns_404_without_affecting_existing_booking(admin_scenario, all_db_path, method, path):
    _, admin, _, booking_id, _ = admin_scenario
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    response = getattr(admin, method)(path.format(id=999999), data={
        "csrf_token": _token(admin), **PROPOSAL, "target": "registered", "note": "ok",
    })
    assert response.status_code == 404
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before


@pytest.mark.parametrize("path", [path for path in MUTATIONS if "{id}" in path])
def test_admin_action_urls_cannot_mutate_on_get(admin_scenario, all_db_path, path):
    _, admin, _, booking_id, _ = admin_scenario
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    assert admin.get(path.format(id=booking_id)).status_code == 405
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before


@pytest.mark.parametrize("status", list(database.BOOKING_STATUSES))
def test_admin_status_filters_show_only_matching_bookings(admin_scenario, all_db_path, status):
    _, admin, _, booking_id, _ = admin_scenario
    database.create_booking("Annan kund", "other-filter@example.test", "9999999999", "Unik extrakurs", 2,
                            "Lund", "Sal 2", "2030-09-01", all_db_path)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))
    response = admin.get("/admin/", query_string={"status": status})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'data-status="{status}"' in html
    assert f'href="/admin/bookings/{booking_id}"' in html
    assert ("Unik extrakurs" in html) == (status == "pending")
    assert 'aria-current="page"' in html


@pytest.mark.parametrize("status", ["unknown", "ACCEPTED", "' OR 1=1 --", "<script>alert(1)</script>"])
def test_admin_rejects_unknown_status_filters(admin_scenario, status):
    _, admin, *_ = admin_scenario
    response = admin.get("/admin/", query_string={"status": status})
    assert response.status_code == 400
    assert "Välj en giltig bokningsstatus" in response.get_data(as_text=True)


@pytest.mark.parametrize(("field", "value"), [
    ("proposed_date", ""), ("proposed_date", "2030-02-30"),
    ("proposed_date", "20300824"), ("proposed_date", "2030-W34-6"),
    ("proposed_date", "2030-08-24T09:00:00"),
    ("proposed_city", ""), ("proposed_city", " \t "), ("proposed_city", "a" * 201),
    ("proposed_venue", ""), ("proposed_venue", " \t "), ("proposed_venue", "a" * 1001),
])
@pytest.mark.parametrize("suffix", ["proposal/preview", "proposal"])
def test_invalid_proposal_fields_never_create_or_send_proposal(admin_scenario, all_db_path, field, value, suffix):
    _, admin, _, booking_id, _ = admin_scenario
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    history = database.get_booking_history(booking_id, all_db_path, include_private=True)
    response = admin.post(f"/admin/bookings/{booking_id}/{suffix}", data={
        **PROPOSAL, field: value, "csrf_token": _token(admin),
    })
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'role="alert"' in html
    expected_error = {
        "proposed_date": "Ange ett giltigt utbildningsdatum.",
        "proposed_city": "Ange en ort med högst 200 tecken.",
        "proposed_venue": "Ange en utbildningslokal eller adress med högst 1 000 tecken.",
    }[field]
    assert expected_error in html
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before
    assert database.get_booking_proposals(booking_id, all_db_path) == []
    assert database.get_booking_history(booking_id, all_db_path, include_private=True) == history


def test_preview_accepts_limits_trims_fields_and_escapes_html_without_saving(admin_scenario, all_db_path):
    _, admin, _, booking_id, _ = admin_scenario
    city = "Ö" * 200
    venue = '<img src=x onerror="alert(1)">'.ljust(1000, "å")
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    response = admin.post(f"/admin/bookings/{booking_id}/proposal/preview", data={
        "csrf_token": _token(admin), "proposed_date": " 2030-08-24 ",
        "proposed_city": f" {city} ", "proposed_venue": f" {venue} ",
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    fields = _Inputs(html).values
    assert fields["proposed_date"] == "2030-08-24"
    assert fields["proposed_city"] == city
    assert fields["proposed_venue"] == venue
    assert fields["confirmation_token"]
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before
    assert database.get_booking_proposals(booking_id, all_db_path) == []


@pytest.mark.parametrize("note", ["", " \n\t ", "a" * 4001])
def test_invalid_admin_notes_do_not_add_history(admin_scenario, all_db_path, note):
    _, admin, _, booking_id, _ = admin_scenario
    history = database.get_booking_history(booking_id, all_db_path, include_private=True)
    response = admin.post(f"/admin/bookings/{booking_id}/notes", data={"csrf_token": _token(admin), "note": note})
    assert response.status_code == 400
    assert "Ange en anteckning med 1–4 000 tecken" in response.get_data(as_text=True)
    assert database.get_booking_history(booking_id, all_db_path, include_private=True) == history


def test_note_at_maximum_length_is_private_and_preserves_booking(admin_scenario, all_db_path):
    _, admin, customer, booking_id, administrator = admin_scenario
    note = "Ö" * 4000
    before = database.get_booking_details_by_id(booking_id, all_db_path)
    response = admin.post(f"/admin/bookings/{booking_id}/notes", data={"csrf_token": _token(admin), "note": f" {note} "})
    assert response.status_code == 302
    history = database.get_booking_history(booking_id, all_db_path, include_private=True)
    entry = history[-1]
    assert entry["note"] == note
    assert entry["kind"] == "note" and entry["is_private"]
    assert entry["actor_id"] == administrator[0]
    assert entry["from_status"] == entry["to_status"] == "pending"
    assert database.get_booking_details_by_id(booking_id, all_db_path) == before
    assert note not in customer.get(f"/bookings/{booking_id}").get_data(as_text=True)
    assert note in admin.get(f"/admin/bookings/{booking_id}").get_data(as_text=True)


@pytest.mark.parametrize("mismatch", ["other-administrator", "other-booking", "expired-preview"])
def test_signed_preview_is_bound_to_admin_booking_and_expiration(admin_scenario, all_db_path, monkeypatch, mismatch):
    app, admin, _, booking_id, _ = admin_scenario
    csrf = _token(admin)
    with monkeypatch.context() as preview_clock:
        if mismatch == "expired-preview":
            class ExpiredSigner(TimestampSigner):
                def get_timestamp(self):
                    return int(time.time()) - 1900

            serializer = URLSafeTimedSerializer(app.secret_key, salt="booking-proposal-preview", signer=ExpiredSigner)
            preview_clock.setattr(admin_routes, "_confirmation_serializer", lambda: serializer)
        response = admin.post(f"/admin/bookings/{booking_id}/proposal/preview", data={
            **PROPOSAL, "csrf_token": csrf,
        })
    assert response.status_code == 200
    fields = _Inputs(response.get_data(as_text=True)).values
    target_id, sender = booking_id, admin
    if mismatch == "other-administrator":
        other_admin = database.create_user("Andra admin", "second-admin@example.test", PASSWORD, "5566778899", "admin", all_db_path)
        sender = _signed_client(app, other_admin)
        fields["csrf_token"] = _token(sender)
    elif mismatch == "other-booking":
        target_id = database.create_booking("Kund Åsa", CUSTOMER_EMAIL, "5566778899", "Annan kurs", 2,
                                            "Lund", "Sal 2", "2030-09-01", all_db_path)
    response = sender.post(f"/admin/bookings/{target_id}/proposal", data=fields)
    assert response.status_code == 400
    assert "Förhandsgranska förslaget igen" in response.get_data(as_text=True)
    for identifier in {booking_id, target_id}:
        assert database.get_booking_details_by_id(identifier, all_db_path)["status"] == "pending"
        assert database.get_booking_proposals(identifier, all_db_path) == []


@pytest.mark.parametrize("result", ["unexpected-result", None, RuntimeError("simulated mail failure")])
def test_retry_records_unknown_mail_results_and_exceptions_as_failed(admin_scenario, all_db_path, monkeypatch, result):
    _, admin, _, booking_id, _ = admin_scenario
    proposal = database.create_proposal(booking_id, "2030-08-24", "Växjö", "Ågatan 1", all_db_path)
    sender = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(gen_func, "send_proposal_mail", sender)
    csrf = _token(admin)
    cookie = admin.get_cookie("session")
    admin.set_cookie("session", cookie.value, domain="untrusted.example")
    response = admin.post(f"/admin/bookings/{booking_id}/proposals/{proposal['id']}/retry", data={
        "csrf_token": csrf,
    }, headers={"Host": "untrusted.example"})
    assert response.status_code == 302
    sender.assert_called_once()
    assert sender.call_args.args[0]["email"] == CUSTOMER_EMAIL
    assert sender.call_args.args[2] == f"https://kursplan.se/bookings/{booking_id}"
    saved = database.get_booking_proposals(booking_id, all_db_path)[0]
    assert saved["delivery_status"] == "failed"
    assert saved["state"] == "pending"
    assert database.get_booking_details_by_id(booking_id, all_db_path)["status"] == "awaiting_response"
    assert database.get_booking_details_by_id(booking_id, all_db_path)["faststallt_datum"] is None


def test_deleted_admin_session_cannot_execute_old_csrf_form(admin_scenario, all_db_path):
    _, admin, _, booking_id, administrator = admin_scenario
    csrf = _token(admin)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "DELETE FROM users WHERE id = %s", (administrator[0],))
    response = admin.post(f"/admin/bookings/{booking_id}/status", data={"csrf_token": csrf, "target": "registered"})
    assert response.status_code == 302 and response.location.endswith("/login")
    assert database.get_booking_details_by_id(booking_id, all_db_path)["status"] == "pending"
    with admin.session_transaction() as state:
        assert "user_id" not in state and "role" not in state
