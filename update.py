"""Build, test and deploy origin/main to the existing Kursplan Compose project."""

import argparse
import json
import subprocess
import sys
import tarfile
import time
from contextlib import closing
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from uuid import uuid4


COMPOSE = ("docker", "compose", "--env-file", ".env", "-f", "docker-compose.yml")
MAIN_REF = "refs/remotes/origin/main"
PUBLIC_HEALTH_HOST = "kursplan.se"
ROLLBACK_IMAGE = "kursplan:rollback"
REVISION_LABEL = "org.opencontainers.image.revision"
DEV_MODE_VALUES = ("1", "true", "yes", "on")
DATABASE_PROBE = f"""\
import os

if os.getenv("DEV_MODE", "").strip().lower() not in {DEV_MODE_VALUES!r}:
    import psycopg

    with psycopg.connect(
        os.environ["db_path"], connect_timeout=5, options="-c statement_timeout=5000", autocommit=True
    ) as connection:
        connection.execute("SELECT 1")
"""


class DeploymentError(RuntimeError):
    pass


def run(*command, capture=False):
    result = subprocess.run(command, check=True, capture_output=capture, text=True)
    return result.stdout.strip() if capture else None


def verify_checkout():
    if not Path(".env").is_file() or not Path("docker-compose.yml").is_file():
        raise DeploymentError("Kör update.py i produktionsprojektet med dess lokala .env-fil.")
    if run("git", "branch", "--show-current", capture=True) != "main":
        raise DeploymentError("Produktionsprojektet måste stå på grenen main.")
    if run("git", "status", "--porcelain", "--untracked-files=no", capture=True):
        raise DeploymentError("Produktionsprojektet har lokala ändringar i Git-spårade filer.")


def fetch_revision():
    run("git", "fetch", "--no-tags", "origin", f"+refs/heads/main:{MAIN_REF}")
    return run("git", "rev-parse", "--verify", f"{MAIN_REF}^{{commit}}", capture=True)


def verify_source(revision):
    try:
        run("git", "merge-base", "--is-ancestor", "HEAD", revision)
    except subprocess.CalledProcessError as error:
        raise DeploymentError("Serverns main kan inte snabbspolas till origin/main.") from error
    release_manifest = run("git", "rev-parse", "--verify", f"{revision}:docker-compose.yml", capture=True)
    local_manifest = run("git", "hash-object", "--path=docker-compose.yml", "docker-compose.yml", capture=True)
    if local_manifest != release_manifest:
        raise DeploymentError("Serverns docker-compose.yml skiljer sig från origin/main. Granska infrastrukturändringen separat.")


def production_web_image(*, warn_dev_mode=True):
    config = json.loads(run(*COMPOSE, "config", "--format", "json", capture=True))
    web = config["services"]["web"]
    dev_mode = str(web.get("environment", {}).get("DEV_MODE") or "").strip().lower()
    if warn_dev_mode and dev_mode in DEV_MODE_VALUES:
        print(
            "Varning – DEV_MODE är aktiverat. Webbappen använder en tillfällig SQLite-databas som börjar om vid omstart.",
            file=sys.stderr,
        )
    image = web.get("image") or f"{config['name']}-web"
    if image == ROLLBACK_IMAGE:
        raise DeploymentError("Compose-tjänstens image måste ha ett annat namn än kursplan:rollback.")
    return image


def export_source(revision, destination):
    archive = destination.parent / "source.tar"
    run("git", "archive", "--format=tar", "--output", str(archive), revision)
    destination.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    if (destination / ".env").exists() or (destination / ".env").is_symlink():
        raise DeploymentError("Releasekoden innehåller en Git-spårad .env-fil.")
    archive.unlink()


def wait_for_test_container(name):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = run("docker", "inspect", "--format", "{{.State.Status}} {{.State.Health.Status}}", name, capture=True)
        if state == "running healthy":
            return
        if state.startswith(("exited ", "dead ")) or state.endswith(" unhealthy"):
            break
        time.sleep(2)
    run("docker", "logs", "--tail", "60", name)
    raise DeploymentError(f"Testcontainern {name} blev inte frisk.")


def build_and_test(source, revision):
    # Distinct names and no published ports or mounts keep the tests off the
    # production database, volume and tunnel, even on the same Docker engine.
    network = f"kursplan-update-{uuid4().hex[:12]}"
    db, web, runner, smoke = (f"{network}-{role}" for role in ("db", "web", "pytest", "smoke"))
    image = f"kursplan:{revision}"
    test_image = f"kursplan:{network}-tests"
    password = token_hex(24)
    dsn = f"postgresql://postgres:{password}@db:5432/postgres"
    env_file = source.parent / "test.env"
    env_file.write_text(
        f"POSTGRES_PASSWORD={password}\nTEST_DATABASE_URL={dsn}\ndb_path={dsn}\n"
        f"DEV_MODE=false\napp_port=8000\napp_secret_key={token_hex(24)}\n"
        f"test_email=release-admin@example.invalid\ntest_password={token_hex(24)}\nsend_mail=0\n",
        encoding="ascii",
    )

    print("Bygger webbimage och testmiljö.", flush=True)
    run("docker", "build", "--pull", "--label", f"{REVISION_LABEL}={revision}", "-t", image, "-f", str(source / "dockerfile"), str(source))
    image_id = run("docker", "image", "inspect", "--format", "{{.Id}}", image, capture=True)
    try:
        run("docker", "build", "--pull", "-t", test_image, "-f", str(source / "tests/dockerfile"), str(source))
        run("docker", "network", "create", "--internal", network)
        isolated = ("--network", network, "--env-file", str(env_file))
        health_flags = ("--health-interval", "2s", "--health-timeout", "5s", "--health-retries", "30")
        run(
            "docker", "run", "--detach", "--name", db, *isolated, "--network-alias", "db", *health_flags,
            "--health-cmd", "pg_isready -h 127.0.0.1 -U postgres -d postgres", "postgres:18",
        )
        wait_for_test_container(db)
        print("Kör pytest med tillfällig PostgreSQL, SQLite och Chromium.", flush=True)
        run("docker", "run", "--rm", "--init", "--shm-size", "1g", "--name", runner, *isolated, test_image)
        print("Provar den byggda webbimagen på det isolerade nätverket.", flush=True)
        run(
            "docker", "run", "--detach", "--name", web, *isolated, "--network-alias", "kursplan", *health_flags,
            "--health-cmd", "python -c 'from urllib.request import urlopen; urlopen(\"http://127.0.0.1:8000/health\", timeout=3)'",
            image_id,
        )
        wait_for_test_container(web)
        run("docker", "run", "--rm", "--name", smoke, *isolated, "--entrypoint", "python", image_id, "/app/tests/release_smoke.py")
        return image_id
    finally:
        for command in (
            ("docker", "rm", "-fv", smoke, runner, web, db),
            ("docker", "network", "rm", network),
            ("docker", "image", "rm", test_image),
        ):
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def running_web_image():
    containers = run(*COMPOSE, "ps", "--quiet", "web", capture=True).splitlines()
    if len(containers) != 1:
        raise DeploymentError("Exakt en körande Compose-container för web krävs för återställning.")
    return run("docker", "inspect", "--format", "{{.Image}}", containers[0], capture=True)


def image_revision(image):
    return run(
        "docker", "image", "inspect", "--format", f'{{{{with .Config.Labels}}}}{{{{index . "{REVISION_LABEL}"}}}}{{{{end}}}}',
        image, capture=True,
    )


def verify_public_health():
    for attempt in range(6):
        try:
            with closing(HTTPSConnection(PUBLIC_HEALTH_HOST, timeout=5)) as connection:
                connection.request("GET", f"/health?deployment={uuid4().hex}", headers={"Cache-Control": "no-cache"})
                with connection.getresponse() as response:
                    if response.status == 200 and json.load(response) == {"status": "ok"}:
                        return
        except (HTTPException, OSError, ValueError):
            pass
        if attempt < 5:
            time.sleep(3)
    raise DeploymentError("Den publika hälsokontrollen på kursplan.se misslyckades.")


def verify_database():
    try:
        run(*COMPOSE, "exec", "-T", "web", "python", "-c", DATABASE_PROBE, capture=True)
    except subprocess.CalledProcessError as error:
        raise DeploymentError("Webbcontainerns anslutning till PostgreSQL kunde inte verifieras.") from error


def start_web(image, compose_image):
    run("docker", "image", "tag", image, compose_image)
    run(*COMPOSE, "up", "--detach", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120", "web")
    if running_web_image() != image:
        raise DeploymentError("Webbcontainern kör en annan image än den valda.")
    verify_database()
    verify_public_health()


def deploy(tested_image, compose_image, previous_image):
    run("docker", "image", "tag", previous_image, ROLLBACK_IMAGE)
    try:
        start_web(tested_image, compose_image)
    except (DeploymentError, subprocess.CalledProcessError, OSError, KeyboardInterrupt) as error:
        print(f"Driftsättningen misslyckades ({error}). Återställer föregående webimage.", file=sys.stderr)
        try:
            start_web(previous_image, compose_image)
        except (DeploymentError, subprocess.CalledProcessError, OSError) as rollback_error:
            raise DeploymentError(f"Återställningen kunde inte verifieras ({rollback_error}).") from rollback_error
        raise DeploymentError("Föregående webimage återställdes och svarar på /health.") from error


def update():
    verify_checkout()
    revision = fetch_revision()
    verify_source(revision)
    compose_image = production_web_image()
    if compose_image == f"kursplan:{revision}":
        raise DeploymentError("Compose-tjänstens image måste ha ett eget namn för driftsättning.")
    previous_image = running_web_image()

    if image_revision(previous_image) == revision:
        verify_database()
        verify_public_health()
        print(f"{revision} körs redan och svarar på /health.")
        return

    print(f"Förbereder {revision} från origin/main.", flush=True)
    with TemporaryDirectory(prefix="kursplan-update-") as directory:
        source = Path(directory) / "source"
        export_source(revision, source)
        tested_image = build_and_test(source, revision)

    if fetch_revision() != revision:
        raise DeploymentError("origin/main ändrades under testningen. Kör update.py igen för den nya committen.")
    verify_checkout()
    verify_source(revision)
    if production_web_image(warn_dev_mode=False) != compose_image or running_web_image() != previous_image:
        raise DeploymentError("Produktionsstacken ändrades under testningen. Kör update.py igen.")
    run("git", "merge", "--ff-only", "--no-overwrite-ignore", revision)
    print("Tester godkända. Uppdaterar webbcontainern.", flush=True)
    deploy(tested_image, compose_image, previous_image)
    print(f"{revision} körs i produktion och svarar på /health.")


def main():
    parser = argparse.ArgumentParser(description="Bygg, testa och driftsätt Kursplan från origin/main.")
    parser.parse_args()
    if not sys.platform.startswith("linux") or not hasattr(tarfile, "data_filter"):
        sys.exit("Produktionsskriptet kräver Linux och en Python-version med tarfile.data_filter.")

    # Den här låsningen körs på produktionsservern (Linux), även vid manuella omkörningar.
    import fcntl

    try:
        lock_path = Path(run("git", "rev-parse", "--git-path", "kursplan-deploy.lock", capture=True))
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeploymentError("En driftsättning pågår redan på servern.") from error
            update()
    except (DeploymentError, subprocess.CalledProcessError, OSError, ValueError, tarfile.TarError) as error:
        sys.exit(f"Driftsättning avbruten. {error}")
    except KeyboardInterrupt:
        sys.exit("Driftsättning avbruten av användaren.")


if __name__ == "__main__":
    main()
