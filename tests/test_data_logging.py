import logging
from smtplib import SMTPAuthenticationError
from unittest.mock import MagicMock

import pytest

import database
import gen_func
from dev_accounts import load_dev_accounts


def test_database_logs_operations_without_sql_values(tmp_path, caplog):
    path = tmp_path / "logging.db"
    with caplog.at_level(logging.DEBUG, logger="database"):
        database.create_database(path)
        database.create_user(
            "Hemligt kundnamn", "private-customer@example.test", "private-password",
            "private-organization", "user", path,
        )
        assert database.check_user_exists("private-customer@example.test", path)

    assert "operation=create_user backend=sqlite" in caplog.text
    assert "typ=INSERT" in caplog.text
    assert any(record.levelno == logging.INFO for record in caplog.records)
    for private in ("Hemligt kundnamn", "private-customer@example.test", "private-password",
                    "private-organization", "INSERT INTO", "SELECT id FROM"):
        assert private not in caplog.text


def test_failed_database_transaction_does_not_log_success_or_error_values(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="database"):
        with pytest.raises(RuntimeError):
            with database._cursor(tmp_path / "failed.db", "create_user"):
                raise RuntimeError("private-password private-customer@example.test")

    assert "Databastransaktion misslyckades" in caplog.text
    assert "feltyp=RuntimeError" in caplog.text
    assert "Databastransaktion slutförd" not in caplog.text
    assert "private-password" not in caplog.text
    assert "private-customer@example.test" not in caplog.text


@pytest.fixture
def logging_smtp(monkeypatch):
    for name, value in {
        "send_mail": "1", "smtp_email": "private-sender@example.test",
        "smtp_server_address": "private-smtp.example.test", "smtp_port": "587",
        "smtp_password": "private-smtp-password", "DEBUG": "true",
    }.items():
        monkeypatch.setenv(name, value)
    smtp = MagicMock()
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    return smtp


def test_smtp_protocol_debug_stays_off_when_application_debug_is_on(logging_smtp, caplog):
    with caplog.at_level(logging.DEBUG, logger="gen_func"):
        assert gen_func.send_booked_mail(
            "private-recipient@example.test", "2030-01-01", "Privat ort", 42, "Privat kurs",
        )

    logging_smtp.return_value.__enter__.return_value.set_debuglevel.assert_called_once_with(0)
    assert "SMTP-autentisering lyckades" in caplog.text
    assert "Bokningsbekräftelse skickades" in caplog.text
    for private in ("private-smtp-password", "private-recipient@example.test",
                    "private-sender@example.test", "Privat ort", "Privat kurs"):
        assert private not in caplog.text


def test_smtp_failure_logs_exception_type_without_server_response(logging_smtp, caplog):
    server = logging_smtp.return_value.__enter__.return_value
    server.login.side_effect = SMTPAuthenticationError(535, b"private-smtp-password private-recipient@example.test")
    with caplog.at_level(logging.DEBUG, logger="gen_func"):
        with pytest.raises(SMTPAuthenticationError):
            gen_func.send_booked_mail("private-recipient@example.test", "2030-01-01", "Ort", 42, "Kurs")

    assert "feltyp=SMTPAuthenticationError" in caplog.text
    assert "private-smtp-password" not in caplog.text
    assert "private-recipient@example.test" not in caplog.text


def test_invalid_development_accounts_do_not_log_file_contents(tmp_path, caplog):
    path = tmp_path / ".dev-accounts.json"
    path.write_text("private-development-password", encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger="dev_accounts"):
        with pytest.raises(ValueError):
            load_dev_accounts(path)

    assert "Utvecklingskonton kunde inte läsas" in caplog.text
    assert "private-development-password" not in caplog.text
