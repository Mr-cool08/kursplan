"""Verifieringslänkar ska hålla sig i rätt miljö utan att lita på Host."""

from flask import Flask
import pytest

from users import user_page
from users.routes import _verification_url


TOKEN = "a" * 43
VERIFY_PATH = f"/verify-email?token={TOKEN}"


@pytest.fixture
def origin_app(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("app_port", raising=False)
    app = Flask("verification-origin-tests")
    app.config.update(TESTING=False, DEV_MODE=False)
    app.register_blueprint(user_page)
    return app


def _link(app, host="attacker.example"):
    with app.test_request_context("/signup", base_url=f"https://{host}"):
        return _verification_url(TOKEN)


@pytest.mark.parametrize("port", ["80", "4753", "65535"])
def test_development_default_uses_configured_local_port_without_request_host(
    origin_app, monkeypatch, port
):
    origin_app.config["DEV_MODE"] = True
    monkeypatch.setenv("app_port", port)
    assert _link(origin_app) == f"http://127.0.0.1:{port}{VERIFY_PATH}"


def test_development_default_uses_application_default_port(origin_app):
    origin_app.config["DEV_MODE"] = True
    assert _link(origin_app) == f"http://127.0.0.1:80{VERIFY_PATH}"


@pytest.mark.parametrize("port", [None, "4753", "not-a-port"])
def test_production_default_stays_on_public_origin(origin_app, monkeypatch, port):
    if port is not None:
        monkeypatch.setenv("app_port", port)
    assert _link(origin_app) == "https://kursplan.se" + VERIFY_PATH


@pytest.mark.parametrize("dev_mode", [False, True])
def test_explicit_environment_origin_takes_precedence_over_local_defaults(
    origin_app, monkeypatch, dev_mode
):
    origin_app.config["DEV_MODE"] = dev_mode
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://preview.example.test/")
    monkeypatch.setenv("app_port", "not-a-port")
    assert _link(origin_app) == "https://preview.example.test" + VERIFY_PATH


def test_application_origin_takes_precedence_over_environment(origin_app, monkeypatch):
    origin_app.config.update(DEV_MODE=True, PUBLIC_BASE_URL="https://configured.example.test/")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://environment.example.test")
    monkeypatch.setenv("app_port", "not-a-port")
    assert _link(origin_app) == "https://configured.example.test" + VERIFY_PATH


@pytest.mark.parametrize("host", ["attacker.example", "kursplan.se", "127.0.0.1:9999"])
def test_development_link_ignores_request_host_and_forwarding_headers(
    origin_app, monkeypatch, host
):
    origin_app.config["DEV_MODE"] = True
    monkeypatch.setenv("app_port", "4753")
    with origin_app.test_request_context(
        "/signup", base_url=f"https://{host}",
        headers={"X-Forwarded-Host": "forwarded-attacker.example", "X-Forwarded-Proto": "https"},
    ):
        assert _verification_url(TOKEN) == "http://127.0.0.1:4753" + VERIFY_PATH


@pytest.mark.parametrize("base", ["http://localhost:4753", "http://127.0.0.1:4753", "http://[::1]:4753"])
@pytest.mark.parametrize(("dev_mode", "testing"), [(True, False), (False, True)])
def test_explicit_loopback_http_is_allowed_for_development_or_tests(
    origin_app, base, dev_mode, testing
):
    origin_app.config.update(DEV_MODE=dev_mode, TESTING=testing, PUBLIC_BASE_URL=base)
    assert _link(origin_app) == base + VERIFY_PATH


@pytest.mark.parametrize("base", ["http://localhost:4753", "http://127.0.0.1:4753", "http://[::1]:4753"])
def test_production_rejects_even_loopback_plain_http(origin_app, base):
    origin_app.config["PUBLIC_BASE_URL"] = base
    with pytest.raises(ValueError):
        _link(origin_app)


@pytest.mark.parametrize("base", ["http://remote.example", "http://127.0.0.1.attacker.example", "http://192.168.1.20:4753"])
@pytest.mark.parametrize(("dev_mode", "testing"), [(True, False), (False, True)])
def test_plain_http_to_remote_hosts_is_rejected_even_in_development_or_tests(
    origin_app, base, dev_mode, testing
):
    origin_app.config.update(DEV_MODE=dev_mode, TESTING=testing, PUBLIC_BASE_URL=base)
    with pytest.raises(ValueError):
        _link(origin_app)


@pytest.mark.parametrize("port", ["", "0", "-1", "65536", "not-a-port", "4753.0"])
def test_invalid_development_port_fails_closed(origin_app, monkeypatch, port):
    origin_app.config["DEV_MODE"] = True
    monkeypatch.setenv("app_port", port)
    with pytest.raises(ValueError):
        _link(origin_app)
