"""Fernet encryption for registered agents' credentials (FR-AGT-007, FR-AUTH-005, Architecture §18.5).

Only ciphertext is stored in `Agent.encrypted_credentials`; plaintext exists only
in memory, just before an adapter call.
"""

from cryptography.fernet import Fernet

from app.core.config import get_settings

__all__ = ["encrypt", "decrypt"]


def _fernet() -> Fernet:
    key = get_settings().fernet_key.get_secret_value()
    if not key:
        # Fail closed: never store credentials unencrypted.
        raise RuntimeError("FERNET_KEY is not set; set it in .env before registering agents with credentials.")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
