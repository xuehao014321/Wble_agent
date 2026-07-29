import base64
import logging
import sys


SECRET_PREFIX = "dpapi:"
DPAPI_ENTROPY = b"UTAR_WBLE_Agent:secrets:v1"


class SecretDecryptionError(RuntimeError):
    """Raised when an existing protected value cannot be decrypted."""


def protect_secret(value):
    """Encrypt a secret for the current Windows user using DPAPI."""
    value = str(value or "")
    if not value or value.startswith(SECRET_PREFIX):
        return value
    if sys.platform != "win32":
        logging.warning("DPAPI is unavailable outside Windows; secret is not encrypted.")
        return value

    try:
        import win32crypt

        encrypted = win32crypt.CryptProtectData(
            value.encode("utf-8"),
            "UTAR WBLE Agent secret",
            DPAPI_ENTROPY,
            None,
            None,
            0,
        )
        return SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception as error:
        raise RuntimeError(
            "Windows DPAPI encryption failed; refusing to save a plaintext secret."
        ) from error


def unprotect_secret(value):
    """Decrypt a DPAPI value. Existing plaintext values remain migratable."""
    value = str(value or "")
    if not value.startswith(SECRET_PREFIX):
        return value
    if sys.platform != "win32":
        logging.error("Cannot decrypt a Windows DPAPI secret on this platform.")
        return ""

    try:
        import win32crypt

        encrypted = base64.b64decode(value[len(SECRET_PREFIX):])
        _, decrypted = win32crypt.CryptUnprotectData(
            encrypted,
            DPAPI_ENTROPY,
            None,
            None,
            0,
        )
        return decrypted.decode("utf-8")
    except Exception:
        logging.exception("Failed to decrypt a saved WBLE Agent secret.")
        raise SecretDecryptionError(
            "The saved secret belongs to a different Windows user or context."
        )
