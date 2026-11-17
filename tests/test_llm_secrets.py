"""004 FR-020 to FR-022: protecting a stored key and keeping it out of text (research R4, R9)."""

import base64

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from freyja_agent.llm.secrets import (
    REDACTED,
    KeyUnreadableError,
    SecretNotConfiguredError,
    decrypt,
    encrypt,
    key_hint,
    redact,
)

KEY = "sk-recognizable-key-0001"


def a_secret() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


def test_a_key_comes_back_unchanged_under_the_same_secret() -> None:
    secret = a_secret()
    assert decrypt(encrypt(KEY, secret), secret) == KEY


def test_the_stored_token_does_not_contain_the_key() -> None:
    token = encrypt(KEY, a_secret())
    assert KEY not in token
    assert KEY not in base64.urlsafe_b64decode(token).decode("latin-1")


def test_the_same_key_is_stored_differently_each_time() -> None:
    secret = a_secret()
    assert encrypt(KEY, secret) != encrypt(KEY, secret)


def test_a_key_cannot_be_read_under_another_secret() -> None:
    token = encrypt(KEY, a_secret())
    with pytest.raises(KeyUnreadableError) as refused:
        decrypt(token, a_secret())
    assert KEY not in str(refused.value)


def test_something_that_is_not_a_token_cannot_be_read() -> None:
    with pytest.raises(KeyUnreadableError):
        decrypt("not-a-token", a_secret())


def test_nothing_is_stored_or_read_without_a_secret() -> None:
    with pytest.raises(SecretNotConfiguredError):
        encrypt(KEY, None)
    with pytest.raises(SecretNotConfiguredError):
        decrypt(encrypt(KEY, a_secret()), None)


@pytest.mark.parametrize(
    ("key", "hint"),
    [
        (KEY, "0001"),
        ("12345678", "5678"),
        ("1234567", ""),
        ("k", ""),
        ("", ""),
    ],
)
def test_the_hint_is_the_last_four_characters_of_a_key_of_eight_or_more(
    key: str, hint: str
) -> None:
    assert key_hint(key) == hint


def test_every_occurrence_of_a_key_is_removed_from_text() -> None:
    text = f"401: Incorrect API key provided: {KEY}. Check {KEY}!"
    cleaned = redact(text, [KEY])
    assert KEY not in cleaned
    assert cleaned == f"401: Incorrect API key provided: {REDACTED}. Check {REDACTED}!"


def test_several_keys_are_removed_and_an_empty_list_changes_nothing() -> None:
    other = "another-key-0002"
    assert redact(f"{KEY} and {other}", [KEY, other]) == f"{REDACTED} and {REDACTED}"
    assert redact("nothing to hide", []) == "nothing to hide"


def test_a_key_inside_a_longer_key_does_not_leave_a_piece_behind() -> None:
    longer = KEY + "-suffix"
    assert redact(f"got {longer}", [KEY, longer]) == f"got {REDACTED}"


def test_a_short_key_and_an_encoded_key_are_left_alone() -> None:
    # A key under eight characters could be any common word; removing it would mangle text.
    assert redact("the key is 1234567", ["1234567"]) == "the key is 1234567"
    encoded = base64.b64encode(KEY.encode()).decode()
    assert redact(f"Basic {encoded}", [KEY]) == f"Basic {encoded}"
