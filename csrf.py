import hmac
import secrets

from flask import session


_BOOKING_CSRF_SESSION_KEY = "booking_csrf_token"


def get_booking_csrf_token():
    token = session.get(_BOOKING_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_BOOKING_CSRF_SESSION_KEY] = token
    return token


def validate_booking_csrf_token(submitted_token):
    expected_token = session.get(_BOOKING_CSRF_SESSION_KEY, "")
    return bool(
        submitted_token
        and expected_token
        and hmac.compare_digest(submitted_token, expected_token)
    )


def clear_booking_csrf_token():
    session.pop(_BOOKING_CSRF_SESSION_KEY, None)
