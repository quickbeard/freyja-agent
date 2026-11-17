"""Protecting stored API keys, and keeping them out of text (feature 004, research R4, R9).

A key an operator enters is stored as a Fernet token under the secret the service was given
in ``FREYJA_SECRET_KEY``. Fernet authenticates what it decrypts, so a token read under another
secret is refused instead of yielding a wrong key (FR-022). The key's last four characters are
kept beside the token in clear, so a profile can be shown without the secret (FR-020).

``redact`` is the one function that takes a key out of text. It is applied wherever text that
an endpoint or a client produced is shown, logged, or kept (FR-021).
"""

from collections.abc import Iterable

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

# A key shorter than this shows no character of itself and is not searched for in text: it
# could be any short word, and removing it would mangle what it appears in (FR-020, FR-021).
MIN_KEY_LENGTH = 8
HINT_LENGTH = 4
# Stands in for a key in any text that may be shown, logged, or kept.
REDACTED = "<key>"


class SecretNotConfiguredError(RuntimeError):
    """No secret was given to the service, so no key can be stored or read (FR-022)."""

    def __init__(self) -> None:
        super().__init__(
            "No secret protects stored API keys: FREYJA_SECRET_KEY is not set, so no key can "
            "be stored or read."
        )


class KeyUnreadableError(RuntimeError):
    """A stored key cannot be read with the secret the service was given (FR-022)."""

    def __init__(self) -> None:
        super().__init__(
            "The stored API key cannot be read with the current FREYJA_SECRET_KEY; enter the "
            "key again."
        )


def _fernet(secret: SecretStr | None) -> Fernet:
    if secret is None:
        raise SecretNotConfiguredError
    return Fernet(secret.get_secret_value())


def encrypt(key: str, secret: SecretStr | None) -> str:
    """The key as a token that only ``secret`` reads."""
    return _fernet(secret).encrypt(key.encode()).decode()


def decrypt(token: str, secret: SecretStr | None) -> str:
    """The key a token holds. Raises without repeating anything of the token or the key."""
    fernet = _fernet(secret)
    try:
        return fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise KeyUnreadableError from None


def key_hint(key: str) -> str:
    """The last four characters of a key of eight or more; nothing of a shorter one."""
    return key[-HINT_LENGTH:] if len(key) >= MIN_KEY_LENGTH else ""


def redact(text: str, secrets: Iterable[str]) -> str:
    """``text`` with the exact text of each key of eight characters or more replaced.

    The longest key goes first, so a key that contains another leaves no piece behind. An
    encoded or altered form of a key is not searched for (FR-021).
    """
    for secret in sorted({s for s in secrets if len(s) >= MIN_KEY_LENGTH}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text
