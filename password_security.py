"""Lösenord hashade med Argon2id och bakåtkompatibel verifiering."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY
from werkzeug.security import check_password_hash


_hasher = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)
_ARGON2_PREFIXES = ("$argon2id$", "$argon2i$", "$argon2d$")


def hash_password(password):
    """Använd bibliotekets slumpmässiga salt och RFC 9106:s 64 MiB-profil."""
    return _hasher.hash(password)


def is_password_hash(value):
    """Recognize stored formats without treating a plain prefix as a hash."""
    return value.startswith(_ARGON2_PREFIXES) or (
        value.startswith(("scrypt:", "pbkdf2:")) and value.count("$") == 2
    )


def verify_password(stored, supplied):
    """Return (valid, needs_upgrade), failing closed for malformed hashes."""
    if not isinstance(supplied, str) or not isinstance(stored, str):
        return False, False
    if stored.startswith(_ARGON2_PREFIXES):
        try:
            _hasher.verify(stored, supplied)
            return True, _hasher.check_needs_rehash(stored)
        except (InvalidHashError, VerificationError):
            return False, False
    if is_password_hash(stored):
        try:
            return check_password_hash(stored, supplied), True
        except (ValueError, TypeError, OverflowError):
            return False, False
    # Only legacy records can reach this branch: all current writes hash first.
    return secrets.compare_digest(stored.encode("utf-8"), supplied.encode("utf-8")), True
