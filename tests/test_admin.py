"""The owner's dashboard login: password hashing and signed sessions. Offline."""

from __future__ import annotations

import pytest

from alert2playbook import admin

PASSWORD = "correct horse battery staple 42"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("A2P_ADMIN_USER", "abubakar")
    monkeypatch.setenv("A2P_ADMIN_PASSWORD_HASH", admin.hash_password(PASSWORD))
    monkeypatch.setenv("A2P_ADMIN_SESSION_SECRET", "s" * 64)


def test_password_hash_round_trip():
    stored = admin.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert admin.verify_password(PASSWORD, stored)
    assert not admin.verify_password(PASSWORD + "x", stored)
    assert not admin.verify_password(PASSWORD, "garbage")
    assert admin.hash_password(PASSWORD) != stored  # a new salt every time


def test_login_needs_both_username_and_password(configured):
    assert admin.check_login("abubakar", PASSWORD)
    assert not admin.check_login("abubakar", "wrong")
    assert not admin.check_login("admin", PASSWORD)


def test_nothing_works_when_not_configured():
    assert not admin.configured()
    assert not admin.check_login("abubakar", PASSWORD)
    assert admin.read_session("anything.here") is None


def test_session_round_trip_and_tampering(configured):
    token = admin.make_session()
    assert admin.read_session(token) == "abubakar"
    payload, sig = token.split(".")
    assert admin.read_session(f"{payload}.{sig[:-2]}AA") is None
    assert admin.read_session(f"{payload}x.{sig}") is None
    assert admin.read_session("") is None and admin.read_session(None) is None


def test_session_expires(configured):
    token = admin.make_session(now=1_000_000)
    assert admin.read_session(token, now=1_000_000 + admin.SESSION_TTL - 1) == "abubakar"
    assert admin.read_session(token, now=1_000_000 + admin.SESSION_TTL + 1) is None


def test_changing_the_password_signs_everyone_out(configured, monkeypatch):
    token = admin.make_session()
    monkeypatch.setenv("A2P_ADMIN_PASSWORD_HASH", admin.hash_password("a brand new password"))
    assert admin.read_session(token) is None


def test_upstash_is_found_under_any_prefix(monkeypatch):
    from alert2playbook.store import Store, upstash_settings

    assert upstash_settings() == ("", "") and Store().backend == "memory"
    monkeypatch.setenv("STORAGE_REST_API_URL", "https://example.upstash.io")
    monkeypatch.setenv("STORAGE_REST_API_READ_ONLY_TOKEN", "read-only")
    assert upstash_settings() == ("", "")  # a URL without a write token isn't enough
    monkeypatch.setenv("STORAGE_REST_API_TOKEN", "write-token")
    assert upstash_settings() == ("https://example.upstash.io", "write-token")
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://own.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "own-token")
    assert upstash_settings() == ("https://own.upstash.io", "own-token")


def test_a_short_session_secret_is_refused(monkeypatch):
    monkeypatch.setenv("A2P_ADMIN_USER", "abubakar")
    monkeypatch.setenv("A2P_ADMIN_PASSWORD_HASH", admin.hash_password(PASSWORD))
    monkeypatch.setenv("A2P_ADMIN_SESSION_SECRET", "short")
    assert not admin.configured()
