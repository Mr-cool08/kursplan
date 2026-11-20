"""Kontoregistrering, inloggningsfel och sessioners livscykel via riktiga routes."""
from urllib.parse import urlsplit

import pytest
from argon2 import PasswordHasher

import database
from users.routes import _safe_next


EMAIL = "account-scenario@example.test"
PASSWORD = "test-password-with-åäö"
SIGNUP = {
    "name": "Åsa Andersson", "email": EMAIL, "password": PASSWORD,
    "organization_number": "5566778899",
}


@pytest.fixture
def account_client(scenario_app):
    return scenario_app.test_client()


def _token(client, path="/login"):
    assert client.get(path).status_code == 200
    with client.session_transaction() as state:
        return state["booking_csrf_token"]


def _login(client, **fields):
    return client.post("/login", data={
        "csrf_token": _token(client), "email": EMAIL, "password": PASSWORD, **fields,
    })


@pytest.mark.parametrize(("field", "value"), [
    ("name", ""), ("name", " \t "),
    ("email", ""), ("email", "utan-snabel-a"), ("email", "a@localhost"),
    ("email", "a b@example.test"), ("email", "a@@example.test"),
    ("password", ""), ("organization_number", ""), ("organization_number", " \n "),
])
def test_invalid_signup_never_creates_account(account_client, all_db_path, field, value):
    response = account_client.post("/signup", data={
        **SIGNUP, field: value, "csrf_token": _token(account_client, "/signup"),
    })
    assert response.status_code == 400
    assert "Fyll i namn, giltig e-postadress" in response.get_data(as_text=True)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] == 0
    with account_client.session_transaction() as state:
        assert "user_id" not in state


def test_signup_trims_identity_forces_customer_role_and_replaces_old_session(account_client, all_db_path, monkeypatch):
    monkeypatch.setattr("users.routes.gen_func.send_verification_mail", lambda *args: "sent")
    csrf = _token(account_client, "/signup")
    with account_client.session_transaction() as state:
        state.update(role="admin", user_id=987654, unrelated_secret="old-session")
    response = account_client.post("/signup", data={
        **SIGNUP, "name": "  Åsa Andersson  ", "email": f"  {EMAIL.upper()}  ",
        "organization_number": " 5566778899 ", "role": "admin", "status": "accepted", "email_verified": "1",
        "csrf_token": csrf,
    })
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    with account_client.session_transaction() as state:
        assert "user_id" not in state
        assert "user_name" not in state
        assert "user_email" not in state
        assert "organization_number" not in state
        assert "role" not in state
        assert "unrelated_secret" not in state
        assert "booking_csrf_token" not in state
        assert state["email_verification_pending"] is True
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "SELECT id, password, status FROM users WHERE email = %s", (EMAIL,))
        user_id, stored, status = cursor.fetchone()
    identity = database.get_user_identity(user_id, all_db_path)
    assert identity["role"] == "user"
    assert identity["name"] == SIGNUP["name"]
    assert identity["email"] == EMAIL
    assert identity["organization_number"] == SIGNUP["organization_number"]
    assert not identity["email_verified"]
    assert stored != PASSWORD and PasswordHasher().verify(stored, PASSWORD)
    assert status == "pending"
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    assert account_client.get("/admin/").status_code == 302


def test_duplicate_signup_preserves_existing_identity_and_password(account_client, all_db_path):
    existing = database.create_user("Befintlig", EMAIL, "original-password", "original-org", "admin", all_db_path)
    before = database.get_user_identity(existing[0], all_db_path)
    response = account_client.post("/signup", data={
        **SIGNUP, "csrf_token": _token(account_client, "/signup"),
    })
    assert response.status_code == 400
    assert "Det finns redan ett konto" in response.get_data(as_text=True)
    assert database.get_user_identity(existing[0], all_db_path) == before
    assert database.authenticate_user(EMAIL, "original-password", all_db_path)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    with account_client.session_transaction() as state:
        assert "user_id" not in state


@pytest.mark.parametrize("fields", [
    {"email": ""}, {"email": " \t "}, {"password": ""},
    {"email": "unknown@example.test"}, {"password": "wrong-password"},
    {"email": "' OR 1=1 --", "password": "' OR 1=1 --"},
])
def test_failed_login_does_not_authenticate_or_echo_password(account_client, all_db_path, fields):
    database.create_user("Kund", EMAIL, PASSWORD, "5566778899", "user", all_db_path)
    response = _login(account_client, **fields)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'role="alert"' in html
    assert PASSWORD not in html
    assert "wrong-password" not in html
    with account_client.session_transaction() as state:
        assert "user_id" not in state
    assert account_client.get("/dashboard").status_code == 302


@pytest.mark.parametrize(("role", "destination"), [("user", "/dashboard"), ("admin", "/admin/")])
def test_login_uses_database_role_trims_email_and_rotates_session(account_client, all_db_path, role, destination):
    user = database.create_user("Kund", EMAIL, PASSWORD, "5566778899", role, all_db_path)
    csrf = _token(account_client)
    with account_client.session_transaction() as state:
        state.update(user_name="Gammalt namn", role="forged", stale_value="must disappear",
                     email_verification_pending=True)
    response = account_client.post("/login", data={
        "csrf_token": csrf, "email": f"  {EMAIL} \t", "password": PASSWORD, "role": "admin",
    })
    assert response.status_code == 302
    assert urlsplit(response.location).path == destination
    with account_client.session_transaction() as state:
        assert state["user_id"] == user[0]
        assert state["user_name"] == "Kund"
        assert state["role"] == role
        assert "stale_value" not in state
        assert "booking_csrf_token" not in state
        assert "email_verification_pending" not in state
    assert account_client.get("/").location.endswith(destination)
    assert account_client.get("/login").location.endswith(destination)


def test_login_preserves_access_to_legacy_accounts_with_case_colliding_emails(account_client, all_db_path):
    mixed_email = "Legacy.Case@example.test"
    lower_email = mixed_email.lower()
    original = database.create_user("Äldre kund", mixed_email, "original-password", "1111111111", "user", all_db_path)
    other = database.create_user("Annan kund", lower_email, "other-password", "2222222222", "user", all_db_path)
    assert original[0] != other[0]
    response = account_client.post("/login", data={
        "csrf_token": _token(account_client), "email": mixed_email, "password": "original-password",
    })
    assert response.status_code == 302
    with account_client.session_transaction() as state:
        assert state["user_id"] == original[0]
        assert state["user_email"] == mixed_email


@pytest.mark.parametrize("role", [None, "", "superadmin", "USER"])
def test_login_rejects_unknown_database_roles(account_client, all_db_path, role):
    database.create_user("Fel roll", EMAIL, PASSWORD, "5566778899", role, all_db_path)
    response = _login(account_client)
    assert response.status_code == 200
    assert "Felaktig e-postadress eller lösenord" in response.get_data(as_text=True)
    with account_client.session_transaction() as state:
        assert "user_id" not in state


@pytest.mark.parametrize("path", ["/", "/login", "/dashboard", "/booking", "/bookings/999999"])
@pytest.mark.parametrize("account_state", ["deleted", "invalid-role"])
def test_stale_customer_session_is_cleared(account_client, all_db_path, path, account_state):
    user = database.create_user("Kund", EMAIL, PASSWORD, "5566778899", "user", all_db_path)
    assert _login(account_client).status_code == 302
    with database._cursor(all_db_path) as cursor:
        if account_state == "deleted":
            database._execute(cursor, "DELETE FROM users WHERE id = %s", (user[0],))
        else:
            database._execute(cursor, "UPDATE users SET role = 'invalid-role' WHERE id = %s", (user[0],))
    response = account_client.get(path)
    assert response.status_code == (200 if path in {"/", "/login"} else 302)
    with account_client.session_transaction() as state:
        assert "user_id" not in state
        assert "role" not in state
        assert "user_email" not in state


@pytest.mark.parametrize(("method", "status"), [("get", 302), ("post", 307)])
def test_old_admin_login_redirect_preserves_post_method(account_client, method, status):
    response = getattr(account_client, method)("/admin/login", data={"password": "never-echo"})
    assert response.status_code == status
    assert response.location.endswith("/login")
    assert "never-echo" not in response.get_data(as_text=True)


@pytest.mark.parametrize("target", [
    None, "", "relative", "/", "/dashboard", "/admin/", "/bookings/0", "/bookings/-1",
    "/bookings/01", "/bookings/1/", "/bookings/1/../2", "/bookings/1#\nunsafe",
    "https://evil.example/bookings/1", "//evil.example/bookings/1", "/%2Fevil.example/bookings/1",
    "/bookings/1%00", "/bookings/1%0d%0aLocation:https://evil.example", "/bookings/1\\evil",
    "/bookings/" + "1" * 2001,
])
def test_safe_next_rejects_ambiguous_and_non_booking_destinations(target):
    assert _safe_next(target) is None


@pytest.mark.parametrize("target", [
    "/bookings/1", "/bookings/2147483647", "/bookings/12?action=accept&proposal_id=7",
])
def test_safe_next_preserves_valid_booking_destination(target):
    assert _safe_next(target) == target
