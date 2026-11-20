"""Nya kundkonton måste verifiera sin e-post innan de får åtkomst."""

import logging
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

import database


EMAIL = "verification-customer@example.test"
PASSWORD = "Ett långt lösenord med åäö!"
SIGNUP = {
    "name": "Verifieringskund", "email": EMAIL, "password": PASSWORD,
    "organization_number": "5566778899",
}


@pytest.fixture
def verification_mailbox(monkeypatch):
    deliveries = []

    def send(email, url):
        deliveries.append((email, url))
        return "sent"

    monkeypatch.setattr("users.routes.gen_func.send_verification_mail", send)
    return deliveries


@pytest.fixture
def verification_client(scenario_app, verification_mailbox):
    return scenario_app.test_client()


def _csrf(client, path="/login"):
    assert client.get(path).status_code == 200
    with client.session_transaction() as state:
        return state["booking_csrf_token"]


def _signup(client, **kwargs):
    return client.post("/signup", data={
        **SIGNUP, "csrf_token": _csrf(client, "/signup"), **kwargs,
    })


def _mail_token(mailbox):
    return parse_qs(urlsplit(mailbox[-1][1]).query)["token"][0]


def _login(client):
    return client.post("/login", data={
        "email": EMAIL, "password": PASSWORD, "csrf_token": _csrf(client, "/login"),
    })


def _assert_signed_out(client):
    with client.session_transaction() as state:
        for key in ("user_id", "role", "user_email", "user_name", "organization_number"):
            assert key not in state


class _LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms.append(dict(attrs))
        elif tag == "button":
            self.buttons.append(dict(attrs))


def _assert_verification_banner(response):
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="email-verification-banner"' in html
    assert "Skicka verifieringsmejlet igen" in html
    parser = _LoginFormParser()
    parser.feed(html)
    assert any(form.get("id") == "login-form" and form.get("action") == "/login"
               for form in parser.forms)
    assert any(button.get("type") == "submit" and button.get("form") == "login-form"
               and button.get("formaction") == "/verify-email/resend"
               for button in parser.buttons)


def test_customer_must_verify_before_logging_in(verification_client, verification_mailbox, all_db_path):
    client = verification_client
    registration = _signup(client)
    assert registration.status_code == 302
    assert urlsplit(registration.location).path == "/login"
    assert len(verification_mailbox) == 1
    assert verification_mailbox[0][0] == EMAIL
    _assert_signed_out(client)
    login_page = client.get(registration.location)
    _assert_verification_banner(login_page)
    assert "Kontot är skapat" in login_page.get_data(as_text=True)
    _assert_verification_banner(client.get("/login"))
    failed_login = _login(client)
    _assert_verification_banner(failed_login)
    assert "Felaktig e-postadress eller lösenord" in failed_login.get_data(as_text=True)
    with client.session_transaction() as state:
        assert state["email_verification_pending"] is True
        for secret in (PASSWORD, EMAIL, _mail_token(verification_mailbox)):
            assert secret not in repr(dict(state))
    _assert_signed_out(client)
    for protected in ("/dashboard", "/booking", "/bookings/99999", "/admin/"):
        assert client.get(protected).status_code == 302

    token = _mail_token(verification_mailbox)
    response = client.get("/verify-email", query_string={"token": token})
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert database.get_email_verification(token, all_db_path)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    _assert_signed_out(client)

    with client.session_transaction() as state:
        csrf = state["booking_csrf_token"]
    response = client.post("/verify-email", data={"verification_token": token, "csrf_token": csrf})
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    assert not database.get_email_verification(token, all_db_path)
    _assert_signed_out(client)
    with client.session_transaction() as state:
        assert "email_verification_pending" not in state
    assert 'id="email-verification-banner"' not in client.get("/login").get_data(as_text=True)
    assert _login(client).location.endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200


def test_normal_login_does_not_show_post_registration_banner(verification_client):
    response = verification_client.get("/login")
    assert response.status_code == 200
    assert 'id="email-verification-banner"' not in response.get_data(as_text=True)
    _assert_signed_out(verification_client)


def test_old_resend_page_redirects_to_login_with_banner(verification_client):
    response = verification_client.get("/verify-email/resend")
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    _assert_verification_banner(verification_client.get(response.location))
    _assert_signed_out(verification_client)


@pytest.mark.parametrize("fields", [
    {"email": "", "password": PASSWORD},
    {"email": "invalid-email", "password": PASSWORD},
    {"email": EMAIL, "password": ""},
])
def test_resend_validation_stays_on_login_without_sending(verification_client, verification_mailbox, fields):
    response = verification_client.post("/verify-email/resend", data={
        **fields, "csrf_token": _csrf(verification_client),
    })
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert '<h1 id="login-heading">Logga in</h1>' in html
    assert "Ange en giltig e-postadress och ditt lösenord" in html
    assert PASSWORD not in html
    assert verification_mailbox == []
    _assert_signed_out(verification_client)


def test_verification_link_uses_configured_origin_and_not_host_header(
    verification_client, verification_mailbox, scenario_app, csrf_from_html,
):
    scenario_app.config["PUBLIC_BASE_URL"] = "https://accounts.kursplan.example"
    form = verification_client.get("/signup", base_url="https://attacker.example")
    response = verification_client.post("/signup", base_url="https://attacker.example", data={
        **SIGNUP, "csrf_token": csrf_from_html(form.get_data(as_text=True)),
    })
    assert response.status_code == 302
    url = verification_mailbox[0][1]
    assert urlsplit(url).netloc == "accounts.kursplan.example"
    assert urlsplit(url).path == "/verify-email"
    assert "attacker.example" not in url


@pytest.mark.parametrize("token", ["", "invalid-token", "åäö", "' OR 1=1 --", "x" * 2000])
def test_invalid_verification_tokens_never_authenticate(verification_client, all_db_path, token):
    database.create_user("Kund", EMAIL, PASSWORD, "5566778899", "user", all_db_path, email_verified=False)
    response = verification_client.get("/verify-email", query_string={"token": token})
    assert response.status_code == 400
    response = verification_client.post("/verify-email", data={
        "verification_token": token, "csrf_token": _csrf(verification_client),
    })
    assert response.status_code == 400
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    _assert_signed_out(verification_client)


def test_verification_token_is_single_use_and_tamper_proof(verification_client, verification_mailbox, all_db_path):
    assert _signup(verification_client).status_code == 302
    token = _mail_token(verification_mailbox)
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    assert verification_client.post("/verify-email", data={
        "verification_token": tampered, "csrf_token": _csrf(verification_client),
    }).status_code == 400
    assert database.get_email_verification(token, all_db_path)
    assert verification_client.post("/verify-email", data={
        "verification_token": token, "csrf_token": _csrf(verification_client),
    }).status_code == 302
    assert verification_client.get("/verify-email", query_string={"token": token}).status_code == 400
    assert verification_client.post("/verify-email", data={
        "verification_token": token, "csrf_token": _csrf(verification_client),
    }).status_code == 400
    _assert_signed_out(verification_client)


def test_expired_verification_token_cannot_verify_account(verification_client, verification_mailbox, all_db_path):
    assert _signup(verification_client).status_code == 302
    token = _mail_token(verification_mailbox)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "UPDATE user_email_verifications SET expires_at = 0")
    assert verification_client.get("/verify-email", query_string={"token": token}).status_code == 400
    assert verification_client.post("/verify-email", data={
        "verification_token": token, "csrf_token": _csrf(verification_client),
    }).status_code == 400
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    _assert_signed_out(verification_client)


@pytest.mark.parametrize("path", ["/verify-email", "/verify-email/resend"])
@pytest.mark.parametrize("csrf", [None, "forged-csrf-token"])
def test_verification_and_resend_require_csrf(verification_client, verification_mailbox, all_db_path, path, csrf):
    assert _signup(verification_client).status_code == 302
    token = _mail_token(verification_mailbox)
    fields = {"email": EMAIL, "password": PASSWORD, "verification_token": token}
    if csrf is not None:
        fields["csrf_token"] = csrf
    response = verification_client.post(path, data=fields)
    assert response.status_code == 400
    assert database.get_email_verification(token, all_db_path)
    assert len(verification_mailbox) == 1
    _assert_signed_out(verification_client)


def test_resend_requires_credentials_and_does_not_reveal_account_state(
    verification_client, verification_mailbox, all_db_path,
):
    database.create_user("Kund", EMAIL, PASSWORD, "5566778899", "user", all_db_path, email_verified=False)
    states = [
        {"email": "missing@example.test", "password": PASSWORD},
        {"email": EMAIL, "password": "wrong-password"},
    ]
    responses = []
    for fields in states:
        response = verification_client.post("/verify-email/resend", data={
            **fields, "csrf_token": _csrf(verification_client),
        })
        assert response.status_code == 302
        assert urlsplit(response.location).path == "/login"
        with verification_client.session_transaction() as state:
            responses.append(state.get("_flashes"))
        assert verification_client.get(response.location).status_code == 200
    assert responses[0] == responses[1]
    assert verification_mailbox == []
    assert verification_client.post("/verify-email/resend", data={
        "email": EMAIL, "password": PASSWORD, "csrf_token": _csrf(verification_client),
    }).status_code == 302
    assert len(verification_mailbox) == 1
    token = _mail_token(verification_mailbox)
    assert database.get_email_verification(token, all_db_path)
    assert verification_client.post("/verify-email/resend", data={
        "email": EMAIL, "password": PASSWORD, "csrf_token": _csrf(verification_client),
    }).status_code == 302
    assert len(verification_mailbox) == 1
    assert database.get_email_verification(token, all_db_path)
    _assert_signed_out(verification_client)

    assert database.verify_email_token(token, all_db_path)
    response = verification_client.post("/verify-email/resend", data={
        "email": EMAIL, "password": PASSWORD, "csrf_token": _csrf(verification_client),
    })
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    with verification_client.session_transaction() as state:
        assert state.get("_flashes") == responses[0]
    assert len(verification_mailbox) == 1


def test_resend_after_cooldown_replaces_previous_link(verification_client, verification_mailbox, all_db_path):
    assert _signup(verification_client).status_code == 302
    first = _mail_token(verification_mailbox)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "UPDATE user_email_verifications SET sent_at = 0")
    assert verification_client.post("/verify-email/resend", data={
        "email": EMAIL, "password": PASSWORD, "csrf_token": _csrf(verification_client),
    }).status_code == 302
    assert len(verification_mailbox) == 2
    second = _mail_token(verification_mailbox)
    assert second != first
    assert not database.get_email_verification(first, all_db_path)
    assert database.get_email_verification(second, all_db_path)
    assert not database.verify_email_token(first, all_db_path)
    _assert_signed_out(verification_client)


def test_resend_preserves_exact_legacy_email_when_accounts_differ_only_in_case(
    verification_client, verification_mailbox, all_db_path,
):
    email = "Legacy.Verification@example.test"
    first = database.create_user("Äldre kund", email, PASSWORD, "1111111111", "user", all_db_path,
                                 email_verified=False)
    second = database.create_user("Annan kund", email.lower(), "different-password", "2222222222", "user",
                                  all_db_path, email_verified=False)
    response = verification_client.post("/verify-email/resend", data={
        "email": email, "password": PASSWORD, "csrf_token": _csrf(verification_client),
    })
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    assert len(verification_mailbox) == 1
    assert verification_mailbox[0][0] == email
    assert database.verify_email_token(_mail_token(verification_mailbox), all_db_path)
    assert database.get_user_identity(first[0], all_db_path)["email_verified"] == 1
    assert database.get_user_identity(second[0], all_db_path)["email_verified"] == 0


def test_resend_sends_to_stored_account_address_not_caller_spelling(
    verification_client, verification_mailbox, all_db_path,
):
    database.create_user("Kund", EMAIL, PASSWORD, "5566778899", "user", all_db_path, email_verified=False)
    assert verification_client.post("/verify-email/resend", data={
        "email": EMAIL.upper(), "password": PASSWORD, "csrf_token": _csrf(verification_client),
    }).status_code == 302
    assert len(verification_mailbox) == 1
    assert verification_mailbox[0][0] == EMAIL


@pytest.mark.parametrize("delivery", ["disabled", "failed"])
def test_failed_delivery_keeps_account_unverified_and_allows_retry(
    verification_client, verification_mailbox, all_db_path, monkeypatch, delivery,
):
    monkeypatch.setattr("users.routes.gen_func.send_verification_mail", lambda *args: delivery)
    response = _signup(verification_client)
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    response = verification_client.get(response.location)
    html = response.get_data(as_text=True)
    _assert_verification_banner(response)
    assert ("avstäng" in html.lower()) if delivery == "disabled" else ("kunde inte" in html.lower())
    assert database.check_user_exists(EMAIL, all_db_path)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    _assert_signed_out(verification_client)

    def send(email, url):
        verification_mailbox.append((email, url))
        return "sent"

    monkeypatch.setattr("users.routes.gen_func.send_verification_mail", send)
    assert verification_client.post("/verify-email/resend", data={
        "email": EMAIL, "password": PASSWORD, "csrf_token": _csrf(verification_client),
    }).status_code == 302
    assert len(verification_mailbox) == 1
    assert database.get_email_verification(_mail_token(verification_mailbox), all_db_path)


@pytest.mark.parametrize("role", ["user", "admin"])
def test_unverified_accounts_cannot_use_a_preexisting_session(verification_client, all_db_path, role):
    user = database.create_user("Kund", EMAIL, PASSWORD, "5566778899", role, all_db_path, email_verified=False)
    for path in ("/dashboard", "/booking", "/admin/"):
        with verification_client.session_transaction() as state:
            state.update(user_id=user[0], user_name="Kund", user_email=EMAIL,
                         organization_number="5566778899", role=role)
        assert verification_client.get(path).status_code == 302
        _assert_signed_out(verification_client)


def test_verification_does_not_log_token_email_or_password(
    verification_client, verification_mailbox, caplog,
):
    with caplog.at_level(logging.DEBUG):
        assert _signup(verification_client).status_code == 302
        token = _mail_token(verification_mailbox)
        assert verification_client.post("/verify-email", data={
            "verification_token": token, "csrf_token": _csrf(verification_client),
        }).status_code == 302
    messages = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (EMAIL, PASSWORD, token):
        assert secret not in messages
