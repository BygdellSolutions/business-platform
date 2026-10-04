"""Opaque secrets: session tokens, CSRF tokens and setup tokens.

A token is 256 random bits (`secrets`), shown once and stored only as its SHA-256. A fast hash is right here
because the input already has far more entropy than any guessing attack can search; passwords, which do not,
use Argon2id (see `app.core.passwords`).
"""

import hashlib
import re
import secrets

TOKEN_BYTES = 32
# token_urlsafe(32) is exactly 43 URL-safe characters; anything else cannot be one of ours.
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def looks_like_token(value: str | None) -> bool:
    return value is not None and TOKEN_SHAPE.fullmatch(value) is not None
