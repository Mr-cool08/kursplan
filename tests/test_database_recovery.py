from runpy import run_path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

import database


def test_readiness_and_dashboard_recover_after_postgresql_outage(
    db_path, postgres_dsn, monkeypatch, project_root, csrf_from_html,
):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("db_path", db_path)
    monkeypatch.setenv("app_secret_key", "database-recovery-test-secret")
    monkeypatch.setenv("send_mail", "0")
    monkeypatch.setattr("logging_config.configure_logging", lambda app: None)
    app = run_path(str(project_root / "app.py"))["app"]
    app.config.update(TESTING=True, PREFERRED_URL_SCHEME="https")
    client = app.test_client()

    name = "Testkund"
    email = "database-recovery@example.test"
    password = "database-recovery-test-password"
    organization_number = "5566778899"
    course = "Utbildning för återhämtningstest"
    database.create_user(name, email, password, organization_number, "user", db_path)
    database.create_booking(
        name, email, organization_number, course, 3, "Stockholm", "Sal 1",
        "2030-01-01", db_path,
    )

    csrf_token = csrf_from_html(client.get("/login").get_data(as_text=True))
    login = client.post("/login", data={
        "email": email, "password": password, "csrf_token": csrf_token,
    })
    assert login.status_code == 302
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.get_json() == {"status": "ok"}
    assert ready.headers["Cache-Control"] == "no-store"
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert course in dashboard.get_data(as_text=True)

    # Refuse new connections only to the fixture's disposable database.
    database_name = sql.Identifier(conninfo_to_dict(db_path)["dbname"])
    with psycopg.connect(postgres_dsn, autocommit=True, connect_timeout=5) as maintenance:
        maintenance.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(database_name))
        try:
            ready = client.get("/ready")
            assert ready.status_code == 503
            assert ready.get_json() == {"status": "unavailable"}
            assert ready.headers["Cache-Control"] == "no-store"
            assert client.get("/health").status_code == 200
        finally:
            maintenance.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(database_name))

    assert client.get("/ready").status_code == 200
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert course in dashboard.get_data(as_text=True)
