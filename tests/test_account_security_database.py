"""Lösenordsmigrering, engångslänkar och sessioner på SQLite och PostgreSQL."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from argon2 import PasswordHasher
from argon2.low_level import Type
from argon2 import extract_parameters
import pytest
from werkzeug.security import generate_password_hash

import database


EMAIL = "security-account@example.test"
PASSWORD = "Ett långt lösenord med åäö"


def _create(db_path, *, verified=True, email=EMAIL):
    return database.create_user("Testkund", email, PASSWORD, "5566778899", "user",
                                db_path, email_verified=verified)


def _stored(db_path, user_id):
    with database._cursor(db_path) as cursor:
        database._execute(cursor, "SELECT password FROM users WHERE id = %s", (user_id,))
        return cursor.fetchone()[0]


def _replace_password(db_path, user_id, stored):
    with database._cursor(db_path) as cursor:
        database._execute(cursor, "UPDATE users SET password = %s WHERE id = %s", (stored, user_id))


def test_password_writes_use_argon2id_with_independent_salts(all_db_path):
    first = _create(all_db_path)
    second = _create(all_db_path, email="other-security-account@example.test")
    one, two = _stored(all_db_path, first[0]), _stored(all_db_path, second[0])
    assert one != two
    assert PasswordHasher().verify(one, PASSWORD)
    assert PasswordHasher().verify(two, PASSWORD)
    params = extract_parameters(one)
    assert params.type is Type.ID
    assert (params.memory_cost, params.time_cost, params.parallelism) == (65536, 3, 4)
    assert (params.salt_len, params.hash_len) == (16, 32)
    assert database.authenticate_user(EMAIL, one, all_db_path) is None
    assert database.authenticate_user(EMAIL, "fel lösenord", all_db_path) is None


def test_bootstrap_admin_password_is_hashed_without_overwriting(all_db_path):
    database.ensure_test_user(EMAIL, PASSWORD, all_db_path)
    user = database.authenticate_user(EMAIL, PASSWORD, all_db_path)
    assert user[-1] == "admin"
    before = _stored(all_db_path, user[0])
    assert PasswordHasher().verify(before, PASSWORD)
    database.ensure_test_user(EMAIL, "replacement-password", all_db_path)
    assert _stored(all_db_path, user[0]) == before


@pytest.mark.parametrize("old_password", [PASSWORD, "scrypt:äldre-lösenord", "pbkdf2:äldre-lösenord",
                                        "$argon2something", "$argon2id:äldre-lösenord"])
def test_startup_migrates_plaintext_once_and_keeps_identity(all_db_path, old_password):
    user = _create(all_db_path)
    before_identity = database.get_user_identity(user[0], all_db_path)
    _replace_password(all_db_path, user[0], old_password)
    database.create_database(all_db_path)
    hashed = _stored(all_db_path, user[0])
    assert PasswordHasher().verify(hashed, old_password)
    assert database.get_user_identity(user[0], all_db_path) == before_identity
    assert database.authenticate_user(EMAIL, old_password, all_db_path)
    database.create_database(all_db_path)
    assert _stored(all_db_path, user[0]) == hashed


def test_schema_migration_grandfathers_existing_users_and_keeps_new_pending(all_db_path):
    # This is an isolated, disposable test database; emulate the previous schema.
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "ALTER TABLE users DROP COLUMN email_verified")
        database._execute(cursor, "INSERT INTO users (name, email, password, role) "
                         "VALUES (%s, %s, %s, %s) RETURNING id",
                         ("Äldre kund", EMAIL, PASSWORD, "user"))
        user_id = cursor.fetchone()[0]
    database.create_database(all_db_path)
    assert database.get_user_identity(user_id, all_db_path)["email_verified"] == 1
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path)
    pending = _create(all_db_path, verified=False, email="pending@example.test")
    database.create_database(all_db_path)
    assert database.get_user_identity(pending[0], all_db_path)["email_verified"] == 0


@pytest.mark.parametrize("method", ["scrypt", "pbkdf2:sha256:600000"])
def test_legacy_hash_is_upgraded_only_after_correct_password(all_db_path, method):
    user = _create(all_db_path)
    legacy = generate_password_hash(PASSWORD, method=method)
    _replace_password(all_db_path, user[0], legacy)
    database.create_database(all_db_path)
    assert _stored(all_db_path, user[0]) == legacy
    assert database.authenticate_user(EMAIL, legacy, all_db_path) is None
    assert database.authenticate_user(EMAIL, "fel lösenord", all_db_path) is None
    assert _stored(all_db_path, user[0]) == legacy
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path)
    assert PasswordHasher().verify(_stored(all_db_path, user[0]), PASSWORD)


def test_older_argon2_parameters_are_upgraded_after_successful_login(all_db_path):
    user = _create(all_db_path)
    older = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(PASSWORD)
    _replace_password(all_db_path, user[0], older)
    assert database.authenticate_user(EMAIL, "fel lösenord", all_db_path) is None
    assert _stored(all_db_path, user[0]) == older
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path)
    upgraded = _stored(all_db_path, user[0])
    assert PasswordHasher().verify(upgraded, PASSWORD)
    assert not PasswordHasher().check_needs_rehash(upgraded)


@pytest.mark.parametrize("malformed", ["$argon2id$bad", "scrypt:invalid$salt$hash",
                                      "scrypt:1:bad:1$salt$hash", "pbkdf2:unknown:1$salt$hash"])
def test_corrupt_hashes_fail_closed(all_db_path, malformed):
    user = _create(all_db_path)
    _replace_password(all_db_path, user[0], malformed)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    assert database.authenticate_user(EMAIL, malformed, all_db_path) is None


def test_unverified_account_cannot_login_and_token_is_stored_only_as_digest(all_db_path, caplog, monkeypatch):
    monkeypatch.setattr(database.time, "time", lambda: 1800000000)
    user = _create(all_db_path, verified=False)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    assert database.login_user(EMAIL, PASSWORD, "user", all_db_path) is None
    token = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    assert len(token) == 43
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "SELECT user_id, token_hash, expires_at, sent_at FROM user_email_verifications")
        assert cursor.fetchall() == [(user[0], hashlib.sha256(token.encode()).hexdigest(), 1800086400, 1800000000)]
    assert token not in caplog.text and PASSWORD not in caplog.text
    assert database.get_email_verification(token, all_db_path)
    assert database.get_email_verification(token, all_db_path)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None
    assert database.verify_email_token(token, all_db_path)
    assert not database.verify_email_token(token, all_db_path)
    assert not database.get_email_verification(token, all_db_path)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path)
    assert database.issue_email_verification(EMAIL, PASSWORD, all_db_path) is None


def test_resend_requires_credentials_throttles_rotates_and_expires(all_db_path, monkeypatch):
    now = [1800000000]
    monkeypatch.setattr(database.time, "time", lambda: now[0])
    _create(all_db_path, verified=False)
    assert database.issue_email_verification(EMAIL, "fel lösenord", all_db_path) is None
    assert database.issue_email_verification("unknown@example.test", PASSWORD, all_db_path) is None
    original = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    assert database.issue_email_verification(EMAIL, PASSWORD, all_db_path) is None
    now[0] += 59
    assert database.issue_email_verification(EMAIL, PASSWORD, all_db_path) is None
    now[0] += 1
    replacement = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    assert replacement != original
    assert not database.verify_email_token(original, all_db_path)
    assert database.get_email_verification(replacement, all_db_path)
    now[0] += 86400
    assert not database.get_email_verification(replacement, all_db_path)
    assert not database.verify_email_token(replacement, all_db_path)
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path) is None


def test_failed_delivery_revokes_only_matching_token_and_allows_retry(all_db_path):
    _create(all_db_path, verified=False)
    token = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    assert not database.revoke_email_verification("a" * 43, all_db_path)
    assert database.get_email_verification(token, all_db_path)
    assert database.revoke_email_verification(token, all_db_path)
    assert not database.get_email_verification(token, all_db_path)
    assert database.issue_email_verification(EMAIL, PASSWORD, all_db_path)


@pytest.mark.parametrize("token", [None, "", "a" * 42, "a" * 44, "å" * 43, "'" * 43, "a" * 43])
def test_invalid_tokens_do_not_verify_accounts(all_db_path, token):
    user = _create(all_db_path, verified=False)
    assert not database.get_email_verification(token, all_db_path)
    assert not database.verify_email_token(token, all_db_path)
    assert database.get_user_identity(user[0], all_db_path)["email_verified"] == 0


def test_email_lookup_preserves_old_case_and_verification_is_atomic(all_db_path):
    _create(all_db_path, verified=False, email=EMAIL.upper())
    assert database.check_user_exists(EMAIL, all_db_path)
    token = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: database.verify_email_token(token, all_db_path), range(2)))
    assert sorted(results) == [False, True]
    assert database.authenticate_user(EMAIL, PASSWORD, all_db_path)[3] == EMAIL.upper()


def test_verification_recipient_uses_stored_email_not_supplied_case(all_db_path):
    _create(all_db_path, verified=False, email=EMAIL.upper())
    token = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    assert database.get_email_verification_recipient(token, all_db_path) == EMAIL.upper()
    assert database.get_email_verification_recipient("a" * 43, all_db_path) is None
    assert database.get_email_verification_recipient("invalid", all_db_path) is None
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "UPDATE user_email_verifications SET expires_at = 0")
    assert database.get_email_verification_recipient(token, all_db_path) is None


def test_concurrent_resend_and_verification_have_one_consistent_outcome(all_db_path, monkeypatch):
    user = _create(all_db_path, verified=False)
    original = database.issue_email_verification(EMAIL, PASSWORD, all_db_path)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "UPDATE user_email_verifications SET sent_at = 0")
    ready = Barrier(2)
    check_password = database._password_identity

    def synchronized_credentials(*args, **kwargs):
        identity = check_password(*args, **kwargs)
        ready.wait(timeout=10)
        return identity

    def verify():
        ready.wait(timeout=10)
        return database.verify_email_token(original, all_db_path)

    # Start both database mutations after the resend's expensive password check.
    monkeypatch.setattr(database, "_password_identity", synchronized_credentials)
    with ThreadPoolExecutor(max_workers=2) as pool:
        resend = pool.submit(database.issue_email_verification, EMAIL, PASSWORD, all_db_path)
        confirm = pool.submit(verify)
        replacement, verified = resend.result(), confirm.result()
    if verified:
        assert replacement is None
        assert database.get_user_identity(user[0], all_db_path)["email_verified"] == 1
    else:
        assert replacement and database.get_email_verification(replacement, all_db_path)
        assert database.get_user_identity(user[0], all_db_path)["email_verified"] == 0
    assert not database.get_email_verification(original, all_db_path)


def test_login_sessions_are_account_bound_and_revoked_independently(all_db_path):
    user_id = _create(all_db_path)[0]
    other_id = _create(all_db_path, email="other-security-account@example.test")[0]
    first = database.create_login_session(user_id, all_db_path, now=1000, expires_at=2000)
    second = database.create_login_session(user_id, all_db_path, now=1000, expires_at=2000)
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "SELECT token_hash FROM login_sessions")
        assert {row[0] for row in cursor.fetchall()} == {
            hashlib.sha256(token.encode("ascii")).hexdigest() for token in (first, second)
        }

    assert not database.refresh_login_session(first, other_id, all_db_path, now=1100, expires_at=2100)
    assert database.refresh_login_session(first, user_id, all_db_path, now=1100, expires_at=2100)
    database.revoke_login_session(first, all_db_path)
    database.revoke_login_session(first, all_db_path)

    assert not database.refresh_login_session(first, user_id, all_db_path, now=1200, expires_at=2200)
    assert database.refresh_login_session(second, user_id, all_db_path, now=1200, expires_at=2200)


def test_login_session_expiration_advances_monotonically_and_cannot_be_reversed(all_db_path):
    user_id = _create(all_db_path)[0]
    token = database.create_login_session(user_id, all_db_path, now=1000, expires_at=2000)

    assert database.refresh_login_session(token, user_id, all_db_path, now=1200, expires_at=2200)
    assert database.refresh_login_session(token, user_id, all_db_path, now=1100, expires_at=2100)
    assert database.refresh_login_session(token, user_id, all_db_path, now=2199, expires_at=3199)
    assert not database.refresh_login_session(token, user_id, all_db_path, now=3199, expires_at=4199)


def test_login_session_rotation_prunes_expired_sessions_and_preserves_other_sessions(all_db_path):
    user_id = _create(all_db_path)[0]
    expired = database.create_login_session(user_id, all_db_path, now=1000, expires_at=1100)
    previous = database.create_login_session(user_id, all_db_path, now=1000, expires_at=2000)
    other = database.create_login_session(user_id, all_db_path, now=1000, expires_at=2000)

    # Startup migrations can run again while browsers have active sessions.
    database.create_database(all_db_path)
    current = database.create_login_session(
        user_id, all_db_path, now=1100, expires_at=2100, previous_token=previous,
    )

    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "SELECT token_hash FROM login_sessions")
        assert len(cursor.fetchall()) == 2
    assert not database.refresh_login_session(expired, user_id, all_db_path, now=1100, expires_at=2100)
    assert not database.refresh_login_session(previous, user_id, all_db_path, now=1100, expires_at=2100)
    assert database.refresh_login_session(other, user_id, all_db_path, now=1100, expires_at=2100)
    assert database.refresh_login_session(current, user_id, all_db_path, now=1100, expires_at=2100)
