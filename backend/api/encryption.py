"""Symmetric encryption for per-user BYO LLM keys (DESIGN.md §5.6: "Keys
are per-user, encrypted at rest, never logged"). Same "secrets only" env-var
pattern backend/api/security.py's `_jwt_secret` already uses."""
from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet


def _encryption_key() -> bytes:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set — see .env.example")
    return key.encode()


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_encryption_key())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
