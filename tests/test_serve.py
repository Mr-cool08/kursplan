import os
import sys
from runpy import run_path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def startup(monkeypatch):
    steps = Mock()
    app_module = ModuleType("app")
    app_module.app = Mock()
    app_module.dev_mode = False
    app_module.initialize_database = steps.initialize_database
    app_module.main = steps.run_development_server
    monkeypatch.setitem(sys.modules, "app", app_module)
    monkeypatch.setattr(os, "execvp", steps.execvp)
    monkeypatch.delenv("app_host", raising=False)
    monkeypatch.delenv("app_port", raising=False)
    return app_module, steps


@pytest.mark.parametrize(
    ("environment", "expected_bind"),
    [({}, "0.0.0.0:8000"), ({"app_host": "127.0.0.1", "app_port": "8080"}, "127.0.0.1:8080")],
)
def test_production_initializes_database_before_starting_gunicorn(
    project_root, startup, monkeypatch, environment, expected_bind
):
    _, steps = startup
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    run_path(str(project_root / "serve.py"), run_name="__main__")

    assert [step[0] for step in steps.method_calls] == ["initialize_database", "execvp"]
    command, args = steps.execvp.call_args.args
    assert command == args[0] == "gunicorn"
    assert args[args.index("--bind") + 1] == expected_bind
    assert args[-1] == "app:app"


def test_development_runs_the_existing_development_server(project_root, startup):
    app_module, steps = startup
    app_module.dev_mode = True

    run_path(str(project_root / "serve.py"), run_name="__main__")

    steps.run_development_server.assert_called_once_with()
    steps.initialize_database.assert_not_called()
    steps.execvp.assert_not_called()


def test_failed_database_initialization_prevents_server_start(project_root, startup):
    _, steps = startup
    steps.initialize_database.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        run_path(str(project_root / "serve.py"), run_name="__main__")

    steps.initialize_database.assert_called_once_with()
    steps.execvp.assert_not_called()
