"""Encrypts broker credentials (API key/secret, TOTP seed) at rest using Fernet.

Never log or return decrypted values over the API except the minimal fields
needed for an active session (e.g. a freshly generated TOTP code).
"""
from cryptography.fernet import Fernet

from app.core.config import get_settings


class CredentialVault:
    def __init__(self, key: str | None = None):
        key = key or get_settings().encryption_key
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY is not set. Generate one with "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"` and put it in backend/.env"
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


_vault: CredentialVault | None = None


def get_vault() -> CredentialVault:
    global _vault
    if _vault is None:
        _vault = CredentialVault()
    return _vault
