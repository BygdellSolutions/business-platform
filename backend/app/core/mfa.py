"""TOTP (RFC 6238) and recovery codes, from the standard library only.

The key an authenticator app holds is NOT stored. What is stored per person is a random `seed` (meaningless alone); the key is
`HMAC-SHA256(server key, "totp-secret-v1|" + seed)` cut to 20 bytes, so a copy of the database cannot produce a code. The server
key is `SECURITY_KEY`: rotating it makes every enrolled key wrong, so every person would have to be reset by the operator
(`admin reset-mfa`) and re-enroll; change it only deliberately.

Parameters are the ones every authenticator app assumes (SHA-1, 6 digits, 30-second steps), with one step of clock drift allowed
either way. A code is accepted at most once: the newest accepted time step is stored and an older or equal step is refused.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote

from app.core.config import settings

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
SECRET_BYTES = 20
RECOVERY_CODE_COUNT = 10


def new_seed() -> str:
    return secrets.token_hex(16)


def derive_secret(seed: str) -> bytes:
    key = settings.security_key.get_secret_value().encode() if settings.security_key else b""
    return hmac.new(key, b"totp-secret-v1|" + bytes.fromhex(seed), hashlib.sha256).digest()[:SECRET_BYTES]


def hotp(secret: bytes, counter: int) -> str:
    """RFC 4226 with the dynamic truncation, as 6 digits."""
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return str(number).zfill(DIGITS)


def step_of(now: datetime) -> int:
    return int(now.timestamp()) // STEP_SECONDS


def verify(secret: bytes, code: str, now: datetime, last_step: int | None) -> int | None:
    """The time step the code belongs to, or None. A step not newer than `last_step` is refused (no code works twice)."""
    cleaned = "".join(code.split())
    if len(cleaned) != DIGITS or not (cleaned.isascii() and cleaned.isdigit()):
        return None
    current = step_of(now)
    matched: int | None = None
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):  # every step is compared, whatever matches (constant work)
        if hmac.compare_digest(hotp(secret, step), cleaned) and (last_step is None or step > last_step):
            matched = step
    return matched


def secret_base32(secret: bytes) -> str:
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def provisioning_uri(email: str, secret: bytes) -> str:
    """The otpauth:// link an authenticator app reads (from a QR code, or typed by hand as the base32 key)."""
    issuer = settings.mfa_issuer
    label = quote(f"{issuer}:{email}", safe="")
    return f"otpauth://totp/{label}?secret={secret_base32(secret)}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


# --- recovery codes ---------------------------------------------------------------------------------------------------------------------


def new_recovery_code() -> str:
    """64 random bits, shown once as xxxxxxxx-xxxxxxxx."""
    raw = secrets.token_hex(8)
    return f"{raw[:8]}-{raw[8:]}"


def normalize_recovery_code(code: str) -> str | None:
    cleaned = code.strip().lower().replace("-", "").replace(" ", "")
    return cleaned if len(cleaned) == 16 and all(c in "0123456789abcdef" for c in cleaned) else None


def recovery_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()
