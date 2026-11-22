from time import time

from flask import current_app, session

import database


_TOKEN_KEY = "login_session_token"


def _session_times():
    now = int(time())
    lifetime = int(current_app.permanent_session_lifetime.total_seconds())
    return now, now + lifetime


def start_login_session(user_id):
    now, expires_at = _session_times()
    token = database.create_login_session(
        user_id, current_app.config["DB_PATH"], now=now, expires_at=expires_at,
        previous_token=session.get(_TOKEN_KEY),
    )
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session[_TOKEN_KEY] = token


def refresh_authenticated_session():
    if "user_id" not in session and _TOKEN_KEY not in session:
        return

    user_id = session.get("user_id")
    token = session.get(_TOKEN_KEY)
    if type(user_id) is int and token:
        now, expires_at = _session_times()
        if database.refresh_login_session(
            token, user_id, current_app.config["DB_PATH"], now=now, expires_at=expires_at,
        ):
            return

    # Cookies issued before login sessions were introduced require a new login.
    session.clear()


def end_login_session():
    if token := session.get(_TOKEN_KEY):
        database.revoke_login_session(token, current_app.config["DB_PATH"])
    session.clear()
