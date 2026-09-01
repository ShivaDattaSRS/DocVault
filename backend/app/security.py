from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

ALGORITHM = "HS256"


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    # bcrypt silently truncates past 72 bytes, so cap the input explicitly.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- OTP codes
def generate_otp(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp(code: str) -> str:
    return hashlib.sha256(f"{settings.secret_key}:{code}".encode()).hexdigest()


def verify_otp(code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code), code_hash)


# ---------------------------------------------------------------- JWT access
def create_access_token(subject: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


# ------------------------------------------------- short-lived download URLs
def create_download_token(file_id: str, user_id: str | None, expires_in: int = 300) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": file_id,
        "uid": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "typ": "download",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_download_token(token: str) -> dict | None:
    data = decode_token(token)
    if not data or data.get("typ") != "download":
        return None
    return data


def new_share_token() -> str:
    return secrets.token_urlsafe(24)
