"""Session tokens and password hashing (A-NFR-01).

Token = base64url(json{sub, exp}) + "." + base64url(HMAC-SHA256). Stateless, short-lived,
sent as an HttpOnly SameSite=Lax cookie (browser) or a Bearer header (API clients/tests).
The identity comes only from a token the server signed — never from a client-chosen header.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import get_settings

COOKIE_NAME = "dh_session"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    key = get_settings().session_secret.encode()
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def issue_token(user_id: str, *, ttl_seconds: int | None = None, now: float | None = None) -> str:
    ttl = ttl_seconds if ttl_seconds is not None else get_settings().session_ttl_minutes * 60
    issued = now if now is not None else time.time()
    body = _b64(json.dumps({"sub": user_id, "exp": int(issued + ttl)}).encode())
    return f"{body}.{_sign(body)}"


def read_token(token: str, *, now: float | None = None) -> str | None:
    """Returns the user id, or None when the token is malformed, forged or expired."""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < (now if now is not None else time.time()):
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


# ------------------------------------------------------------------ passwords (scrypt)

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_b64(got), dk)
