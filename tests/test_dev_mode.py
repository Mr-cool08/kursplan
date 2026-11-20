import os
from pathlib import Path
import shutil
import subprocess
import sys
from textwrap import dedent

import pytest

import database


@pytest.fixture
def run_app(tmp_path):
    project = Path(__file__).resolve().parents[1]
    for module in (
        "app.py",
        "csrf.py",
        "database.py",
        "dev_accounts.py",
        "gen_func.py",
        "utbildningar.json",
    ):
        shutil.copy2(project / module, tmp_path)
    for directory in ("admin", "static", "templates", "users"):
        shutil.copytree(
            project / directory,
            tmp_path / directory,
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    def run(dev_mode, code, **overrides):
        settings = {
            "DEV_MODE": dev_mode,
            "app_secret_key": "dev-mode-test-key",
            "db_path": "postgresql://example.invalid/kursplan",
            "test_email": "admin@example.com",
            "test_password": "test-admin-password",
            "dev_user_email": None,
            "dev_user_password": None,
            "send_mail": "0",
        }
        settings.update(overrides)
        (tmp_path / ".env").write_text(
            "".join(
                f"{key}={value}\n" for key, value in settings.items() if value is not None
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        for key in (*settings, "environment", "FLASK_DEBUG", "PYTHON_DOTENV_DISABLED"):
            environment.pop(key, None)
        environment["PYTHONIOENCODING"] = "utf-8"

        csrf_helper = dedent('''
            def csrf(client, path):
                client.get(path)
                with client.session_transaction() as state:
                    return state["booking_csrf_token"]
        ''')
        result = subprocess.run(
            [sys.executable, "-c", csrf_helper + dedent(code)],
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
        from html.parser import HTMLParser
        from io import StringIO
        import logging
        import sqlite3
        from pathlib import Path
        from unittest.mock import patch

        from psycopg import OperationalError
        from psycopg.conninfo import conninfo_to_dict

        logs = StringIO()
        logging.getLogger("app").addHandler(logging.StreamHandler(logs))
        with patch("psycopg.connect", side_effect=OperationalError("secret-test-marker")) as connect:
            import database
            from app import app

            connect.assert_called_once()
            settings = conninfo_to_dict(connect.call_args.args[0])
            assert settings["host"] == "example.invalid"
            assert settings["connect_timeout"] == "5"
            assert "temporär SQLite-fil" in logs.getvalue()
            assert "secret-test-marker" not in logs.getvalue()
            assert app.config["DEV_MODE"] is True
            assert app.logger.isEnabledFor(logging.DEBUG)
            assert not app.debug
            db_path = app.config["DB_PATH"]
            assert isinstance(db_path, Path) and db_path.is_file()
            assert db_path.parent == Path.cwd()
            assert db_path.name.startswith("kursplan-dev-")
            assert db_path.suffix == ".db"

            user = app.test_client()
            health = user.get("/health")
            assert health.status_code == 200
            assert health.get_json() == {"status": "ok"}
            signup = user.post("/signup", data={
                "csrf_token": csrf(user, "/signup"),
                "name": "Test User", "email": "user@example.com",
                "password": "test-user-password", "organization_number": "0123456789",
            })
            assert signup.status_code == 302
            user.get("/logout")
            login = user.post("/login", data={
                "csrf_token": csrf(user, "/login"),
                "email": "user@example.com", "password": "test-user-password",
            })
            assert login.location == "/dashboard"
            assert user.get("/admin/").status_code == 403

            class CsrfParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.token = None

                def handle_starttag(self, tag, attrs):
                    attributes = dict(attrs)
                    if tag == "input" and attributes.get("name") == "csrf_token":
                        self.token = attributes.get("value")

            booking_form = user.get("/booking")
            assert booking_form.status_code == 200
            parser = CsrfParser()
            parser.feed(booking_form.get_data(as_text=True))
            assert parser.token
            booking = user.post("/booking", data={
                "csrf_token": parser.token,
                "datumtyp": "exakt",
                "onskat_datum": "2027-01-01",
                "onskat_startdatum": "",
                "onskat_slutdatum": "",
                "utbildningskalla": "katalog",
                "utbildning": "HLR",
                "egen_utbildning": "",
                "antal": "3",
                "ort": "Stockholm",
                "lokalansvar": "kund",
                "lokaladress": "Sal 1, Testgatan 2",
            })
            assert booking.status_code == 302
            (row,) = database.get_bookings_by_email("user@example.com", db_path)
            assert row[4:6] == ("HLR", 3)
            assert row[-1] == "pending"
            details = database.get_booking_details_by_id(row[0], db_path)
            assert details["datumtyp"] == "exakt"
            assert details["onskat_datum"] == "2027-01-01"
            assert details["onskad_ort"] == "Stockholm"
            assert details["lokalansvar"] == "kund"
            assert details["lokaladress"] == "Sal 1, Testgatan 2"

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
                "csrf_token": csrf(admin, "/login"),
                "email": "admin@example.com", "password": "test-admin-password",
            })
            assert login.location == "/admin/"
            assert admin.get("/admin/").status_code == 200
            proposal = database.create_proposal(
                row[0], "2027-01-02", "Uppsala", "Sal 2", db_path,
            )
            assert database.get_booking_details_by_id(row[0], db_path)["status"] == "awaiting_response"
            accepted = user.post(f"/bookings/{row[0]}/proposals/{proposal['id']}/respond", data={
                "csrf_token": csrf(user, f"/bookings/{row[0]}"), "action": "accept",
            })
            assert accepted.status_code == 302
            (row,) = database.get_bookings_by_email("user@example.com", db_path)
            assert (row[6], *row[-2:]) == ("Uppsala", "2027-01-02", "accepted")
            details = database.get_booking_details_by_id(row[0], db_path)
            assert details["faststallt_datum"] == "2027-01-02"
            assert details["faststalld_ort"] == "Uppsala"
            assert details["onskat_datum"] == "2027-01-01"
            assert details["onskad_ort"] == "Stockholm"
            print(db_path)
        """,
    )

    db_path = Path(output.strip().splitlines()[-1])
    assert not db_path.exists()
    assert db_path.parent.is_dir()
    assert (db_path.parent / "app.py").is_file()


def test_dev_mode_uses_configured_postgresql_database_and_preserves_data(run_app, db_path):
    database.create_user(
        "Existing User", "existing@example.com", "existing-password",
        "0123456789", "user", db_path,
    )
    booking_id = database.create_booking(
        "Existing User", "existing@example.com", "0123456789", "HLR", 2,
        "Stockholm", "Sal 1", "2027-01-01", db_path,
    )
    existing_bookings = database.get_all_bookings(db_path)

    run_app(
        "true",
        """
        import json
        import logging
        import os
        from pathlib import Path
        from unittest.mock import patch

        import database
        from psycopg.conninfo import conninfo_to_dict

        with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("unexpected temporary database")):
            from app import app, dev_database_file

        assert dev_database_file is None
        expected_settings = conninfo_to_dict(os.environ.get("db_path", ""))
        # dotenv loaded db_path from the isolated test directory.
        expected_settings["connect_timeout"] = "5"
        assert conninfo_to_dict(app.config["DB_PATH"]) == expected_settings
        assert app.logger.isEnabledFor(logging.DEBUG)
        assert len(database.get_all_bookings(app.config["DB_PATH"])) == 5
        assert len(database.get_bookings_by_email("existing@example.com", app.config["DB_PATH"])) == 1
        accounts = json.loads(Path(".dev-accounts.json").read_text(encoding="utf-8"))
        for role, credentials in accounts.items():
            account = database.authenticate_user(
                credentials["email"], credentials["password"], app.config["DB_PATH"],
            )
            assert account and account[4] == role

        client = app.test_client()
        assert client.get("/health").get_json() == {"status": "ok"}
        login = client.post("/login", data={
            "csrf_token": csrf(client, "/login"),
            "email": "existing@example.com", "password": "existing-password",
        })
        assert login.location == "/dashboard"
        assert client.get("/dashboard").status_code == 200
        assert client.post("/signup", data={
            "csrf_token": csrf(client, "/signup"),
            "name": "New User", "email": "new@example.com",
            "password": "new-password", "organization_number": "0123456789",
        }).status_code == 302
        """,
        db_path=db_path,
    )

    assert database.get_bookings_by_email("existing@example.com", db_path) == existing_bookings
    assert database.get_booking_details_by_id(booking_id, db_path)["status"] == "pending"
    assert database.authenticate_user("existing@example.com", "existing-password", db_path)
    assert database.check_user_exists("new@example.com", db_path)


def test_temporary_database_files_do_not_overwrite_or_remove_existing_files(run_app):
    output = run_app(
        "true",
        """
        from pathlib import Path

        existing_file = Path("kursplan-dev-existing.db")
        existing_file.write_bytes(b"existing database")
        from app import app, initialize_dev_database

        first_path = app.config["DB_PATH"]
        app.config["DB_PATH"] = None
        with initialize_dev_database():
            second_path = app.config["DB_PATH"]
            assert first_path != second_path
            assert first_path.is_file() and second_path.is_file()

        assert not second_path.exists()
        assert first_path.is_file()
        assert existing_file.read_bytes() == b"existing database"
        print(first_path)
        """,
        db_path=None,
    )

    db_path = Path(output.strip())
    assert not db_path.exists()
    assert (db_path.parent / "kursplan-dev-existing.db").read_bytes() == b"existing database"


@pytest.mark.parametrize("configured_path", [None, "", "invalid-connection-string"])
def test_dev_mode_without_valid_connection_settings_uses_temporary_file(run_app, configured_path):
    output = run_app(
        "true",
        """
        import os
        from pathlib import Path
        import sys
        from unittest.mock import patch

        project_root = Path.cwd()
        launch_directory = project_root / "other-directory"
        launch_directory.mkdir()
        os.chdir(launch_directory)
        sys.path.insert(0, str(project_root))

        with patch("psycopg.connect", side_effect=AssertionError("unexpected PostgreSQL connection")):
            from app import app

        db_path = app.config["DB_PATH"]
        assert isinstance(db_path, Path) and db_path.is_file()
        assert db_path.parent == project_root
        assert db_path.parent != Path.cwd()
        assert app.test_client().get("/health").status_code == 200
        print(db_path)
        """,
        db_path=configured_path,
        test_email=None,
        test_password=None,
        app_secret_key=None,
    )

    db_path = Path(output.strip())
    assert not db_path.exists()
    assert db_path.parent.is_dir()
    assert (db_path.parent / "app.py").is_file()


@pytest.mark.parametrize("error_type", ["ProgrammingError", "RuntimeError"])
def test_dev_mode_does_not_hide_initialization_errors(run_app, error_type):
    run_app(
        "true",
        f"""
        from unittest.mock import patch
        from psycopg import ProgrammingError

        error = {error_type}("initialization failure")
        with (
            patch("database.create_database", side_effect=error),
            patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("unexpected fallback")),
        ):
            try:
                import app
            except {error_type} as exc:
                assert exc is error
            else:
                raise AssertionError("initialization error was hidden")
        """,
    )


def test_dev_mode_off_uses_configured_postgresql_database(run_app):
    run_app(
        "false",
        """
        import logging
        from unittest.mock import patch

        from pathlib import Path

        with (
            patch("psycopg.connect", side_effect=AssertionError("unexpected PostgreSQL connection")),
            patch("database.ensure_dev_users", side_effect=AssertionError("unexpected development users")),
            patch("database.ensure_dev_bookings", side_effect=AssertionError("unexpected sample bookings")),
            patch("database.create_database") as create_database,
            patch("database.ensure_test_user") as ensure_test_user,
        ):
            from app import app, initialize_database

            initialize_database()
            create_database.assert_called_once_with("postgresql://example.invalid/kursplan")
            ensure_test_user.assert_called_once_with(
                email="admin@example.com", password="test-admin-password",
                db_path="postgresql://example.invalid/kursplan",
            )

            assert app.config["DEV_MODE"] is False
            assert app.config["DB_PATH"] == "postgresql://example.invalid/kursplan"
            assert not app.logger.isEnabledFor(logging.DEBUG)
            assert not Path(".dev-accounts.json").exists()
        """,
    )


def test_dev_mode_creates_two_usable_accounts_without_settings(run_app):
    run_app(
        "true",
        """
        import json
        from io import StringIO
        import logging
        from pathlib import Path

        logs = StringIO()
        logging.getLogger("app").addHandler(logging.StreamHandler(logs))
        from app import app, initialize_database
        import database

        accounts = json.loads(Path(".dev-accounts.json").read_text(encoding="utf-8"))
        assert set(accounts) == {"admin", "user"}
        original_bookings = database.get_bookings_by_email(
            accounts["user"]["email"], app.config["DB_PATH"],
        )
        assert len(original_bookings) == 4
        assert sorted(row[9] for row in original_bookings) == [
            "accepted", "cancelled", "pending", "pending",
        ]
        original_users = {}
        for role, location in (("admin", "/admin/"), ("user", "/dashboard")):
            credentials = accounts[role]
            assert credentials["password"] not in logs.getvalue()
            original_users[role] = database.authenticate_user(
                credentials["email"], credentials["password"], app.config["DB_PATH"],
            )
            assert original_users[role][4] == role
            client = app.test_client()
            response = client.post("/login", data={**credentials, "csrf_token": csrf(client, "/login")})
            assert response.location == location
            dashboard = client.get(location)
            assert dashboard.status_code == 200
            for booking in original_bookings:
                assert booking[4] in dashboard.get_data(as_text=True)
            if role == "user":
                assert client.get("/admin/").status_code == 403

        initialize_database()
        assert database.get_bookings_by_email(
            accounts["user"]["email"], app.config["DB_PATH"],
        ) == original_bookings
        for role, credentials in accounts.items():
            assert database.authenticate_user(
                credentials["email"], credentials["password"], app.config["DB_PATH"],
            ) == original_users[role]
        assert json.loads(Path(".dev-accounts.json").read_text(encoding="utf-8")) == accounts
        assert app.test_client().get("/health").get_json() == {"status": "ok"}
        """,
        db_path=None,
        test_email=None,
        test_password=None,
    )
