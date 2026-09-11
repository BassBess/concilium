"""Security helpers: credential encryption, password hashing, signed tokens.

Credentials entered in the UI are encrypted at rest with Fernet (AES-128-CBC +
HMAC). The key comes from CONCILIUM_SECRET_KEY, or a generated key persisted to
data/secret.key with 0600 permissions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_ENC_PREFIX = "enc::"
settings = get_settings()


def _load_or_create_key() -> bytes:
    if settings.secret_key:
        # Derive a urlsafe 32-byte key from the configured secret
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        return base64.urlsafe_b64encode(digest)
    key_file: Path = settings.data_path / "secret.key"
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


_FERNET = Fernet(_load_or_create_key())


def encrypt(value: str) -> str:
    if not value:
        return value
    return _ENC_PREFIX + _FERNET.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    try:
        return _FERNET.decrypt(value[len(_ENC_PREFIX) :].encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - corrupt/rotated key
        raise RuntimeError("Stored credential could not be decrypted (key changed?)") from exc


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_ENC_PREFIX)


# --- Password hashing (PBKDF2, stdlib only) --------------------------------

_PBKDF_ROUNDS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF_ROUNDS)
    return f"pbkdf2${_PBKDF_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# --- Stateless signed session tokens ---------------------------------------

def _signing_secret() -> bytes:
    if settings.secret_key:
        return settings.secret_key.encode()
    return _load_or_create_key()


def sign_token(payload: dict, ttl_seconds: int = 7 * 24 * 3600) -> str:
    body = {"exp": int(time.time()) + ttl_seconds, **payload}
    raw = base64.urlsafe_b64encode(json.dumps(body).encode())
    sig = hmac.new(_signing_secret(), raw, hashlib.sha256).digest()
    return raw.decode() + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def verify_token(token: str) -> dict | None:
    try:
        raw_b64, sig_b64 = token.split(".")
        raw = raw_b64.encode()
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        expected = hmac.new(_signing_secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        body = json.loads(base64.urlsafe_b64decode(raw))
        if body.get("exp", 0) < time.time():
            return None
        return body
    except Exception:
        return None


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "•" * (len(value) - 8) + value[-4:]
