import os
from pathlib import Path
import shutil
import subprocess
import sys
from textwrap import dedent

import pytest


@pytest.fixture
def run_app(tmp_path):
    project = Path(__file__).resolve().parents[1]
    for module in ("app.py", "database.py", "gen_func.py"):
        shutil.copy2(project / module, tmp_path)
    for directory in ("admin", "templates", "users"):
        shutil.copytree(
            project / directory,
            tmp_path / directory,
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    def run(dev_mode, code):
        settings = {
            "DEV_MODE": dev_mode,
            "app_secret_key": "dev-mode-test-key",
            "db_path": "postgresql://example.invalid/kursplan",
            "test_email": "admin@example.com",
            "test_password": "test-admin-password",
            "send_mail": "0",
        }
        (tmp_path / ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in settings.items()),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        for key in (*settings, "environment", "FLASK_DEBUG", "PYTHON_DOTENV_DISABLED"):
            environment.pop(key, None)
        environment["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [sys.executable, "-c", dedent(code)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    return run


def test_dev_mode_uses_temporary_database_and_debug_logging(run_app):
    output = run_app(
        "On",
        """
        from contextlib import closing
        import logging
        import sqlite3
        from pathlib import Path
        from unittest.mock import patch

        with patch("psycopg.connect", side_effect=AssertionError("unexpected PostgreSQL connection")):
            import database
            from app import app

            assert app.config["DEV_MODE"] is True
            assert app.logger.isEnabledFor(logging.DEBUG)
            assert not app.debug
            db_path = app.config["DB_PATH"]
            assert isinstance(db_path, Path) and db_path.is_file()

            user = app.test_client()
            signup = user.post("/signup", data={
                "name": "Test User", "email": "user@example.com",
                "password": "test-user-password", "organization_number": "0123456789",
            })
            assert signup.status_code == 302
            user.get("/logout")
            login = user.post("/login", data={
                "email": "user@example.com", "password": "test-user-password",
            })
            assert login.location == "/dashboard"
            assert user.get("/admin/").status_code == 403
            booking = user.post("/booking", data={
                "utbildning": "Test course", "antal": "3", "ort": "Stockholm",
                "lokal": "Sal 1", "datum": "2027-01-01",
            })
            assert booking.status_code == 302
            (row,) = database.get_all_bookings(db_path)
            assert row[4:6] == ("Test course", 3)
            assert row[-1] == "pending"

            # Role changes made directly in the database take effect for the
            # already authenticated session on the next admin request.
            with closing(sqlite3.connect(db_path)) as connection, connection:
                connection.execute(
                    "UPDATE users SET role = 'admin' WHERE email = 'user@example.com'"
                )
            assert user.get("/admin/").status_code == 200
            with closing(sqlite3.connect(db_path)) as connection, connection:
                connection.execute(
                    "UPDATE users SET role = 'user' WHERE email = 'user@example.com'"
                )
            assert user.get("/admin/").status_code == 403

            admin = app.test_client()
            assert admin.get("/admin/").location == "/login"
            assert admin.get("/admin/login").location == "/login"
            legacy_login = admin.post("/admin/login", data={
                "email": "admin@example.com", "password": "test-admin-password",
            })
            assert legacy_login.status_code == 307
            assert legacy_login.location == "/login"
            login = admin.post("/login", data={
                "email": "admin@example.com", "password": "test-admin-password",
            })
            assert login.location == "/admin/"
            accepted = admin.post("/admin/bookings", data={
                "booking_ids": str(row[0]), "date": "2027-01-02", "ort": "Uppsala",
            })
            assert accepted.status_code == 302
            (row,) = database.get_all_bookings(db_path)
            assert (row[6], *row[-2:]) == ("Uppsala", "2027-01-02", "accepted")
            print(db_path)
        """,
    )

    db_path = Path(output.strip().splitlines()[-1])
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_dev_mode_off_uses_configured_postgresql_database(run_app):
    run_app(
        "false",
        """
        import logging
        from unittest.mock import patch

        with patch("psycopg.connect", side_effect=AssertionError("unexpected PostgreSQL connection")):
            from app import app

            assert app.config["DEV_MODE"] is False
            assert app.config["DB_PATH"] == "postgresql://example.invalid/kursplan"
            assert not app.logger.isEnabledFor(logging.DEBUG)
        """,
    )
