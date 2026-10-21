"""Fernet encryption for per-org payment credentials at rest.

Key comes from the PAYMENT_CREDENTIALS_KEY env var (44-char base64,
``Fernet.generate_key()`` format). Fail closed: without a valid key,
encrypt/decrypt raise instead of storing plaintext.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _fernet():
    key = (os.getenv("PAYMENT_CREDENTIALS_KEY", "") or "").strip()
    if not key:
        raise ValueError("PAYMENT_CREDENTIALS_KEY is not configured")
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise ValueError("cryptography package is required") from exc
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise ValueError("PAYMENT_CREDENTIALS_KEY is not a valid Fernet key") from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential. Raises on failure (never returns plaintext)."""
    if not plaintext:
        raise ValueError("nothing to encrypt")
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a credential. Raises on failure (never returns garbage)."""
    if not ciphertext:
        raise ValueError("nothing to decrypt")
    plain = _fernet().decrypt(ciphertext.encode("utf-8"))
    return plain.decode("utf-8")


def mask_secret(value: str, visible: int = 4) -> str:
    """Last-N-chars display form for dashboards (never full secrets)."""
    if not value:
        return ""
    tail = value[-visible:] if len(value) >= visible else value
    return f"…{tail}"
