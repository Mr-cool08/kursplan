import hashlib
import io
import json
import subprocess
from unittest.mock import Mock, call

import pytest

import update


REVISION = "a" * 40
NEW_IMAGE = f"sha256:{'b' * 64}"
OLD_IMAGE = f"sha256:{'c' * 64}"
RELEASE_IMAGE = f"kursplan:{REVISION}"
WEB_IMAGE = "kursplan-web"
WEB_UP = (
    *update.COMPOSE,
    "up", "--detach", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120", "web",
)
GIT_FETCH = ("git", "fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
GIT_HEAD = ("git", "rev-parse", "--verify", "refs/remotes/origin/main")
GIT_TESTED_MANIFEST = ("git", "rev-parse", "--verify", f"{REVISION}:docker-compose.yml")
GIT_LOCAL_MANIFEST = ("git", "hash-object", "--path=docker-compose.yml", "docker-compose.yml")
COMPOSE_CONFIG = (*update.COMPOSE, "config", "--format", "json")
WEB_INSPECT = ("docker", "inspect", "--format", "{{.Image}}", "web-container")
DOCKER_PLATFORM = ("docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}")
DATABASE_CHECK = (*update.COMPOSE, "exec", "-T", "web", "python", "-c", update.DATABASE_PROBE)


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").touch()
    (tmp_path / "docker-compose.yml").touch()
    archive = tmp_path / "kursplan-image.tar.gz"
    archive.write_bytes(b"test-image")
    checksum = tmp_path / "kursplan-image.tar.gz.sha256"
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="ascii")
    return archive, checksum


@pytest.fixture
def deployment(monkeypatch, artifact):
    archive, _ = artifact
    state = {"current": OLD_IMAGE, "web_tag": OLD_IMAGE}
    metadata = {
        "Id": NEW_IMAGE,
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {"Labels": {"org.opencontainers.image.revision": REVISION}},
    }
    responses = {
        GIT_FETCH: lambda: None,
        GIT_HEAD: lambda: REVISION,
        GIT_TESTED_MANIFEST: lambda: "same-manifest-id",
        GIT_LOCAL_MANIFEST: lambda: "same-manifest-id",
        COMPOSE_CONFIG: lambda: json.dumps(
            {"name": "kursplan", "services": {"web": {"build": "."}, "db": {"image": "postgres:18"}}}
        ),
        (*update.COMPOSE, "ps", "--quiet", "web"): lambda: "web-container",
        WEB_INSPECT: lambda: state["current"],
        ("docker", "image", "load", "--input", str(archive)): lambda: None,
        ("docker", "image", "inspect", RELEASE_IMAGE): lambda: json.dumps([metadata]),
        DOCKER_PLATFORM: lambda: "linux/amd64",
        ("docker", "image", "tag", OLD_IMAGE, update.ROLLBACK_IMAGE): lambda: None,
        ("docker", "image", "tag", NEW_IMAGE, WEB_IMAGE): lambda: state.update(web_tag=NEW_IMAGE),
        ("docker", "image", "tag", OLD_IMAGE, WEB_IMAGE): lambda: state.update(web_tag=OLD_IMAGE),
        WEB_UP: lambda: state.update(current=state["web_tag"]),
        DATABASE_CHECK: lambda: None,
    }

    def run(*command, capture=False):
        return responses[command]()

    docker_and_git = Mock(side_effect=run)
    health = Mock()
    monkeypatch.setattr(update, "run", docker_and_git)
    monkeypatch.setattr(update, "verify_public_health", health)
    return state, metadata, responses, docker_and_git, health


def test_deploys_only_web_from_tested_image(artifact, deployment):
    state, _, _, commands, health = deployment

    update.deploy(REVISION, *artifact)

    assert state["current"] == NEW_IMAGE
    assert commands.call_args_list.count(call(*GIT_FETCH)) == 2
    commands.assert_has_calls(
        [
            call("docker", "image", "tag", OLD_IMAGE, update.ROLLBACK_IMAGE),
            call("docker", "image", "tag", NEW_IMAGE, WEB_IMAGE),
            call(*WEB_UP),
        ]
    )
    commands.assert_any_call(*DATABASE_CHECK, capture=True)
    health.assert_called_once_with()


def test_rejects_corrupt_archive_before_git_or_docker(artifact, deployment):
    *_, commands, health = deployment
    artifact[0].write_bytes(b"incorrect-image")

    with pytest.raises(update.DeploymentError, match="kontrollsumma stämmer inte"):
        update.deploy(REVISION, *artifact)

    commands.assert_not_called()
    health.assert_not_called()


def test_stale_revision_during_image_load_leaves_web_running(artifact, deployment):
    state, _, responses, commands, health = deployment
    heads = iter((REVISION, "d" * 40))
    responses[GIT_HEAD] = lambda: next(heads)

    with pytest.raises(update.DeploymentError, match="origin/main"):
        update.deploy(REVISION, *artifact)

    assert state["current"] == OLD_IMAGE
    assert call(*WEB_UP) not in commands.call_args_list
    health.assert_not_called()


def test_compose_manifest_drift_stops_deployment(artifact, deployment):
    state, _, responses, commands, health = deployment
    responses[GIT_LOCAL_MANIFEST] = lambda: "different-manifest-id"

    with pytest.raises(update.DeploymentError, match="skiljer sig från CI-committen"):
        update.deploy(REVISION, *artifact)

    assert state["current"] == OLD_IMAGE
    assert call(*COMPOSE_CONFIG, capture=True) not in commands.call_args_list
    health.assert_not_called()


def test_rejects_development_mode_before_loading_image(artifact, deployment):
    state, _, responses, commands, health = deployment
    responses[COMPOSE_CONFIG] = lambda: json.dumps({"services": {"web": {"environment": {"DEV_MODE": "yes"}}}})

    with pytest.raises(update.DeploymentError, match="DEV_MODE måste vara avstängt"):
        update.deploy(REVISION, *artifact)

    assert state["current"] == OLD_IMAGE
    assert call("docker", "image", "load", "--input", str(artifact[0])) not in commands.call_args_list
    health.assert_not_called()


@pytest.mark.parametrize("mismatch", ["commit label", "platform"])
def test_wrong_image_metadata_leaves_web_running(artifact, deployment, mismatch):
    state, metadata, responses, commands, health = deployment
    if mismatch == "commit label":
        metadata["Config"]["Labels"] = None
    else:
        responses[DOCKER_PLATFORM] = lambda: "linux/arm64"

    with pytest.raises(update.DeploymentError, match="commit-etiketten|matchar inte Docker-servern"):
        update.deploy(REVISION, *artifact)

    assert state["current"] == OLD_IMAGE
    assert call(*WEB_UP) not in commands.call_args_list
    health.assert_not_called()


@pytest.mark.parametrize("failure", ["container health", "database", "public health"])
def test_failed_release_restores_previous_image(artifact, deployment, failure):
    state, _, responses, commands, health = deployment
    if failure == "container health":
        up = responses[WEB_UP]

        def up_fails_once():
            up()
            if state["current"] == NEW_IMAGE:
                raise subprocess.CalledProcessError(1, WEB_UP)

        responses[WEB_UP] = up_fails_once
    elif failure == "database":
        def database_fails_on_new_image():
            if state["current"] == NEW_IMAGE:
                raise subprocess.CalledProcessError(1, DATABASE_CHECK)

        responses[DATABASE_CHECK] = database_fails_on_new_image
    else:
        health.side_effect = [update.DeploymentError("HTTPS misslyckades"), None]

    with pytest.raises(update.DeploymentError, match="Föregående webimage återställdes"):
        update.deploy(REVISION, *artifact)

    assert state["current"] == OLD_IMAGE
    assert commands.call_args_list.count(call(*WEB_UP)) == 2
    assert call("docker", "image", "tag", OLD_IMAGE, WEB_IMAGE) in commands.call_args_list
    assert health.call_count == (2 if failure == "public health" else 1)


def test_repeating_same_release_keeps_previous_rollback_tag(artifact, deployment):
    state, _, _, commands, health = deployment
    state.update(current=NEW_IMAGE, web_tag=NEW_IMAGE)

    update.deploy(REVISION, *artifact)

    assert call("docker", "image", "tag", OLD_IMAGE, update.ROLLBACK_IMAGE) not in commands.call_args_list
    assert call(*WEB_UP) not in commands.call_args_list
    health.assert_called_once_with()


def test_public_probe_retries_without_following_redirects_or_reusing_cache(monkeypatch):
    redirect = io.BytesIO(b'{"status":"ok"}')
    html = io.BytesIO(b"<html>Gateway error</html>")
    healthy = io.BytesIO(b'{"status":"ok"}')
    redirect.status = 302
    html.status = healthy.status = 200
    connection = Mock()
    connection.getresponse.side_effect = [redirect, html, healthy]
    https = Mock(return_value=connection)
    sleep = Mock()
    monkeypatch.setattr(update, "HTTPSConnection", https)
    monkeypatch.setattr(update.time, "sleep", sleep)

    update.verify_public_health()

    assert https.call_args_list == [call(update.PUBLIC_HEALTH_HOST, timeout=5)] * 3
    requested_paths = []
    for args, kwargs in connection.request.call_args_list:
        method, path = args
        assert method == "GET"
        assert path.startswith("/health?deployment=")
        assert kwargs["headers"] == {"Cache-Control": "no-cache"}
        requested_paths.append(path)
    assert len(set(requested_paths)) == 3
    assert sleep.call_args_list == [call(3), call(3)]
