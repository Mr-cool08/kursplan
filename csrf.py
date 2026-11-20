import hmac
import secrets

from flask import current_app, session


_BOOKING_CSRF_SESSION_KEY = "booking_csrf_token"


def get_booking_csrf_token():
    token = session.get(_BOOKING_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_BOOKING_CSRF_SESSION_KEY] = token
        current_app.logger.debug("Nytt CSRF-skydd skapat för formulär.")
    return token


def validate_booking_csrf_token(submitted_token):
    expected_token = session.get(_BOOKING_CSRF_SESSION_KEY, "")
    valid = bool(
        isinstance(submitted_token, str)
        and isinstance(expected_token, str)
        and submitted_token
        and expected_token
        and submitted_token.isascii()
        and expected_token.isascii()
        and hmac.compare_digest(submitted_token, expected_token)
    )
    if not valid:
        current_app.logger.warning("Formulär nekades: CSRF-kontrollen misslyckades.")
    return valid


def clear_booking_csrf_token():
    session.pop(_BOOKING_CSRF_SESSION_KEY, None)
    current_app.logger.debug("CSRF-skydd förbrukat efter formulärhantering.")
