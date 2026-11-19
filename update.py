"""Deploy the CI-tested image to the existing Kursplan Compose project."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from contextlib import closing
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from uuid import uuid4


COMPOSE = ("docker", "compose", "--env-file", ".env", "-f", "docker-compose.yml")
PUBLIC_HEALTH_HOST = "kursplan.se"
ROLLBACK_IMAGE = "kursplan:rollback"
DATABASE_PROBE = """\
import os
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


def verify_archive(archive, checksum):
    fields = checksum.read_text(encoding="ascii").split()
    if len(fields) != 2 or fields[1] != archive.name:
        raise DeploymentError("Kontrollsummans filnamn stämmer inte med CI-arkivet.")

    digest = hashlib.sha256()
    with archive.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)

    if digest.hexdigest() != fields[0]:
        raise DeploymentError("CI-arkivets kontrollsumma stämmer inte.")


def verify_revision(revision):
    run("git", "fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
    if run("git", "rev-parse", "--verify", "refs/remotes/origin/main", capture=True) != revision:
        raise DeploymentError("CI-committen ligger inte längre överst på origin/main.")

    tested_manifest = run("git", "rev-parse", "--verify", f"{revision}:docker-compose.yml", capture=True)
    local_manifest = run("git", "hash-object", "--path=docker-compose.yml", "docker-compose.yml", capture=True)
    if local_manifest != tested_manifest:
        raise DeploymentError("Serverns docker-compose.yml skiljer sig från CI-committen.")


def running_web_image():
    containers = run(*COMPOSE, "ps", "--quiet", "web", capture=True).splitlines()
    if len(containers) != 1:
        raise DeploymentError("Exakt en körande Compose-container för web krävs för återställning.")
    return run("docker", "inspect", "--format", "{{.Image}}", containers[0], capture=True)


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


def deploy(revision, archive, checksum):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise DeploymentError("Ange en fullständig commit-SHA från CI.")
    if not Path(".env").is_file() or not Path("docker-compose.yml").is_file():
        raise DeploymentError("Kör driftsättningen i produktionsprojektet med dess lokala .env-fil.")

    verify_archive(archive, checksum)
    verify_revision(revision)
    config = json.loads(run(*COMPOSE, "config", "--format", "json", capture=True))
    web = config["services"]["web"]
    dev_mode = str(web.get("environment", {}).get("DEV_MODE") or "").strip().lower()
    if dev_mode in {"1", "true", "yes", "on"}:
        raise DeploymentError("DEV_MODE måste vara avstängt i produktionsprojektet.")
    compose_image = web.get("image") or f"{config['name']}-web"
    previous_image = running_web_image()
    release_image = f"kursplan:{revision}"
    if compose_image in (release_image, ROLLBACK_IMAGE):
        raise DeploymentError("Compose-tjänstens image måste ha ett eget namn för driftsättning.")

    run("docker", "image", "load", "--input", str(archive))
    image = json.loads(run("docker", "image", "inspect", release_image, capture=True))[0]
    labels = image["Config"].get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != revision:
        raise DeploymentError("Imagen saknar den förväntade commit-etiketten från CI.")
    platform = f"{image['Os']}/{image['Architecture']}"
    if platform != run("docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}", capture=True):
        raise DeploymentError(f"CI-imagens plattform {platform} matchar inte Docker-servern.")

    # Laddning kan ta tid. Kontrollera main igen innan den körande tjänsten ändras.
    verify_revision(revision)
    if previous_image == image["Id"]:
        verify_database()
        verify_public_health()
        print(f"{revision} körs redan och svarar på /health.")
        return

    run("docker", "image", "tag", previous_image, ROLLBACK_IMAGE)
    try:
        start_web(image["Id"], compose_image)
    except (DeploymentError, subprocess.CalledProcessError, OSError, KeyboardInterrupt) as error:
        print(f"Driftsättningen misslyckades ({error}). Återställer föregående webimage.", file=sys.stderr)
        try:
            start_web(previous_image, compose_image)
        except (DeploymentError, subprocess.CalledProcessError, OSError) as rollback_error:
            raise DeploymentError(f"Återställningen kunde inte verifieras ({rollback_error}).") from rollback_error
        raise DeploymentError("Föregående webimage återställdes och svarar på /health.") from error

    print(f"{revision} körs i produktion och svarar på /health.")


def main():
    parser = argparse.ArgumentParser(description="Driftsätt en godkänd CI-image i Kursplans Compose-projekt.")
    parser.add_argument("revision")
    parser.add_argument("archive", type=Path)
    parser.add_argument("checksum", type=Path)
    args = parser.parse_args()

    # Den här låsningen körs på produktionsservern (Linux), även vid manuella omkörningar.
    import fcntl

    try:
        lock_path = Path(run("git", "rev-parse", "--git-path", "kursplan-deploy.lock", capture=True))
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeploymentError("En driftsättning pågår redan på servern.") from error
            deploy(args.revision, args.archive, args.checksum)
    except (DeploymentError, subprocess.CalledProcessError, OSError, ValueError) as error:
        sys.exit(f"Driftsättning avbruten. {error}")


if __name__ == "__main__":
    main()
