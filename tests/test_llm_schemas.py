"""004 FR-006, FR-020: the shapes of the LLM profile routes (contracts/openapi.yaml)."""

from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from freyja_agent.api.schemas import (
    ErrorDetail,
    LlmLastTest,
    LlmProfile,
    LlmProfileCreate,
    LlmProfileDraft,
    LlmProfileList,
    LlmProfileUpdate,
    LlmTestResult,
    RunProfile,
)

KEY = "sk-recognizable-key-0001"
VALID: dict[str, Any] = {
    "name": "Local",
    "provider": "openai",
    "model": "served-model",
    "api_key": KEY,
}
# A request's fields as a request gives them: a key as text, and a field no shape has.
DRAFT: dict[str, Any] = {"provider": "openai", "model": "m", "api_key": KEY}
UNKNOWN: dict[str, Any] = {"extra": "x"}


def test_a_new_profile_needs_a_name_a_provider_a_model_and_a_key() -> None:
    created = LlmProfileCreate(**VALID)
    assert created.base_url is None
    assert created.responses_api is True
    assert created.api_key.get_secret_value() == KEY
    assert KEY not in repr(created)
    for field in VALID:
        with pytest.raises(ValidationError, match=field):
            LlmProfileCreate(**{k: v for k, v in VALID.items() if k != field})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "  "),
        ("name", "n" * 65),
        ("provider", "someprovider"),
        ("model", ""),
        ("model", "m" * 201),
        ("base_url", "ftp://127.0.0.1/v1"),
        ("base_url", "not a web address"),
        ("base_url", "http://host/" + "p" * 2000),
        ("api_key", ""),
        ("api_key", "k" * 4001),
        ("responses_api", "sometimes"),
    ],
)
def test_a_value_outside_the_rules_is_refused_without_being_repeated(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError, match=field) as refused:
        LlmProfileCreate(**{**VALID, field: value})
    for error in refused.value.errors(include_input=False):
        assert "input" not in error
    if isinstance(value, str) and len(value) > 8:
        assert value not in str(refused.value)


def test_text_is_trimmed_and_the_limits_are_inclusive() -> None:
    created = LlmProfileCreate(
        **{
            **VALID,
            "name": "  " + "n" * 64 + " ",
            "model": " " + "m" * 200,
            "base_url": " http://10.0.0.5:31000/v1 ",
            "api_key": "k" * 4000,
        }
    )
    assert created.name == "n" * 64
    assert created.model == "m" * 200
    assert created.base_url == "http://10.0.0.5:31000/v1"


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="extra"):
        LlmProfileCreate(**VALID, **UNKNOWN)
    with pytest.raises(ValidationError, match="extra"):
        LlmProfileDraft(**DRAFT, **UNKNOWN)
    with pytest.raises(ValidationError, match="extra"):
        LlmProfileUpdate(name="Renamed", **UNKNOWN)


def test_an_update_changes_only_what_it_sends_and_must_send_something() -> None:
    update = LlmProfileUpdate(model="another-model")
    assert update.model_dump(exclude_unset=True) == {"model": "another-model"}
    assert LlmProfileUpdate(base_url=None).model_dump(exclude_unset=True) == {"base_url": None}
    with pytest.raises(ValidationError, match="at least one field"):
        LlmProfileUpdate()


def test_a_draft_takes_its_key_from_the_request_or_from_a_saved_profile() -> None:
    assert LlmProfileDraft(**DRAFT).profile_id is None
    assert LlmProfileDraft(provider="openai", model="m", profile_id="a" * 32).api_key is None
    with pytest.raises(ValidationError, match="api_key"):
        LlmProfileDraft(provider="openai", model="m")


@pytest.mark.parametrize(
    "model", [LlmProfile, LlmProfileList, LlmLastTest, LlmTestResult, RunProfile]
)
def test_no_response_shape_has_a_field_for_a_key(model: type[BaseModel]) -> None:
    for name in model.model_fields:
        assert "key" not in name or name == "key_hint", name
        assert "secret" not in name or name == "secret_configured", name
        assert "token" not in name, name


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "code",
    [
        "profile_not_found",
        "profile_read_only",
        "profile_active",
        "profile_unusable",
        "profile_limit",
        "name_taken",
        "secret_not_configured",
        "test_busy",
        "other_origin",
    ],
)
def test_the_refusals_of_the_profile_routes_have_their_codes(code: str) -> None:
    assert ErrorDetail.model_validate({"code": code, "message": "x"}).code == code


def test_the_codes_are_the_ones_the_contract_lists() -> None:
    """The contract's list and the service's are one list, so neither gains a code alone."""
    contract = yaml.safe_load(
        (ROOT / "specs/004-web-llm-profiles/contracts/openapi.yaml").read_text()
    )
    listed = contract["components"]["schemas"]["ErrorDetail"]["properties"]["code"]["enum"]
    codes = set(get_args(ErrorDetail.model_fields["code"].annotation))
    assert set(listed) <= codes
    # What the service has beyond the contract's are the codes of the earlier features.
    assert codes - set(listed) == {
        "run_active",
        "decision_pending",
        "nothing_pending",
        "proposal_mismatch",
        "thread_not_found",
        "internal",
    }


def test_a_stream_error_code_is_not_an_http_refusal() -> None:
    with pytest.raises(ValidationError):
        ErrorDetail.model_validate({"code": "no_usable_model", "message": "x"})
