import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, call
from uuid import UUID

import pytest

import update


REVISION = "a" * 40
NEW_IMAGE = f"sha256:{'b' * 64}"
OLD_IMAGE = f"sha256:{'c' * 64}"
WEB_IMAGE = "kursplan-web"
WEB_UP = (
    *update.COMPOSE,
    "up", "--detach", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120", "web",
)
WEB_PS = (*update.COMPOSE, "ps", "--quiet", "web")
WEB_INSPECT = ("docker", "inspect", "--format", "{{.Image}}", "web-container")
DATABASE_CHECK = (*update.COMPOSE, "exec", "-T", "web", "python", "-c", update.DATABASE_PROBE)
GIT_MERGE = ("git", "merge", "--ff-only", "--no-overwrite-ignore", REVISION)


@pytest.fixture
def pipeline(monkeypatch):
    steps = Mock()
    for name in (
        "verify_checkout", "fetch_revision", "verify_source", "production_web_image",
        "running_web_image", "export_source", "build_and_test", "run", "deploy",
        "image_revision", "verify_database", "verify_database_service",
    ):
        monkeypatch.setattr(update, name, getattr(steps, name))

    steps.image_revision.return_value = "e" * 40
    steps.fetch_revision.return_value = REVISION
    steps.production_web_image.return_value = WEB_IMAGE
    steps.running_web_image.return_value = OLD_IMAGE
    steps.build_and_test.return_value = NEW_IMAGE
    return steps


def test_updates_only_after_build_and_tests_pass(pipeline):
    update.update()

    revision, source = pipeline.export_source.call_args.args
    assert revision == REVISION
    pipeline.build_and_test.assert_called_once_with(source, REVISION)
    assert not Path(source).exists()
    assert pipeline.fetch_revision.call_count == 2
    assert pipeline.verify_checkout.call_count == 2
    assert pipeline.verify_database_service.call_count == 2
    assert pipeline.verify_source.call_args_list == [call(REVISION), call(REVISION)]
    pipeline.deploy.assert_called_once_with(NEW_IMAGE, WEB_IMAGE, OLD_IMAGE)

    calls = pipeline.mock_calls
    assert calls.index(call.export_source(REVISION, source)) < calls.index(call.build_and_test(source, REVISION))
    assert calls.index(call.build_and_test(source, REVISION)) < calls.index(call.run(*GIT_MERGE))
    database_checks = [index for index, step in enumerate(calls) if step == call.verify_database_service()]
    assert database_checks[0] < calls.index(call.build_and_test(source, REVISION))
    assert calls.index(call.build_and_test(source, REVISION)) < database_checks[1] < calls.index(call.run(*GIT_MERGE))
    assert calls.index(call.run(*GIT_MERGE)) < calls.index(call.deploy(NEW_IMAGE, WEB_IMAGE, OLD_IMAGE))


@pytest.mark.parametrize("failed_step", ["export_source", "build_and_test", "run"])
def test_failure_before_rollout_leaves_production_running(pipeline, failed_step):
    failure = subprocess.CalledProcessError(1, failed_step)
    getattr(pipeline, failed_step).side_effect = failure

    with pytest.raises(subprocess.CalledProcessError):
        update.update()

    pipeline.deploy.assert_not_called()
    if failed_step != "run":
        assert call.run(*GIT_MERGE) not in pipeline.mock_calls


def test_new_main_commit_during_tests_stops_rollout(pipeline):
    pipeline.fetch_revision.side_effect = [REVISION, "d" * 40]

    with pytest.raises(update.DeploymentError):
        update.update()

    pipeline.build_and_test.assert_called_once()
    assert call.run(*GIT_MERGE) not in pipeline.mock_calls
    pipeline.deploy.assert_not_called()


def test_changed_running_image_during_tests_stops_rollout(pipeline):
    pipeline.running_web_image.side_effect = [OLD_IMAGE, f"sha256:{'d' * 64}"]

    with pytest.raises(update.DeploymentError):
        update.update()

    pipeline.build_and_test.assert_called_once()
    assert call.run(*GIT_MERGE) not in pipeline.mock_calls
    pipeline.deploy.assert_not_called()


def test_checkout_change_during_tests_stops_rollout(pipeline):
    pipeline.verify_checkout.side_effect = [None, update.DeploymentError("Källkoden har lokala ändringar.")]

    with pytest.raises(update.DeploymentError):
        update.update()

    pipeline.build_and_test.assert_called_once()
    assert call.run(*GIT_MERGE) not in pipeline.mock_calls
    pipeline.deploy.assert_not_called()


def test_skips_rebuilding_running_revision(pipeline):
    pipeline.image_revision.return_value = REVISION

    update.update()

    pipeline.verify_database.assert_called_once_with()
    pipeline.export_source.assert_not_called()
    pipeline.build_and_test.assert_not_called()
    pipeline.run.assert_not_called()
    pipeline.deploy.assert_not_called()


@pytest.mark.parametrize("check", [1, 2], ids=["before tests", "before rollout"])
def test_unavailable_database_stops_update_before_changing_production(pipeline, check):
    error = update.DeploymentError("Databastjänsten db är inte igång och frisk.")
    pipeline.verify_database_service.side_effect = [None] * (check - 1) + [error]

    with pytest.raises(update.DeploymentError, match="Databastjänsten db"):
        update.update()

    if check == 1:
        pipeline.export_source.assert_not_called()
        pipeline.build_and_test.assert_not_called()
    else:
        pipeline.build_and_test.assert_called_once()
    assert call.run(*GIT_MERGE) not in pipeline.mock_calls
    pipeline.deploy.assert_not_called()


@pytest.mark.parametrize("containers", ["", "db-one\ndb-two"])
def test_database_service_requires_exactly_one_existing_container(monkeypatch, containers):
    commands = Mock(return_value=containers)
    monkeypatch.setattr(update, "run", commands)

    with pytest.raises(update.DeploymentError, match="Exakt en befintlig Compose-container för db"):
        update.verify_database_service()

    commands.assert_called_once_with(*update.COMPOSE, "ps", "--all", "--quiet", "db", capture=True)


@pytest.mark.parametrize(
    "state",
    [
        {"Status": "exited", "ExitCode": 0},
        {"Status": "restarting", "Health": {"Status": "unhealthy"}},
        {"Status": "running", "Health": {"Status": "starting"}},
        {"Status": "running", "Health": {"Status": "unhealthy"}},
        {"Status": "running"},
        {"Status": "running", "Paused": True, "Health": {"Status": "healthy"}},
    ],
    ids=["stopped", "restarting", "starting", "unhealthy", "no healthcheck", "paused"],
)
def test_database_service_rejects_unavailable_database_without_starting_it(monkeypatch, state):
    commands = Mock(side_effect=["db-container", json.dumps(state)])
    monkeypatch.setattr(update, "run", commands)

    with pytest.raises(update.DeploymentError, match="start --wait --wait-timeout 120 db"):
        update.verify_database_service()

    assert commands.call_args_list == [
        call(*update.COMPOSE, "ps", "--all", "--quiet", "db", capture=True),
        call("docker", "inspect", "--format", "{{json .State}}", "db-container", capture=True),
    ]


def test_database_service_accepts_running_healthy_database(monkeypatch):
    commands = Mock(side_effect=["db-container", json.dumps({
        "Status": "running", "Paused": False, "Health": {"Status": "healthy"},
    })])
    monkeypatch.setattr(update, "run", commands)

    update.verify_database_service()

    assert commands.call_count == 2


def test_dev_mode_warns_and_resolves_web_image(monkeypatch, capsys):
    config = {"name": "kursplan", "services": {"web": {"environment": {"DEV_MODE": " YES "}}}}
    monkeypatch.setattr(update, "run", Mock(return_value=json.dumps(config)))

    assert update.production_web_image() == WEB_IMAGE
    warning = capsys.readouterr().err
    assert "Varning" in warning and "DEV_MODE" in warning and "SQLite" in warning
    assert update.production_web_image(warn_dev_mode=False) == WEB_IMAGE
    assert capsys.readouterr().err == ""


def test_database_probe_skips_postgresql_in_dev_mode(monkeypatch):
    monkeypatch.setenv("DEV_MODE", " ON ")
    monkeypatch.delenv("db_path", raising=False)
    psycopg = Mock()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)

    exec(update.DATABASE_PROBE, {})

    psycopg.connect.assert_not_called()


def test_database_probe_checks_postgresql_in_production(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("db_path", "postgresql://db.example.invalid/postgres")
    psycopg = MagicMock()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)

    exec(update.DATABASE_PROBE, {})

    psycopg.connect.assert_called_once()
    psycopg.connect.return_value.__enter__.return_value.execute.assert_called_once_with("SELECT 1")


@pytest.mark.parametrize("tracked_env", [False, True])
def test_exports_tracked_revision_without_production_env(tmp_path, monkeypatch, tracked_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEV_MODE=false\n", encoding="ascii")
    source = tmp_path / "staged" / "source"
    source.parent.mkdir()
    archive = source.parent / "source.tar"

    def write_tracked_files(*args):
        with tarfile.open(archive, "w") as tar:
            files = {"app.py": b"print('tracked release')\n"}
            if tracked_env:
                files[".env"] = b"DEBUG=true\n"
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))

    git = Mock(side_effect=write_tracked_files)
    monkeypatch.setattr(update, "run", git)

    if tracked_env:
        with pytest.raises(update.DeploymentError, match="Git-spårad .env"):
            update.export_source(REVISION, source)
    else:
        update.export_source(REVISION, source)
        assert (source / "app.py").read_bytes() == b"print('tracked release')\n"
        assert not (source / ".env").exists()
        assert not archive.exists()
    git.assert_called_once_with("git", "archive", "--format=tar", "--output", str(archive), REVISION)


def test_failed_pytest_cleans_only_its_isolated_resources(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(update, "uuid4", lambda: UUID("12345678-abcd-4000-8000-000000000000"))
    network = "kursplan-update-12345678abcd"
    runner = f"{network}-pytest"
    test_image = f"kursplan:{network}-tests"

    def docker(*command, capture=False):
        if command[:3] == ("docker", "image", "inspect"):
            return NEW_IMAGE
        if command[:2] == ("docker", "run") and runner in command:
            raise subprocess.CalledProcessError(1, command)

    run = Mock(side_effect=docker)
    cleanup = Mock()
    monkeypatch.setattr(update, "run", run)
    monkeypatch.setattr(update.subprocess, "run", cleanup)
    monkeypatch.setattr(update, "wait_for_test_container", Mock())

    with pytest.raises(subprocess.CalledProcessError):
        update.build_and_test(source, REVISION)

    run.assert_any_call("docker", "network", "create", "--internal", network)
    test_runs = [args for args, _ in run.call_args_list if args[:2] == ("docker", "run")]
    assert len(test_runs) == 2
    assert test_runs[-1][-6:] == (test_image, "python", "-m", "pytest", "-n", "auto")
    for args in test_runs:
        assert args[args.index("--network") + 1] == network
        assert not {"-p", "--publish", "-v", "--volume", "--mount"}.intersection(args)
    env = (tmp_path / "test.env").read_text(encoding="ascii")
    assert "@db:5432/postgres" in env
    assert "send_mail=0\n" in env

    assert [args[0] for args, _ in cleanup.call_args_list] == [
        ("docker", "rm", "-fv", f"{network}-smoke", runner, f"{network}-web", f"{network}-db"),
        ("docker", "network", "rm", network),
        ("docker", "image", "rm", test_image),
    ]


@pytest.fixture
def deployment(monkeypatch):
    state = {"current": OLD_IMAGE, "web_tag": OLD_IMAGE}
    responses = {
        WEB_PS: lambda: "web-container",
        WEB_INSPECT: lambda: state["current"],
        ("docker", "image", "tag", OLD_IMAGE, update.ROLLBACK_IMAGE): lambda: None,
        ("docker", "image", "tag", NEW_IMAGE, WEB_IMAGE): lambda: state.update(web_tag=NEW_IMAGE),
        ("docker", "image", "tag", OLD_IMAGE, WEB_IMAGE): lambda: state.update(web_tag=OLD_IMAGE),
        WEB_UP: lambda: state.update(current=state["web_tag"]),
        DATABASE_CHECK: lambda: None,
    }
    commands = Mock(side_effect=lambda *command, **kwargs: responses[command]())
    monkeypatch.setattr(update, "run", commands)
    return state, responses, commands


def test_deploys_only_web_from_tested_image(deployment):
    state, _, commands = deployment

    update.deploy(NEW_IMAGE, WEB_IMAGE, OLD_IMAGE)

    assert state["current"] == NEW_IMAGE
    assert commands.call_args_list == [
        call("docker", "image", "tag", OLD_IMAGE, update.ROLLBACK_IMAGE),
        call("docker", "image", "tag", NEW_IMAGE, WEB_IMAGE),
        call(*WEB_UP),
        call(*WEB_PS, capture=True),
        call(*WEB_INSPECT, capture=True),
        call(*DATABASE_CHECK, capture=True),
    ]


@pytest.mark.parametrize("operation", [WEB_UP, DATABASE_CHECK], ids=["container health", "database"])
def test_failed_release_restores_previous_image(deployment, operation):
    state, responses, commands = deployment
    run = responses[operation]

    def fail_for_candidate():
        result = run()
        if state["current"] == NEW_IMAGE:
            raise subprocess.CalledProcessError(1, operation)
        return result

    responses[operation] = fail_for_candidate

    with pytest.raises(update.DeploymentError, match="Föregående webbimage återställdes"):
        update.deploy(NEW_IMAGE, WEB_IMAGE, OLD_IMAGE)

    assert state["current"] == OLD_IMAGE
    assert commands.call_args_list.count(call(*WEB_UP)) == 2
    assert call("docker", "image", "tag", OLD_IMAGE, WEB_IMAGE) in commands.call_args_list
    assert commands.call_args_list[-1] == call(*DATABASE_CHECK, capture=True)
