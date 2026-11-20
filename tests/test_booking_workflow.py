"""Exercise booking decisions through real routes against both database engines."""
from email import policy
from email.parser import Parser
from html.parser import HTMLParser
from smtplib import SMTPServerDisconnected
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from flask import Flask, redirect, session, url_for
import pytest

import database
import gen_func
from admin import admin_page
from users import user_page


CUSTOMER = "workflow-customer@example.test"
ADMIN = "workflow-admin@example.test"
PASSWORD = "workflow-test-password"


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.values[attrs["name"]] = attrs.get("value", "")


def token(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.get_data(as_text=True)
    fields = Inputs(response.get_data(as_text=True)).values
    if "csrf_token" in fields:
        return fields["csrf_token"]
    # Terminal detail pages have no forms; obtain a real form token to test
    # rejected stale POSTs without failing at the CSRF layer instead.
    response = client.get("/booking")
    return Inputs(response.get_data(as_text=True)).values["csrf_token"]


def login(client, email, *, next_url=None):
    path = "/login"
    if next_url:
        from urllib.parse import urlencode
        path += "?" + urlencode({"next": next_url})
    response = client.get(path)
    fields = Inputs(response.get_data(as_text=True)).values
    return client.post(path, data={**fields, "email": email, "password": PASSWORD})


@pytest.fixture(params=("sqlite", "postgresql"))
def workflow_path(request, tmp_path):
    if request.param == "postgresql":
        return request.getfixturevalue("db_path")
    path = tmp_path / "workflow-routes.db"
    database.create_database(path)
    return path


@pytest.fixture
def workflow(workflow_path, project_root, monkeypatch):
    monkeypatch.setenv("send_mail", "0")
    app = Flask("booking-workflow-tests", template_folder=str(project_root / "templates"),
                static_folder=str(project_root / "static"))
    app.config.update(TESTING=True, SECRET_KEY="workflow-test-secret", DB_PATH=workflow_path)
    app.register_blueprint(user_page)
    app.register_blueprint(admin_page)

    @app.route("/logout", endpoint="logout")
    def logout():
        session.clear()
        return redirect(url_for("user_page.index"))

    for name, email, role in (("Kund Åsa", CUSTOMER, "user"), ("Admin", ADMIN, "admin"),
                              ("Annan kund", "other@example.test", "user")):
        database.create_user(name, email, PASSWORD, "5566778899", role, workflow_path)
    customer, admin, other = app.test_client(), app.test_client(), app.test_client()
    assert login(customer, CUSTOMER).location.endswith("/dashboard")
    assert login(admin, ADMIN).location.endswith("/admin/")
    assert login(other, "other@example.test").location.endswith("/dashboard")
    return app, customer, admin, other, workflow_path


def create_request(customer, path, *, interval=False):
    response = customer.post("/booking", data={
        "csrf_token": token(customer, "/booking"),
        "datumtyp": "intervall" if interval else "exakt",
        "onskat_datum": "" if interval else "2030-05-01",
        "onskat_startdatum": "2030-05-01" if interval else "",
        "onskat_slutdatum": "2030-05-05" if interval else "",
        "utbildningskalla": "fritext", "egen_utbildning": "Anpassad HLR",
        "antal": "6", "ort": "", "lokalansvar": "kund", "lokaladress": "Ågatan 1",
    })
    assert response.status_code == 302
    booking = database.list_booking_details(path, email=CUSTOMER)[0]
    assert booking["status"] == "pending"
    return booking["id"]


def send_proposal(admin, path, booking_id, *, day="2030-05-03", city="Uppsala", venue="Sal Å"):
    values = {"proposed_date": day, "proposed_city": city, "proposed_venue": venue}
    preview = admin.post(f"/admin/bookings/{booking_id}/proposal/preview", data={
        **values, "csrf_token": token(admin, f"/admin/bookings/{booking_id}"),
    })
    assert preview.status_code == 200, preview.get_data(as_text=True)
    fields = Inputs(preview.get_data(as_text=True)).values
    assert fields.get("confirmation_token")
    assert database.get_booking_details_by_id(booking_id, path)["status"] in {
        "pending", "registered", "new_date_requested",
    }
    response = admin.post(f"/admin/bookings/{booking_id}/proposal", data={
        **fields, **values, "confirmed": "1",
    })
    assert response.status_code == 302, response.get_data(as_text=True)
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "awaiting_response"
    return database.get_booking_proposals(booking_id, path)[-1]


def answer(customer, booking_id, proposal_id, action):
    return customer.post(f"/bookings/{booking_id}/proposals/{proposal_id}/respond", data={
        "csrf_token": token(customer, f"/bookings/{booking_id}"), "action": action,
    })


def change_status(admin, booking_id, target):
    return admin.post(f"/admin/bookings/{booking_id}/status", data={
        "csrf_token": token(admin, f"/admin/bookings/{booking_id}"), "target": target,
    })


@pytest.mark.parametrize("registered", (False, True), ids=("direct-proposal", "registered-first"))
def test_request_registration_proposal_acceptance_and_completion(workflow, registered):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path, interval=True)
    if registered:
        assert change_status(admin, booking_id, "registered").status_code == 302
        assert database.get_booking_details_by_id(booking_id, path)["status"] == "registered"
        assert not database.get_booking_proposals(booking_id, path)
        assert "Registrerad" in customer.get("/dashboard").get_data(as_text=True)
    proposal = send_proposal(admin, path, booking_id)
    booking = database.get_booking_details_by_id(booking_id, path)
    assert booking["faststallt_datum"] is None
    assert proposal["sent_at"] is None
    assert proposal["delivery_status"] == "disabled"
    assert "Inväntar kundens svar" in customer.get("/dashboard").get_data(as_text=True)
    assert answer(customer, booking_id, proposal["id"], "accept").status_code == 302
    booking = database.get_booking_details_by_id(booking_id, path)
    assert booking["status"] == "accepted"
    assert (booking["faststallt_datum"], booking["faststalld_ort"], booking["faststalld_lokal"]) == (
        "2030-05-03", "Uppsala", "Sal Å",
    )
    assert (booking["onskat_startdatum"], booking["onskat_slutdatum"], booking["lokaladress"]) == (
        "2030-05-01", "2030-05-05", "Ågatan 1",
    )
    assert change_status(admin, booking_id, "completed").status_code == 302
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "completed"
    assert "Slutförd" in customer.get(f"/bookings/{booking_id}").get_data(as_text=True)
    proposals = database.get_booking_proposals(booking_id, path)
    assert len(proposals) == 1 and proposals[0]["state"] == "accepted"
    assert proposals[0]["responded_at"]
    history = database.get_booking_history(booking_id, path)
    assert {event["actor"] for event in history} >= {"customer", "admin"}
    assert history[-1]["to_status"] == "completed"


def test_three_proposals_stale_answers_and_manual_reschedule(workflow):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    proposals = []
    for day in ("2030-05-02", "2030-05-04", "2030-05-06"):
        proposal = send_proposal(admin, path, booking_id, day=day)
        proposals.append(proposal)
        if len(proposals) < 3:
            assert answer(customer, booking_id, proposal["id"], "new_date").status_code == 302
            assert database.get_booking_details_by_id(booking_id, path)["status"] == "new_date_requested"
            assert "Kunden har begärt ett nytt datumförslag." in str(database.get_booking_history(booking_id, path))
    for stale in proposals[:2]:
        assert answer(customer, booking_id, stale["id"], "accept").status_code == 409
        assert database.get_booking_details_by_id(booking_id, path)["status"] == "awaiting_response"
    assert answer(customer, booking_id, proposals[-1]["id"], "accept").status_code == 302
    before = database.get_booking_details_by_id(booking_id, path)
    assert change_status(admin, booking_id, "rescheduled").status_code == 302
    after = database.get_booking_details_by_id(booking_id, path)
    assert after["status"] == "rescheduled"
    for field in ("faststallt_datum", "faststalld_ort", "faststalld_lokal"):
        assert before[field] == after[field]
    assert len(database.get_booking_proposals(booking_id, path)) == 3
    assert "Ombokad" in customer.get("/dashboard").get_data(as_text=True)
    assert change_status(admin, booking_id, "cancelled").status_code == 302
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "cancelled"


@pytest.mark.parametrize("stage", ("pending", "registered", "awaiting_response", "new_date_requested"))
def test_customer_can_cancel_each_unconfirmed_stage_and_old_link_cannot_accept(workflow, stage):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    proposal = None
    if stage == "registered":
        assert change_status(admin, booking_id, "registered").status_code == 302
    elif stage in {"awaiting_response", "new_date_requested"}:
        proposal = send_proposal(admin, path, booking_id)
        if stage == "new_date_requested":
            assert answer(customer, booking_id, proposal["id"], "new_date").status_code == 302
    response = customer.post(f"/cancel_booking/{booking_id}", data={
        "csrf_token": token(customer, f"/bookings/{booking_id}"),
    })
    assert response.status_code == 302
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "cancelled"
    if proposal:
        assert answer(customer, booking_id, proposal["id"], "accept").status_code == 409


def test_authorization_csrf_and_invalid_transitions_do_not_change_booking(workflow):
    app, customer, admin, other, path = workflow
    booking_id = create_request(customer, path)
    assert other.get(f"/bookings/{booking_id}").status_code == 404
    assert other.get(f"/admin/bookings/{booking_id}").status_code == 403
    assert customer.post(f"/admin/bookings/{booking_id}/status", data={"target": "registered"}).status_code == 403
    assert admin.post(f"/admin/bookings/{booking_id}/status", data={"target": "registered"}).status_code == 400
    assert change_status(admin, booking_id, "accepted").status_code == 409
    assert change_status(admin, booking_id, "completed").status_code == 409
    assert change_status(admin, booking_id, "cancelled").status_code == 409
    proposal = send_proposal(admin, path, booking_id)
    assert other.post(f"/bookings/{booking_id}/proposals/{proposal['id']}/respond", data={
        "csrf_token": token(other, "/booking"), "action": "accept",
    }).status_code == 404
    assert customer.post(f"/bookings/{booking_id}/proposals/{proposal['id']}/respond", data={"action": "accept"}).status_code == 400
    assert answer(customer, booking_id, proposal["id"], "invalid").status_code == 400
    anonymous = app.test_client()
    assert anonymous.get(f"/bookings/{booking_id}").status_code == 302
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "awaiting_response"
    assert database.get_booking_details_by_id(booking_id, path)["faststallt_datum"] is None


def test_email_link_requires_login_retains_local_destination_and_never_mutates_on_get(workflow):
    app, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    proposal = send_proposal(admin, path, booking_id)
    target = f"/bookings/{booking_id}?action=accept&proposal_id={proposal['id']}"
    anonymous = app.test_client()
    response = anonymous.get(target)
    assert response.status_code == 302
    redirect_url = urlsplit(response.location)
    assert redirect_url.path == "/login"
    assert parse_qs(redirect_url.query)["next"] == [target]
    login_response = login(anonymous, CUSTOMER, next_url=target)
    assert login_response.status_code == 302
    assert login_response.location == target
    assert anonymous.get(target).status_code == 200
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "awaiting_response"
    assert answer(anonymous, booking_id, proposal["id"], "accept").status_code == 302
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "accepted"


@pytest.mark.parametrize("target", ("https://evil.example/", "//evil.example/", "/\\evil.example/"))
def test_login_next_rejects_external_redirects(workflow, target):
    app, *_ = workflow
    response = login(app.test_client(), CUSTOMER, next_url=target)
    assert response.location == "/dashboard"


def test_login_and_signup_require_csrf_and_reject_replayed_old_session_token(workflow):
    app, *_ = workflow
    client = app.test_client()
    assert client.post("/login", data={"email": CUSTOMER, "password": PASSWORD}).status_code == 400
    assert client.post("/signup", data={"name": "New", "email": "new@example.test", "password": PASSWORD,
                                        "organization_number": "5566778899"}).status_code == 400
    old = token(client, "/login")
    assert client.post("/login", data={"csrf_token": old, "email": CUSTOMER, "password": PASSWORD}).status_code == 302
    client.get("/logout")
    assert client.post("/login", data={"csrf_token": old, "email": CUSTOMER, "password": PASSWORD}).status_code == 400


def test_signup_hashes_password_and_database_identity_overrides_forged_session_email(workflow):
    app, customer, _, other, path = workflow
    registration = app.test_client()
    response = registration.post("/signup", data={
        "csrf_token": token(registration, "/signup"), "name": "Ny kund", "email": "new@example.test",
        "password": PASSWORD, "organization_number": "5566778899",
    })
    assert response.status_code == 302
    assert database.authenticate_user("new@example.test", PASSWORD, path)
    with database._cursor(path) as cursor:
        database._execute(cursor, "SELECT password FROM users WHERE email = %s", ("new@example.test",))
        stored = cursor.fetchone()[0]
    from werkzeug.security import check_password_hash
    assert stored != PASSWORD and check_password_hash(stored, PASSWORD)
    booking_id = create_request(customer, path)
    with other.session_transaction() as state:
        state["user_email"] = CUSTOMER
        state["role"] = "admin"
    assert other.get(f"/bookings/{booking_id}").status_code == 404
    assert other.get(f"/admin/bookings/{booking_id}").status_code == 403
    with other.session_transaction() as state:
        assert state["user_email"] == "other@example.test" and state["role"] == "user"


def test_admin_role_revocation_takes_effect_for_existing_session(workflow):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    old_token = token(admin, f"/admin/bookings/{booking_id}")
    with database._cursor(path) as cursor:
        database._execute(cursor, "UPDATE users SET role = 'user' WHERE email = %s", (ADMIN,))
    assert admin.get(f"/admin/bookings/{booking_id}").status_code == 403
    assert admin.post(f"/admin/bookings/{booking_id}/status", data={
        "csrf_token": old_token, "target": "registered",
    }).status_code == 403
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "pending"


def test_old_email_link_shows_warning_and_current_proposal_without_accepting_either(workflow):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    old = send_proposal(admin, path, booking_id)
    assert answer(customer, booking_id, old["id"], "new_date").status_code == 302
    current = send_proposal(admin, path, booking_id, day="2030-05-05")
    response = customer.get(f"/bookings/{booking_id}?action=accept&proposal_id={old['id']}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "tidigare datumförslag" in html
    assert f'/bookings/{booking_id}/proposals/{current["id"]}/respond' in html
    assert f'/bookings/{booking_id}/proposals/{old["id"]}/respond' not in html
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "awaiting_response"


def test_preview_from_previous_proposal_cycle_is_rejected_even_with_fresh_csrf(workflow):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    first = send_proposal(admin, path, booking_id)
    assert answer(customer, booking_id, first["id"], "new_date").status_code == 302
    values = {"proposed_date": "2030-05-05", "proposed_city": "Uppsala", "proposed_venue": "Sal 1"}
    preview = admin.post(f"/admin/bookings/{booking_id}/proposal/preview", data={
        **values, "csrf_token": token(admin, f"/admin/bookings/{booking_id}"),
    })
    assert preview.status_code == 200
    old_fields = Inputs(preview.get_data(as_text=True)).values
    assert admin.post(f"/admin/bookings/{booking_id}/proposal", data=old_fields).status_code == 302
    second = database.get_booking_proposals(booking_id, path)[-1]
    assert answer(customer, booking_id, second["id"], "new_date").status_code == 302
    response = admin.post(f"/admin/bookings/{booking_id}/proposal", data={
        **old_fields, "csrf_token": token(admin, f"/admin/bookings/{booking_id}"),
    })
    assert response.status_code in {400, 409}
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "new_date_requested"
    assert len(database.get_booking_proposals(booking_id, path)) == 2


def test_preview_required_tamper_rejected_and_duplicate_submission_sends_once(workflow, monkeypatch):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    mail = Mock(return_value="sent")
    monkeypatch.setattr(gen_func, "send_proposal_mail", mail)
    values = {"proposed_date": "2030-05-03", "proposed_city": "Uppsala", "proposed_venue": "Sal 1"}
    csrf = token(admin, f"/admin/bookings/{booking_id}")
    assert admin.post(f"/admin/bookings/{booking_id}/proposal", data={
        **values, "csrf_token": csrf, "confirmed": "1",
    }).status_code == 400
    preview = admin.post(f"/admin/bookings/{booking_id}/proposal/preview", data={
        **values, "csrf_token": csrf,
    })
    fields = Inputs(preview.get_data(as_text=True)).values
    assert admin.post(f"/admin/bookings/{booking_id}/proposal", data={
        **fields, **values, "proposed_city": "Manipulerad ort", "confirmed": "1",
    }).status_code == 400
    submission = {**fields, **values, "confirmed": "1"}
    assert admin.post(f"/admin/bookings/{booking_id}/proposal", data=submission).status_code == 302
    assert admin.post(f"/admin/bookings/{booking_id}/proposal", data=submission).status_code in {400, 409}
    assert mail.call_count == 1
    booking, proposal, booking_url = mail.call_args.args
    assert booking["id"] == booking_id and booking["email"] == CUSTOMER
    assert proposal["booking_id"] == booking_id
    assert booking_url.startswith("https://kursplan.se/bookings/")
    proposals = database.get_booking_proposals(booking_id, path)
    assert len(proposals) == 1 and proposals[0]["sent_at"]


def test_failed_mail_is_visible_and_can_be_retried_without_new_proposal(workflow, monkeypatch):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    mail = Mock(side_effect=["failed", "sent"])
    monkeypatch.setattr(gen_func, "send_proposal_mail", mail)
    proposal = send_proposal(admin, path, booking_id)
    assert proposal["delivery_status"] == "failed" and proposal["sent_at"] is None
    html = admin.get(f"/admin/bookings/{booking_id}").get_data(as_text=True)
    assert "misslyck" in html.lower() or "kunde inte skickas" in html.lower()
    response = admin.post(f"/admin/bookings/{booking_id}/proposals/{proposal['id']}/retry", data={
        "csrf_token": token(admin, f"/admin/bookings/{booking_id}"),
    })
    assert response.status_code == 302
    proposals = database.get_booking_proposals(booking_id, path)
    assert len(proposals) == 1 and proposals[0]["delivery_status"] == "sent"
    assert proposals[0]["sent_at"] and mail.call_count == 2
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "awaiting_response"


def test_admin_notes_are_private_and_user_input_is_escaped(workflow):
    _, customer, admin, _, path = workflow
    booking_id = create_request(customer, path)
    note = "Intern anteckning <script>alert('x')</script>"
    response = admin.post(f"/admin/bookings/{booking_id}/notes", data={
        "csrf_token": token(admin, f"/admin/bookings/{booking_id}"), "note": note,
    })
    assert response.status_code == 302
    html = admin.get(f"/admin/bookings/{booking_id}").get_data(as_text=True)
    assert "&lt;script&gt;" in html and "<script>alert" not in html
    customer_html = customer.get(f"/bookings/{booking_id}").get_data(as_text=True)
    assert "Intern anteckning" not in customer_html
    assert "Intern anteckning" not in customer.get("/dashboard").get_data(as_text=True)


def test_proposal_email_content_uses_real_customer_and_confirmation_links(monkeypatch):
    monkeypatch.setenv("send_mail", "1")
    for name, value in (("smtp_email", "sender@example.test"), ("smtp_server_address", "smtp.example.test"),
                        ("smtp_port", "587"), ("smtp_password", "test-smtp-password")):
        monkeypatch.setenv(name, value)
    smtp = Mock()
    smtp.return_value.__enter__ = Mock(return_value=smtp.return_value)
    smtp.return_value.__exit__ = Mock(return_value=False)
    smtp.return_value.sendmail.return_value = {}
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    booking = {"id": 42, "email": CUSTOMER, "utbildning": "Första hjälpen", "antal": 6, "name": "Åsa"}
    proposal = {"id": 3, "booking_id": 42, "proposed_date": "2030-05-03", "proposed_city": "Uppsala",
                "proposed_venue": "Sal Å"}
    assert gen_func.send_proposal_mail(booking, proposal, "https://kursplan.se/bookings/42") == "sent"
    args = smtp.return_value.sendmail.call_args.args
    assert args[:2] == ("sender@example.test", CUSTOMER)
    message = Parser(policy=policy.default).parsestr(args[2])
    body = message.get_body(preferencelist=("plain",)).get_content()
    for value in ("42", "Första hjälpen", "6", "2030-05-03", "Uppsala", "Sal Å",
                  "Acceptera", "Begär nytt datum", "Avbryt"):
        assert value in body
    assert "action=accept" in body and "action=new_date" in body and "action=cancel" in body
    assert "proposal_id=3" in body
    smtp.return_value.starttls.assert_called_once()
    smtp.return_value.login.assert_called_once_with("sender@example.test", "test-smtp-password")


def test_proposal_email_disabled_and_failed_smtp_never_report_sent(monkeypatch):
    booking = {"id": 42, "email": CUSTOMER, "utbildning": "HLR", "antal": 3, "name": "Kund"}
    proposal = {"id": 3, "booking_id": 42, "proposed_date": "2030-05-03", "proposed_city": "Uppsala",
                "proposed_venue": "Sal Å"}
    smtp = Mock(side_effect=OSError("SMTP unavailable"))
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    monkeypatch.setenv("send_mail", "0")
    assert gen_func.send_proposal_mail(booking, proposal, "https://kursplan.se/bookings/42") == "disabled"
    smtp.assert_not_called()
    monkeypatch.setenv("send_mail", "1")
    for name, value in (("smtp_email", "sender@example.test"), ("smtp_server_address", "smtp.example.test"),
                        ("smtp_port", "587"), ("smtp_password", "test-smtp-password")):
        monkeypatch.setenv(name, value)
    assert gen_func.send_proposal_mail(booking, proposal, "https://kursplan.se/bookings/42") == "failed"


@pytest.mark.parametrize(("refused", "expected"), (({}, "sent"), ({CUSTOMER: (550, b"Rejected")}, "failed")))
def test_mail_accepted_by_server_remains_sent_when_quit_disconnects(monkeypatch, refused, expected):
    monkeypatch.setenv("send_mail", "1")
    for name, value in (("smtp_email", "sender@example.test"), ("smtp_server_address", "smtp.example.test"),
                        ("smtp_port", "587"), ("smtp_password", "test-smtp-password")):
        monkeypatch.setenv(name, value)
    smtp = Mock()
    server = smtp.return_value
    server.__enter__ = Mock(return_value=server)
    server.__exit__ = Mock(side_effect=SMTPServerDisconnected("QUIT disconnected"))
    server.sendmail.return_value = refused
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    booking = {"id": 42, "email": CUSTOMER, "utbildning": "HLR", "antal": 3, "name": "Kund"}
    proposal = {"id": 3, "booking_id": 42, "proposed_date": "2030-05-03", "proposed_city": "Uppsala",
                "proposed_venue": "Sal Å"}
    assert gen_func.send_proposal_mail(booking, proposal, "https://kursplan.se/bookings/42") == expected

