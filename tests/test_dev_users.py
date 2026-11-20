from contextlib import closing
from pathlib import Path
import secrets
import sqlite3

import psycopg
import pytest
from werkzeug.security import check_password_hash

import database


ADMIN_EMAIL = "dev-admin@example.test"
USER_EMAIL = "dev-user@example.test"


@pytest.fixture(params=("sqlite", "postgresql"))
def account_db_path(request, tmp_path):
    if request.param == "postgresql":
        return request.getfixturevalue("db_path")
    path = tmp_path / "dev-users.db"
    database.create_database(path)
    return path


@pytest.fixture
def account_passwords():
    return secrets.token_urlsafe(24), secrets.token_urlsafe(24)


def _users(db_path):
    query = "SELECT id, name, email, password, organization_number, role FROM users ORDER BY email"
    if isinstance(db_path, Path):
        with closing(sqlite3.connect(db_path)) as connection:
            return connection.execute(query).fetchall()
    with psycopg.connect(db_path) as connection:
        return connection.execute(query).fetchall()


def _seed(db_path, passwords):
    database.ensure_dev_users(
        ADMIN_EMAIL, passwords[0], USER_EMAIL, passwords[1], db_path
    )


def test_development_accounts_have_distinct_roles_and_hashed_passwords(
    account_db_path, account_passwords
):
    _seed(account_db_path, account_passwords)

    rows = _users(account_db_path)
    assert len(rows) == 2
    for row, role, password in zip(rows, ("admin", "user"), account_passwords):
        assert row[3] != password
        assert check_password_hash(row[3], password)
        assert row[4] == "0000000000"
        assert row[5] == role
        assert database.authenticate_user(row[2], password, account_db_path) == (
            row[0], row[1], row[4], row[2], role
        )
        assert database.login_user(row[2], password, role, account_db_path) == (
            row[0], row[1], row[4]
        )
        assert database.authenticate_user(
            row[2], "incorrect-password", account_db_path
        ) is None
        assert database.authenticate_user(row[2], row[3], account_db_path) is None
        other_role = "user" if role == "admin" else "admin"
        assert database.login_user(row[2], password, other_role, account_db_path) is None

    assert database.authenticate_user(
        "missing@example.test", account_passwords[0], account_db_path
    ) is None


def test_development_account_creation_is_idempotent(account_db_path, account_passwords):
    _seed(account_db_path, account_passwords)
    original_rows = _users(account_db_path)

    _seed(account_db_path, (secrets.token_urlsafe(24), secrets.token_urlsafe(24)))

    assert _users(account_db_path) == original_rows
    assert database.authenticate_user(ADMIN_EMAIL, account_passwords[0], account_db_path)
    assert database.authenticate_user(USER_EMAIL, account_passwords[1], account_db_path)


def test_development_accounts_preserve_conflicting_existing_accounts(
    account_db_path, account_passwords
):
    original_admin_password = secrets.token_urlsafe(24)
    original_user_password = secrets.token_urlsafe(24)
    database.create_user(
        "Befintlig kund", ADMIN_EMAIL, original_admin_password,
        "5566778899", "user", account_db_path,
    )
    database.create_user(
        "Befintlig administratör", USER_EMAIL, original_user_password,
        "1122334455", "admin", account_db_path,
    )
    original_rows = _users(account_db_path)

    _seed(account_db_path, account_passwords)

    assert _users(account_db_path) == original_rows
    assert database.authenticate_user(
        ADMIN_EMAIL, original_admin_password, account_db_path
    )[-1] == "user"
    assert database.authenticate_user(
        USER_EMAIL, original_user_password, account_db_path
    )[-1] == "admin"
    assert database.authenticate_user(ADMIN_EMAIL, account_passwords[0], account_db_path) is None
    assert database.authenticate_user(USER_EMAIL, account_passwords[1], account_db_path) is None


@pytest.mark.parametrize("other_email", (ADMIN_EMAIL, ADMIN_EMAIL.upper(), f" {ADMIN_EMAIL} "))
def test_development_accounts_reject_same_email_before_writing(
    account_db_path, account_passwords, other_email
):
    with pytest.raises(ValueError, match="olika e-postadresser"):
        database.ensure_dev_users(
            ADMIN_EMAIL, account_passwords[0], other_email,
            account_passwords[1], account_db_path,
        )

    assert _users(account_db_path) == []


@pytest.mark.parametrize(
    "password",
    ("Befintligt lösenord med åäö", "scrypt:äldre-lösenord", "pbkdf2:äldre-lösenord"),
)
def test_authentication_preserves_existing_plaintext_passwords(account_db_path, password):
    user = database.create_user(
        "Äldre användare", USER_EMAIL, password, "0123456789", "user", account_db_path
    )

    assert database.authenticate_user(USER_EMAIL, password, account_db_path) == (
        *user[:3], USER_EMAIL, "user"
    )
    assert database.authenticate_user(USER_EMAIL, "fel lösenord", account_db_path) is None
    assert _users(account_db_path)[0][3] == password
