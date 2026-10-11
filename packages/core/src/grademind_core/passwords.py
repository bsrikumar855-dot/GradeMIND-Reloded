"""Password rules and temporary passwords (5.0; D30.3). Hashing stays in `security` (argon2id)."""

from __future__ import annotations

import secrets

MIN_LENGTH = 12
MAX_LENGTH = 200
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no look-alike characters (a person types it once)


def check_new_password(new: str, current: str | None, email: str) -> str | None:
    """A plain-words reason the new password is refused, or None if it is acceptable."""
    if len(new) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(new) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    if current is not None and new == current:
        return "The new password must be different from the current one."
    local = email.split("@", 1)[0].lower()
    if new.lower() in (email.lower(), local) or (len(local) >= 6 and local in new.lower()):
        return "The password must not contain your email address."
    if len(set(new)) < 5:
        return "Use a password with more different characters."
    return None


def temporary_password(length: int = 16) -> str:
    """A random one-time password for an admin reset: about 90 bits, from a safe alphabet."""
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))
