import json
import os

import pytest

from dev_accounts import load_dev_accounts


@pytest.fixture
def accounts_path(tmp_path, monkeypatch):
    for setting in ("test_email", "test_password", "dev_user_email", "dev_user_password"):
        monkeypatch.delenv(setting, raising=False)
    return tmp_path / ".dev-accounts.json"


def test_generates_distinct_passwords_and_reuses_saved_accounts(accounts_path):
    accounts = load_dev_accounts(accounts_path)

    assert accounts["admin"]["email"] == "admin@example.invalid"
    assert accounts["user"]["email"] == "user@example.invalid"
    assert all(len(account["password"]) >= 32 for account in accounts.values())
    assert accounts["admin"]["password"] != accounts["user"]["password"]
    assert json.loads(accounts_path.read_text(encoding="utf-8")) == accounts
    assert load_dev_accounts(accounts_path) == accounts
    if os.name != "nt":
        assert accounts_path.stat().st_mode & 0o777 == 0o600


def test_environment_settings_override_saved_accounts(accounts_path, monkeypatch):
    original = load_dev_accounts(accounts_path)
    settings = {
        "test_email": "configured-admin@example.com",
        "test_password": "configured-admin-test-password",
        "dev_user_email": "configured-user@example.com",
        "dev_user_password": "configured-user-test-password",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)

    accounts = load_dev_accounts(accounts_path)

    assert accounts == {
        "admin": {"email": settings["test_email"], "password": settings["test_password"]},
        "user": {"email": settings["dev_user_email"], "password": settings["dev_user_password"]},
    }
    assert accounts != original
    assert json.loads(accounts_path.read_text(encoding="utf-8")) == accounts


def test_duplicate_emails_do_not_overwrite_saved_credentials(accounts_path, monkeypatch):
    original = load_dev_accounts(accounts_path)
    monkeypatch.setenv("test_email", "Same@example.com")
    monkeypatch.setenv("dev_user_email", "same@example.com")

    with pytest.raises(ValueError, match="olika e-postadresser"):
        load_dev_accounts(accounts_path)

    assert json.loads(accounts_path.read_text(encoding="utf-8")) == original


def test_whitespace_email_settings_use_default_addresses(accounts_path, monkeypatch):
    monkeypatch.setenv("test_email", "  ")
    monkeypatch.setenv("dev_user_email", "\t ")

    accounts = load_dev_accounts(accounts_path)

    assert accounts["admin"]["email"] == "admin@example.invalid"
    assert accounts["user"]["email"] == "user@example.invalid"


@pytest.mark.parametrize("contents", ["not json", "[]", '{"admin": "invalid"}', '{"user": {"password": 123}}'])
def test_invalid_saved_credentials_are_not_replaced(accounts_path, contents):
    accounts_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError):
        load_dev_accounts(accounts_path)

    assert accounts_path.read_text(encoding="utf-8") == contents
