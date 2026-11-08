"""Login for the owner's dashboard: one fixed username and password.

The password is never stored, only a scrypt hash of it (A2P_ADMIN_PASSWORD_HASH).
A successful login gets a signed session token in an HttpOnly cookie. The token
carries the username, an expiry time and a stamp of the current password hash, so
changing the password signs every session out. Nothing is kept on the server, which
suits serverless functions.

A successful login also gets a "known browser" token (OWASP's device cookie). It
grants no access by itself. It only means that browser has its own allowance of wrong
passwords, so someone spamming wrong passwords from elsewhere can't lock the owner out.
It is signed the same way, lasts 180 days, and also dies when the password changes.

Environment:
  A2P_ADMIN_USER            the username
  A2P_ADMIN_PASSWORD_HASH   from hash_password() (scripts/make_admin.py writes it)
  A2P_ADMIN_SESSION_SECRET  random secret that signs session tokens
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

SESSION_TTL = 8 * 3600
COOKIE = "a2p_admin"
DEVICE_TTL = 180 * 86400
DEVICE_COOKIE = "a2p_device"
_N, _R, _P, _LEN = 2**14, 8, 1, 32


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(want)))
        return hmac.compare_digest(got, _unb64(want))
    except (ValueError, TypeError):
        return False


def _config() -> tuple[str, str, bytes] | None:
    user = os.environ.get("A2P_ADMIN_USER", "")
    pw_hash = os.environ.get("A2P_ADMIN_PASSWORD_HASH", "")
    secret = os.environ.get("A2P_ADMIN_SESSION_SECRET", "")
    if not (user and pw_hash and len(secret) >= 32):
        return None
    return user, pw_hash, secret.encode()


def configured() -> bool:
    return _config() is not None


def _stamp(pw_hash: str) -> str:
    return hashlib.sha256(pw_hash.encode()).hexdigest()[:10]


def check_login(username: str, password: str) -> bool:
    cfg = _config()
    if not cfg:
        return False
    user, pw_hash, _ = cfg
    # Always run the slow hash, so a wrong username takes as long as a wrong password.
    ok_pw = verify_password(password, pw_hash)
    ok_user = hmac.compare_digest(username.encode(), user.encode())
    return ok_pw and ok_user


def make_session(now: float | None = None) -> str:
    cfg = _config()
    if not cfg:
        raise RuntimeError("admin login is not configured")
    user, pw_hash, secret = cfg
    exp = int((now or time.time()) + SESSION_TTL)
    payload = _b64(f"{user}|{exp}|{_stamp(pw_hash)}".encode())
    sig = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_session(token: str | None, now: float | None = None) -> str | None:
    """The username if the token is genuine, unexpired and from the current password."""
    cfg = _config()
    if not cfg or not token or token.count(".") != 1:
        return None
    user, pw_hash, secret = cfg
    payload, sig = token.split(".")
    want = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        return None
    try:
        who, exp, stamp = _unb64(payload).decode().split("|")
    except (ValueError, UnicodeDecodeError):
        return None
    if who != user or stamp != _stamp(pw_hash) or int(exp) < (now or time.time()):
        return None
    return who


def _sign(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())


def make_device(now: float | None = None) -> str:
    """A known-browser token: "device", a random id, the username, an expiry and the
    password stamp. The "device" label keeps it from ever passing as a session token."""
    cfg = _config()
    if not cfg:
        raise RuntimeError("admin login is not configured")
    user, pw_hash, secret = cfg
    exp = int((now or time.time()) + DEVICE_TTL)
    payload = _b64(f"device|{secrets.token_hex(8)}|{user}|{exp}|{_stamp(pw_hash)}".encode())
    return f"{payload}.{_sign(secret, payload)}"


def read_device(token: str | None, now: float | None = None) -> str | None:
    """The browser's id if the token is genuine, unexpired and from the current password."""
    cfg = _config()
    if not cfg or not token or token.count(".") != 1:
        return None
    user, pw_hash, secret = cfg
    payload, sig = token.split(".")
    if not hmac.compare_digest(_sign(secret, payload), sig):
        return None
    try:
        kind, device, who, exp, stamp = _unb64(payload).decode().split("|")
        expired = int(exp) < (now or time.time())
    except (ValueError, UnicodeDecodeError):
        return None
    if kind != "device" or who != user or stamp != _stamp(pw_hash) or expired:
        return None
    return device
