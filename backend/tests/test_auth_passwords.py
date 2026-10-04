"""Argon2id passwords, the equal-work (dummy) path, and bounded admission to hashing."""

import threading
import time

import pytest

from app.core import passwords
from app.core.passwords import AuthBusy, HashAdmission

pytestmark = pytest.mark.usefixtures("session_mode")


def test_hashes_are_argon2id_with_the_configured_parameters():
    stored = passwords.hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    assert "m=1024,t=1,p=1" in stored
    assert passwords.hash_password("correct horse battery staple") != stored  # random salt


def test_verify_accepts_the_right_password_and_nothing_else():
    stored = passwords.hash_password("correct horse battery staple")
    assert passwords.verify(stored, "correct horse battery staple") is True
    for wrong in ("", "correct horse battery stapl", "Correct horse battery staple", "x" * 200):
        assert passwords.verify(stored, wrong) is False


def test_a_corrupt_stored_hash_is_a_failed_verification_not_an_error():
    for broken in ("", "not a hash", "$argon2id$garbage", "$2b$12$abcdefghijklmnopqrstuv"):
        assert passwords.verify(broken, "whatever password here") is False


def test_without_a_stored_hash_verification_still_does_exactly_one_argon2_verification(monkeypatch):
    calls = []
    real = passwords._argon_verify
    monkeypatch.setattr(passwords, "_argon_verify", lambda stored, password: calls.append(stored) or real(stored, password))

    assert passwords.verify(None, "anything at all") is False
    assert len(calls) == 1 and calls[0].startswith("$argon2id$")  # a dummy hash, made with the current parameters

    calls.clear()
    stored = passwords.hash_password("correct horse battery staple")
    assert passwords.verify(stored, "wrong wrong wrong wrong") is False
    assert len(calls) == 1


def test_the_dummy_hash_follows_the_current_parameters(monkeypatch):
    first = passwords._dummy_hash()
    monkeypatch.setattr(passwords.settings, "argon2_time_cost", 2)
    second = passwords._dummy_hash()
    assert "t=1" in first and "t=2" in second


def test_rehash_is_needed_exactly_when_parameters_are_outdated(monkeypatch):
    stored = passwords.hash_password("correct horse battery staple")
    assert passwords.needs_rehash(stored) is False
    monkeypatch.setattr(passwords.settings, "argon2_memory_kib", 2048)
    assert passwords.needs_rehash(stored) is True


@pytest.mark.parametrize(
    "password, ok",
    [
        ("a" * 11, False),
        ("a" * 12, True),
        ("a" * 128, True),
        ("a" * 129, False),
        ("password with spaces only ok", True),  # no composition rules
        ("alllowercasebutlongenough", True),
        ("12345678901234567890", True),
        ("ünïcödé pässwörd ✓ 日本語パスワード", True),
        ("has\x00nul in the middle ok?", False),
    ],
)
def test_the_policy_is_length_only(password, ok):
    assert (passwords.policy_problem(password) is None) is ok


def test_a_password_equal_to_the_email_is_refused():
    assert passwords.policy_problem("Someone@Example.com", "someone@example.com") is not None
    assert passwords.policy_problem("a different long password", "someone@example.com") is None


# --- bounded admission -------------------------------------------------------------------------------------------------------------------------


def test_admission_never_runs_more_than_the_limit_at_once():
    admission = HashAdmission(concurrent=2, waiting=10, wait_seconds=5)
    gate, running, peak = threading.Event(), [0], [0]
    lock = threading.Lock()

    def work():
        with admission.slot():
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            gate.wait(5)
            with lock:
                running[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    assert running[0] == 2  # the others are waiting, not running
    gate.set()
    for t in threads:
        t.join(10)
    assert peak[0] == 2 and admission.peak_running == 2


def test_requests_beyond_running_plus_waiting_are_refused_at_once_without_waiting():
    admission = HashAdmission(concurrent=1, waiting=2, wait_seconds=30)
    gate = threading.Event()
    started = threading.Barrier(4)

    def hold():
        with admission.slot():
            started.wait(5)
            gate.wait(10)

    def wait_for_turn():
        with admission.slot():
            pass

    runner = threading.Thread(target=hold)
    runner.start()
    waiters = [threading.Thread(target=wait_for_turn) for _ in range(2)]
    time.sleep(0.2)  # the runner holds the only slot
    for w in waiters:
        w.start()
    time.sleep(0.3)  # 1 running + 2 waiting = the limit
    began = time.monotonic()
    with pytest.raises(AuthBusy):
        with admission.slot():
            pytest.fail("a fourth request must not be admitted")
    assert time.monotonic() - began < 0.5  # refused immediately, not after the 30 s wait
    started.abort()
    gate.set()
    runner.join(10)
    for w in waiters:
        w.join(10)


def test_a_waiter_gives_up_after_the_wait_limit_and_leaves_no_trace():
    admission = HashAdmission(concurrent=1, waiting=5, wait_seconds=0.2)
    gate = threading.Event()
    entered = threading.Event()

    def hold():
        with admission.slot():
            entered.set()
            gate.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    entered.wait(2)
    began = time.monotonic()
    with pytest.raises(AuthBusy):
        with admission.slot():
            pass
    assert 0.15 < time.monotonic() - began < 2
    gate.set()
    holder.join(5)
    with admission.slot():  # nothing leaked: capacity is fully available again
        pass
    assert admission._in_flight == 0


def test_a_slot_is_released_when_the_work_raises():
    admission = HashAdmission(concurrent=1, waiting=0, wait_seconds=0.5)
    for _ in range(3):
        with pytest.raises(ValueError):
            with admission.slot():
                raise ValueError("boom")
    with admission.slot():
        pass
    assert admission._in_flight == 0


def test_with_no_waiting_allowed_a_busy_slot_refuses_immediately():
    admission = HashAdmission(concurrent=1, waiting=0, wait_seconds=30)
    with admission.slot():
        began = time.monotonic()
        with pytest.raises(AuthBusy):
            with admission.slot():
                pass
        assert time.monotonic() - began < 0.5
