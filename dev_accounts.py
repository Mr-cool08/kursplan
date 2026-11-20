"""Lokala inloggningsuppgifter för konton som skapas i utvecklingsläget."""

import json
import logging
import os
import secrets


logger = logging.getLogger(__name__)


def load_dev_accounts(path):
    """Återanvänd lokala lösenord; miljövariabler har företräde."""
    logger.debug("Läser lokala utvecklingskonton.")
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        logger.error("Utvecklingskonton kunde inte läsas; feltyp=%s.", type(exc).__name__)
        raise
    if not isinstance(saved, dict):
        logger.error("Utvecklingskonton kunde inte läsas; ogiltig filstruktur.")
        raise ValueError("Filen med utvecklingskonton måste innehålla ett JSON-objekt.")

    accounts = {}
    for role, email_setting, password_setting, default_email in (
        ("admin", "test_email", "test_password", "admin@example.invalid"),
        ("user", "dev_user_email", "dev_user_password", "user@example.invalid"),
    ):
        previous = saved.get(role, {})
        if not isinstance(previous, dict) or any(
            not isinstance(value, str) or not value.strip()
            for value in previous.values()
        ):
            logger.error("Utvecklingskonton kunde inte läsas; ogiltiga kontouppgifter.")
            raise ValueError("Filen med utvecklingskonton innehåller ogiltiga uppgifter.")
        accounts[role] = {
            "email": (
                (os.getenv(email_setting) or "").strip()
                or (previous.get("email") or default_email).strip()
            ),
            "password": (
                os.getenv(password_setting)
                or previous.get("password")
                or secrets.token_urlsafe(24)
            ),
        }

    if accounts["admin"]["email"].casefold() == accounts["user"]["email"].casefold():
        logger.warning("Utvecklingskonton avvisades; samma e-postadress har angetts för båda rollerna.")
        raise ValueError("Utvecklingskontona måste ha olika e-postadresser.")

    if accounts != saved:
        # Lösenorden behövs för lokal inloggning och ska inte hamna i startloggen.
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as credentials_file:
                json.dump(accounts, credentials_file, ensure_ascii=False, indent=2)
                credentials_file.write("\n")
        except OSError as exc:
            logger.error("Utvecklingskonton kunde inte sparas; feltyp=%s.", type(exc).__name__)
            raise
        logger.info("Lokala utvecklingskonton sparades.")
    else:
        logger.debug("Befintliga lokala utvecklingskonton återanvänds.")
    return accounts
