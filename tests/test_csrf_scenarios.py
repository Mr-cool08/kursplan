"""CSRF lifetime and malformed requests, without database or network access."""

from flask import Flask
import pytest

from csrf import (
    clear_booking_csrf_token,
    get_booking_csrf_token,
    validate_booking_csrf_token,
)
from users import user_page


@pytest.fixture
def csrf_app(project_root):
    app = Flask("csrf-scenarios", template_folder=str(project_root / "templates"),
                static_folder=str(project_root / "static"))
    app.config.update(TESTING=True, SECRET_KEY="csrf-scenario-test-key", DB_PATH="unused")
    app.register_blueprint(user_page)
    return app


def test_token_is_stable_until_consumed_and_then_replaced(csrf_app):
    with csrf_app.test_request_context():
        original = get_booking_csrf_token()
        assert len(original) >= 32
        assert original.isascii()
        assert get_booking_csrf_token() == original
        assert validate_booking_csrf_token(original)
        clear_booking_csrf_token()
        assert not validate_booking_csrf_token(original)
        replacement = get_booking_csrf_token()
        assert replacement != original
        assert validate_booking_csrf_token(replacement)
        assert not validate_booking_csrf_token(original)


def test_clearing_absent_token_is_idempotent(csrf_app):
    with csrf_app.test_request_context():
        clear_booking_csrf_token()
        clear_booking_csrf_token()
        assert not validate_booking_csrf_token("never-issued")


@pytest.mark.parametrize("submitted", [None, "", "wrong", "å", "🔒", "a" * 10000,
                                      b"token", 1, ["token"], {"token": "value"}])
def test_malformed_token_is_rejected_without_raising(csrf_app, submitted):
    with csrf_app.test_request_context():
        original = get_booking_csrf_token()
        assert validate_booking_csrf_token(submitted) is False
        assert validate_booking_csrf_token(original)


@pytest.mark.parametrize("stored", [b"token", 1, ["token"], "å"])
def test_invalid_session_token_is_rejected_without_raising(csrf_app, stored):
    from flask import session

    with csrf_app.test_request_context():
        session["booking_csrf_token"] = stored
        assert validate_booking_csrf_token("token") is False


@pytest.mark.parametrize("path", ["/login", "/signup"])
@pytest.mark.parametrize("submitted", ["å", "🔒", "wrong"])
def test_invalid_form_token_returns_400_before_database_access(csrf_app, path, submitted):
    client = csrf_app.test_client()
    assert client.get(path).status_code == 200
    response = client.post(path, data={"csrf_token": submitted})
    assert response.status_code == 400
    assert "Formuläret kunde inte verifieras" in response.get_data(as_text=True)


def test_token_from_another_browser_session_is_rejected(csrf_app, csrf_from_html):
    first = csrf_app.test_client()
    second = csrf_app.test_client()
    first_token = csrf_from_html(first.get("/login").get_data(as_text=True))
    second_token = csrf_from_html(second.get("/login").get_data(as_text=True))
    assert first_token != second_token
    assert second.post("/signup", data={"csrf_token": first_token}).status_code == 400
    # Its own token still reaches the ordinary account validation after the rejection.
    response = second.post("/signup", data={"csrf_token": second_token})
    assert response.status_code == 400
    assert "Fyll i namn" in response.get_data(as_text=True)
