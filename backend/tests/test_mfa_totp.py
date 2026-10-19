"""TOTP and recovery codes as pure functions: the RFC test vectors, the time window, replay, and the key derivation."""

from datetime import datetime, timezone

import pytest
from pydantic import SecretStr

from app.core import mfa
from app.core.config import settings

RFC_SECRET = b"12345678901234567890"  # the shared secret of RFC 4226 and RFC 6238 (SHA-1)


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def test_hotp_matches_the_rfc_4226_vectors():
    assert [mfa.hotp(RFC_SECRET, counter) for counter in range(10)] == [
        "755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489",
    ]


@pytest.mark.parametrize(
    "seconds, expected",  # RFC 6238 appendix B, SHA-1, shown as the last six of its eight digits
    [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")],
)
def test_the_code_for_a_moment_matches_the_rfc_6238_vectors(seconds, expected):
    assert mfa.hotp(RFC_SECRET, mfa.step_of(at(seconds))) == expected


def test_a_code_is_accepted_one_step_either_way_but_not_two():
    now = at(1_000_000_000)
    step = mfa.step_of(now)
    for offset, ok in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
        code = mfa.hotp(RFC_SECRET, step + offset)
        assert (mfa.verify(RFC_SECRET, code, now, None) == step + offset) is ok


def test_a_code_cannot_be_used_twice_and_never_goes_back_in_time():
    now = at(1_000_000_000)
    step = mfa.step_of(now)
    code = mfa.hotp(RFC_SECRET, step)

    assert mfa.verify(RFC_SECRET, code, now, step - 1) == step
    assert mfa.verify(RFC_SECRET, code, now, step) is None  # the same step again
    assert mfa.verify(RFC_SECRET, mfa.hotp(RFC_SECRET, step - 1), now, step) is None  # an older code after a newer one


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34", "١٢٣٤٥٦", "123456\n7"])
def test_anything_but_six_ascii_digits_is_refused(bad):
    assert mfa.verify(RFC_SECRET, bad, at(1_000_000_000), None) is None


def test_spaces_inside_a_code_are_ignored_as_authenticator_apps_show_it():
    now = at(1_000_000_000)
    code = mfa.hotp(RFC_SECRET, mfa.step_of(now))

    assert mfa.verify(RFC_SECRET, f"{code[:3]} {code[3:]}", now, None) == mfa.step_of(now)


def test_the_key_is_derived_from_the_seed_and_the_server_key_only(monkeypatch):
    monkeypatch.setattr(settings, "security_key", SecretStr("k" * 40))
    seed, other = mfa.new_seed(), mfa.new_seed()

    first = mfa.derive_secret(seed)
    assert first == mfa.derive_secret(seed) and len(first) == mfa.SECRET_BYTES
    assert first != mfa.derive_secret(other)
    assert seed not in mfa.secret_base32(first)
    monkeypatch.setattr(settings, "security_key", SecretStr("j" * 40))
    assert mfa.derive_secret(seed) != first  # a different server key gives a different key: a database copy alone is useless


def test_the_provisioning_link_has_what_an_authenticator_app_reads():
    uri = mfa.provisioning_uri("ada+test@example.com", RFC_SECRET)

    assert uri.startswith("otpauth://totp/Business%20Platform%3Aada%2Btest%40example.com?")
    assert "secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ" in uri  # base32 of the RFC secret, no padding
    assert "issuer=Business%20Platform" in uri and "algorithm=SHA1" in uri and "digits=6" in uri and "period=30" in uri
    assert "=" not in mfa.secret_base32(RFC_SECRET)


def test_recovery_codes_have_64_random_bits_and_are_normalised_before_hashing():
    codes = {mfa.new_recovery_code() for _ in range(200)}
    assert len(codes) == 200  # no collisions in a small sample
    code = next(iter(codes))
    assert len(code) == 17 and code[8] == "-"

    assert mfa.normalize_recovery_code(code.upper()) == mfa.normalize_recovery_code(f" {code} ") == code.replace("-", "")
    for bad in ("", "short", "z" * 16, code + "0", "1234-5678"):
        assert mfa.normalize_recovery_code(bad) is None
    assert mfa.recovery_hash(code.replace("-", "")) != code.replace("-", "") and len(mfa.recovery_hash("a" * 16)) == 64
