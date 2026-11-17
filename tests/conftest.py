import os
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

import database


@pytest.fixture(scope="session")
def postgres_dsn():
    if test_url := os.getenv("TEST_DATABASE_URL"):
        yield test_url
        return

    # The embedded server's root mode modifies OS users and directory permissions.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.fail("Run pytest as a regular user or set TEST_DATABASE_URL.", pytrace=False)

    try:
        from embedded_postgres import get_server
    except ModuleNotFoundError as exc:
        if exc.name != "embedded_postgres":
            raise
        pytest.fail("Install test dependencies: pip install -r requirements-test.txt", pytrace=False)

    # On Windows the server drops admin privileges, so inherit the temp folder's ACL.
    data_dir = Path(gettempdir()) / f"kursplan-pg-{uuid4().hex[:16]}"
    data_dir.mkdir(mode=0o755 if os.name == "nt" else 0o700)
    with get_server(data_dir, cleanup_mode="delete") as server:
        yield server.get_uri()


@pytest.fixture
def db_path(postgres_dsn):
    # Database functions open their own connections, so isolate the entire test database.
    name = f"kursplan_test_{uuid4().hex}"
    identifier = sql.Identifier(name)
    test_dsn = make_conninfo(postgres_dsn, dbname=name)
    with psycopg.connect(postgres_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(identifier))
        try:
            database.create_database(test_dsn)
            yield test_dsn
        finally:
            conn.execute(sql.SQL("DROP DATABASE {}").format(identifier))
