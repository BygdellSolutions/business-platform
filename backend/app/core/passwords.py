"""Password hashing: Argon2id through argon2-cffi (no custom cryptography), behind bounded admission.

Hashing is deliberately expensive, so it is also the cheapest thing to attack. Two rules follow:

  * `admission` bounds the work: at most `argon2_max_concurrent` hashes run at once, at most
    `argon2_max_waiting` more requests may wait (for at most `argon2_wait_seconds`), and any further request
    is refused AT ONCE with `AuthBusy`. There is no unbounded queue, so memory and threads cannot build up.
  * `verify` always performs exactly one Argon2 verification, against a dummy hash when there is no stored one,
    so an unknown account, an account without a credential and a wrong password cost the same.

Callers take ONE admission slot for a whole operation (`with admission.slot():`) and then call `verify` and
`hash_password` inside it; the functions here never take a slot themselves.
"""

import threading
from contextlib import contextmanager

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings


class AuthBusy(Exception):
    """Password hashing has no capacity right now. The caller should answer 503 and ask to retry."""


class HashAdmission:
    """Bounded admission to password hashing (running + waiting <= concurrent + waiting limit)."""

    def __init__(self, concurrent: int, waiting: int, wait_seconds: float):
        self.configure(concurrent, waiting, wait_seconds)

    def configure(self, concurrent: int, waiting: int, wait_seconds: float) -> None:
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(concurrent)
        self._limit = concurrent + waiting
        self._wait = wait_seconds
        self._in_flight = 0  # running plus waiting
        self.peak_running = 0
        self._running = 0

    @contextmanager
    def slot(self):
        with self._lock:
            if self._in_flight >= self._limit:
                raise AuthBusy()
            self._in_flight += 1
        acquired = False
        try:
            acquired = self._semaphore.acquire(timeout=self._wait)
            if not acquired:
                raise AuthBusy()
            with self._lock:
                self._running += 1
                self.peak_running = max(self.peak_running, self._running)
            try:
                yield
            finally:
                with self._lock:
                    self._running -= 1
                self._semaphore.release()
        finally:
            with self._lock:
                self._in_flight -= 1


admission = HashAdmission(settings.argon2_max_concurrent, settings.argon2_max_waiting, settings.argon2_wait_seconds)


def configure_admission_from_settings() -> None:
    admission.configure(settings.argon2_max_concurrent, settings.argon2_max_waiting, settings.argon2_wait_seconds)


def hasher() -> PasswordHasher:
    """A hasher with the CURRENT parameters (so a changed setting applies to new hashes at once)."""
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
    )


def hash_password(password: str) -> str:
    return hasher().hash(password)


def needs_rehash(stored: str) -> bool:
    """True when the stored hash was made with weaker (or different) parameters than the current settings."""
    return hasher().check_needs_rehash(stored)


_dummy: dict[tuple[int, int, int], str] = {}


def _dummy_hash() -> str:
    key = (settings.argon2_time_cost, settings.argon2_memory_kib, settings.argon2_parallelism)
    if key not in _dummy:
        _dummy[key] = hash_password("dummy password for equal work when no credential exists")
    return _dummy[key]


def _argon_verify(stored: str, password: str) -> bool:
    try:
        return hasher().verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify(stored: str | None, password: str) -> bool:
    """Check `password` against `stored` with exactly one Argon2 verification.

    With no stored hash the verification runs against a dummy hash and the answer is False.
    """
    if stored is None:
        _argon_verify(_dummy_hash(), password)
        return False
    return _argon_verify(stored, password)


def policy_problem(password: str, email: str | None = None) -> str | None:
    """Why a NEW password is not acceptable (length only, no composition rules), or None."""
    if len(password) < settings.password_min_length:
        return f"The password must be at least {settings.password_min_length} characters."
    if len(password) > settings.password_max_length:
        return f"The password must be at most {settings.password_max_length} characters."
    if "\x00" in password:
        return "The password contains an invalid character."
    if email is not None and password.strip().lower() == email.strip().lower():
        return "The password must not be the same as the email address."
    return None
