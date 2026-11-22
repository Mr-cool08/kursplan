from unittest.mock import MagicMock, Mock

import psycopg

import database


def test_postgresql_readiness_uses_a_short_connection_and_query_timeout(monkeypatch):
    connection = MagicMock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(psycopg, "connect", connect)

    dsn = "postgresql://example.invalid/readiness"
    assert database.database_is_ready(dsn)

    connect.assert_called_once_with(
        dsn, connect_timeout=2, options="-c statement_timeout=2000", autocommit=True,
    )
    connection.__enter__.return_value.execute.assert_called_once_with("SELECT 1")
    connection.__exit__.assert_called_once()


def test_postgresql_readiness_handles_query_failure(monkeypatch, caplog):
    connection = MagicMock()
    connection.__enter__.return_value.execute.side_effect = psycopg.errors.QueryCanceled(
        "private-test-marker",
    )
    monkeypatch.setattr(psycopg, "connect", Mock(return_value=connection))

    assert not database.database_is_ready("postgresql://example.invalid/readiness")
    assert "QueryCanceled" in caplog.text
    assert "private-test-marker" not in caplog.text


def test_sqlite_readiness_checks_the_existing_file(tmp_path):
    db_path = tmp_path / "utveckling #1.db"
    assert not database.database_is_ready(db_path)
    assert not db_path.exists()

    database.create_database(db_path)
    assert database.database_is_ready(db_path)

    db_path.write_text("invalid database", encoding="utf-8")
    assert not database.database_is_ready(db_path)
