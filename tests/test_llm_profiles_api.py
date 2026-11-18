"""004: the LLM profile routes (contracts/openapi.yaml).

The apps are assembled the production way with an in-memory profile store and a model factory
that returns scripted models, so no model endpoint exists to be contacted (research R18).
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from freyja_agent.api.llm import NoInputRoute
from freyja_agent.api.schemas import LlmProfileCreate
from freyja_agent.llm.secrets import decrypt, encrypt
from freyja_agent.llm.store import PROFILE_LIMIT, LlmProfileRecord
from tests.conftest import LlmApp, McpApp
from tests.fakes import ScriptedChatModel

KEY = "sk-recognizable-key-0001"
ENV_KEY = "sk-environment-key-9f2c"
PROVIDER_VARIABLES = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
)


@pytest.fixture(autouse=True)
def environment_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment names an OpenAI-compatible model with a key and an address."""
    for name in PROVIDER_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    monkeypatch.setenv("OPENAI_API_BASE", "http://10.0.0.5:31000/v1")


def save(made: LlmApp, name: str, model: str = "saved-model", **values: Any) -> LlmProfileRecord:
    """Save a profile straight into the store, with its key stored as the service stores it."""
    given: dict[str, Any] = {
        "provider": "openai",
        "base_url": "http://127.0.0.1:9997/v1",
        "api_key_encrypted": encrypt(KEY, made.settings.secret_key),
        "key_hint": KEY[-4:],
        "responses_api": False,
    }
    return asyncio.run(made.store.create_profile(name=name, model=model, **{**given, **values}))


def listing(client: TestClient) -> dict[str, Any]:
    response = client.get("/llm/profiles")
    assert response.status_code == 200, response.text
    return response.json()


# Part 1 (T024): the list.


def test_with_nothing_saved_the_list_is_the_environments_profile_and_it_is_active(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model", openai_responses_api=False)
    with TestClient(made.app) as client:
        response = client.get("/llm/profiles")
        body = response.json()

    assert response.status_code == 200
    assert body == {
        "profiles": [
            {
                "id": "environment",
                "name": "Environment",
                "source": "environment",
                "provider": "openai",
                "model": "env-model",
                "base_url": "http://10.0.0.5:31000/v1",
                "responses_api": False,
                "key_hint": "9f2c",
                "active": True,
                "usable": True,
                "unusable_reason": None,
                "last_test": None,
                "created_at": None,
                "updated_at": None,
            }
        ],
        "active_id": "environment",
        "usable_model": True,
        "secret_configured": True,
        "limit": 50,
    }
    # Nothing of the environment's key beyond its last four characters.
    assert ENV_KEY not in response.text
    assert ENV_KEY[:-4] not in response.text


def test_listing_asks_no_model_anything(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    save(made, "Stub")
    with TestClient(made.app) as client:
        built = len(made.models.built)
        for _ in range(3):
            listing(client)
    assert all(model.calls == 0 for model in made.models.models.values())
    # Reading the list builds nothing either: the environment is described once, at startup.
    assert len(made.models.built) == built


def test_saved_profiles_follow_the_environments_by_name(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    beta = save(made, "beta", "model-b")
    alpha = save(made, "Alpha", "model-a", provider="anthropic", base_url=None)
    with TestClient(made.app) as client:
        response = client.get("/llm/profiles")
        body = response.json()

    assert [p["name"] for p in body["profiles"]] == ["Environment", "Alpha", "beta"]
    assert [p["active"] for p in body["profiles"]] == [True, False, False]
    saved = body["profiles"][2]
    assert saved == {
        "id": beta.id,
        "name": "beta",
        "source": "saved",
        "provider": "openai",
        "model": "model-b",
        "base_url": "http://127.0.0.1:9997/v1",
        "responses_api": False,
        "key_hint": "0001",
        "active": False,
        "usable": True,
        "unusable_reason": None,
        "last_test": None,
        "created_at": saved["created_at"],
        "updated_at": saved["updated_at"],
    }
    assert saved["created_at"] is not None
    # A provider with no choice of API reports none.
    assert body["profiles"][1]["id"] == alpha.id
    assert body["profiles"][1]["responses_api"] is None
    assert body["profiles"][1]["base_url"] is None
    # No key, no token, and nothing of a key beyond its last four characters.
    assert KEY not in response.text
    assert beta.api_key_encrypted not in response.text
    assert "api_key" not in response.text


def test_the_active_profile_is_marked_and_named(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    saved = save(made, "Stub")
    asyncio.run(made.store.activate(saved.id))
    with TestClient(made.app) as client:
        body = listing(client)
    assert body["active_id"] == saved.id
    assert [(p["name"], p["active"]) for p in body["profiles"]] == [
        ("Environment", False),
        ("Stub", True),
    ]
    assert body["usable_model"] is True


def test_a_profiles_last_test_is_shown_with_its_step_and_reason(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    saved = save(made, "Stub")
    asyncio.run(
        made.store.record_test(saved.id, 1, ok=False, step="conversation", reason="refused")
    )
    with TestClient(made.app) as client:
        last = listing(client)["profiles"][1]["last_test"]
    assert last == {"ok": False, "at": last["at"], "step": "conversation", "reason": "refused"}
    assert last["at"] is not None


def test_without_a_secret_saved_profiles_are_listed_as_not_usable(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    with_secret = make_llm_app(model="openai:env-model")
    save(with_secret, "Stub")
    made = make_llm_app(model="openai:env-model", store=with_secret.store, secret=None)
    with TestClient(made.app) as client:
        body = listing(client)
    assert body["secret_configured"] is False
    environment, saved = body["profiles"]
    assert (environment["usable"], environment["unusable_reason"]) == (True, None)
    assert (saved["usable"], saved["unusable_reason"]) == (False, "secret_not_configured")
    assert saved["key_hint"] == "0001"
    assert body["usable_model"] is True


def test_a_key_stored_under_another_secret_is_listed_as_not_readable(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    other_secret = SecretStr(Fernet.generate_key().decode())
    save(made, "Stub", api_key_encrypted=encrypt(KEY, other_secret))
    with TestClient(made.app) as client:
        saved = listing(client)["profiles"][1]
    assert (saved["usable"], saved["unusable_reason"]) == (False, "key_unreadable")


def test_an_environment_with_no_key_is_listed_as_not_configured(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        body = listing(client)
    (environment,) = body["profiles"]
    assert (environment["usable"], environment["unusable_reason"]) == (False, "not_configured")
    assert environment["key_hint"] == ""
    assert body["usable_model"] is False


def test_a_model_handed_to_the_runtime_is_listed_as_the_environments(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        body = listing(client)
    (environment,) = body["profiles"]
    # It is described by the model setting, and usable because it was handed over built.
    assert (environment["provider"], environment["model"]) == ("anthropic", "claude-sonnet-5-5")
    assert environment["usable"] is True
    assert body["active_id"] == "environment"


def test_a_refused_value_is_never_repeated_by_a_profile_route() -> None:
    """Research R9: FastAPI's own 422 carries the refused input, which here may be a key."""
    router = APIRouter(route_class=NoInputRoute)

    @router.post("/profiles")
    async def create(body: LlmProfileCreate) -> dict[str, str]:
        return {"name": body.name}

    app = FastAPI()
    app.include_router(router)
    too_long = KEY + "k" * 4000
    with TestClient(app) as client:
        response = client.post(
            "/profiles",
            json={"name": "Local", "provider": "openai", "model": "m", "api_key": too_long},
        )
        missing = client.post("/profiles", json={"name": "Local", "api_key": KEY})
        accepted = client.post(
            "/profiles", json={"name": "Local", "provider": "openai", "model": "m", "api_key": KEY}
        )

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "api_key"]]
    assert KEY not in response.text
    assert all(set(error) == {"type", "loc", "msg"} for error in response.json()["detail"])
    assert missing.status_code == 422
    assert KEY not in missing.text
    assert sorted(e["loc"][-1] for e in missing.json()["detail"]) == ["model", "provider"]
    assert accepted.status_code == 200


# Part 2 (T038): adding a profile and activating one.

NEW: dict[str, Any] = {
    "name": "Local vLLM",
    "provider": "openai",
    "model": "served-model",
    "base_url": "http://10.0.0.5:31000/v1",
    "api_key": KEY,
    "responses_api": False,
}
ROUTES = "freyja_agent.api.llm"


def add(client: TestClient, **values: Any) -> Any:
    return client.post("/llm/profiles", json={**NEW, **values})


def activate(client: TestClient, profile_id: str) -> Any:
    return client.post(f"/llm/profiles/{profile_id}/activate")


def refusal(response: Any, status: int, code: str) -> str:
    """The message of a refusal, after checking its status, its code, and that it has no key."""
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert KEY not in response.text
    return detail["message"]


def test_adding_a_profile_answers_with_it_saved_and_not_active(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        response = add(client)
        body = response.json()
        after = listing(client)

    assert response.status_code == 201, response.text
    assert body == {
        "id": body["id"],
        "name": "Local vLLM",
        "source": "saved",
        "provider": "openai",
        "model": "served-model",
        "base_url": "http://10.0.0.5:31000/v1",
        "responses_api": False,
        "key_hint": "0001",
        "active": False,
        "usable": True,
        "unusable_reason": None,
        "last_test": None,
        "created_at": body["created_at"],
        "updated_at": body["created_at"],
    }
    assert len(body["id"]) == 32
    assert int(body["id"], 16) >= 0
    # The key went in and never comes out: not whole, and no more of it than four characters.
    assert KEY not in response.text
    assert KEY[:-4] not in response.text
    assert "api_key" not in response.text
    # It is listed, and what was active still is.
    assert after["profiles"][1] == body
    assert after["active_id"] == "environment"
    # It is stored under the service's secret, not as it was sent.
    (record,) = asyncio.run(made.store.list_profiles())
    assert KEY not in record.api_key_encrypted
    assert decrypt(record.api_key_encrypted, made.settings.secret_key) == KEY


def test_the_api_choice_is_the_responses_api_unless_said_and_is_for_openai_only(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    unsaid = {key: value for key, value in NEW.items() if key != "responses_api"}
    with TestClient(made.app) as client:
        openai = client.post("/llm/profiles", json=unsaid)
        anthropic = client.post(
            "/llm/profiles",
            json={"name": "Claude", "provider": "anthropic", "model": "claude-x", "api_key": KEY},
        )
    assert (openai.status_code, anthropic.status_code) == (201, 201)
    assert openai.json()["responses_api"] is True
    assert anthropic.json()["responses_api"] is None
    assert anthropic.json()["base_url"] is None


def test_a_name_and_a_model_are_saved_trimmed(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        body = add(client, name="  Local vLLM ", model=" served-model ").json()
    assert (body["name"], body["model"]) == ("Local vLLM", "served-model")


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": None}, "name"),
        ({"provider": None}, "provider"),
        ({"model": None}, "model"),
        ({"api_key": None}, "api_key"),
        ({"name": "n" * 65}, "name"),
        ({"name": "   "}, "name"),
        ({"model": "m" * 201}, "model"),
        ({"provider": "someprovider"}, "provider"),
        ({"base_url": "not a web address"}, "base_url"),
        ({"base_url": "ftp://files.example/v1"}, "base_url"),
        ({"base_url": "http://host/" + "p" * 2000}, "base_url"),
        ({"api_key": KEY + "k" * 4000}, "api_key"),
        ({"api_key": ""}, "api_key"),
        ({"surprise": "sent-by-mistake"}, "surprise"),
    ],
)
def test_a_value_that_is_missing_or_breaks_a_rule_is_refused_by_name_and_never_repeated(
    make_llm_app: Callable[..., LlmApp], change: dict[str, Any], field: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    sent = {key: value for key, value in {**NEW, **change}.items() if value is not None}
    with TestClient(made.app) as client:
        response = client.post("/llm/profiles", json=sent)
        after = listing(client)

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert [error["loc"] for error in errors] == [["body", field]]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    # Neither the key nor the refused value comes back.
    assert KEY not in response.text
    for value in change.values():
        if value and str(value).strip():
            assert str(value) not in response.text
    # Nothing was saved.
    assert [profile["name"] for profile in after["profiles"]] == ["Environment"]


@pytest.mark.parametrize("name", ["local vllm", "LOCAL VLLM", " Local vLLM "])
def test_a_name_in_use_is_refused_whatever_its_case(
    make_llm_app: Callable[..., LlmApp], name: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        assert add(client).status_code == 201
        message = refusal(add(client, name=name, model="another"), 409, "name_taken")
        after = listing(client)
    assert "name" in message
    assert [profile["model"] for profile in after["profiles"][1:]] == ["served-model"]


@pytest.mark.parametrize("name", ["Environment", "environment", "ENVIRONMENT"])
def test_the_environments_name_cannot_be_taken(
    make_llm_app: Callable[..., LlmApp], name: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        refusal(add(client, name=name), 409, "name_taken")
        assert len(listing(client)["profiles"]) == 1


def test_the_fifty_first_profile_is_refused_and_fifty_are_listed_in_one_answer(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    for number in range(PROFILE_LIMIT):
        save(made, f"profile {number:02}")
    with TestClient(made.app) as client:
        message = refusal(add(client), 409, "profile_limit")
        after = listing(client)
    assert "50" in message
    # The environment's and all fifty, in one request.
    assert len(after["profiles"]) == PROFILE_LIMIT + 1 == 51
    assert after["limit"] == 50
    assert "Local vLLM" not in [profile["name"] for profile in after["profiles"]]


def test_without_a_secret_nothing_is_saved_and_the_reason_names_the_setting(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model", secret=None)
    with TestClient(made.app) as client:
        message = refusal(add(client), 409, "secret_not_configured")
        after = listing(client)
    assert "FREYJA_SECRET_KEY" in message
    assert len(after["profiles"]) == 1
    assert asyncio.run(made.store.list_profiles()) == []


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("text/plain", json.dumps(NEW)),
        ("application/x-www-form-urlencoded", "name=Local&provider=openai&model=m&api_key=" + KEY),
        ("application/json", "{not json"),
    ],
)
def test_a_body_that_is_not_sent_as_json_is_refused(
    make_llm_app: Callable[..., LlmApp], content_type: str, body: str
) -> None:
    """FR-003a: a page from another origin can send text or a form without being asked first."""
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        response = client.post(
            "/llm/profiles", content=body, headers={"content-type": content_type}
        )
        after = listing(client)
    assert response.status_code == 422, response.text
    assert KEY not in response.text
    assert len(after["profiles"]) == 1


def test_activating_a_profile_answers_with_the_list_and_exactly_one_active(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    other = save(made, "Other")
    with TestClient(made.app) as client:
        added = add(client).json()
        response = activate(client, added["id"])
        body = response.json()
        again = listing(client)

    assert response.status_code == 200, response.text
    assert body["active_id"] == added["id"]
    assert [(p["name"], p["active"]) for p in body["profiles"]] == [
        ("Environment", False),
        ("Local vLLM", True),
        ("Other", False),
    ]
    assert body["usable_model"] is True
    assert body == again
    assert other.id != added["id"]
    assert KEY not in response.text


def test_an_activation_is_applied_on_this_instance_at_once(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        built = len(made.models.built)
        assert activate(client, added["id"]).status_code == 200
        # No run has started, and the orchestrator is already built for the new profile,
        # from its own values, with no model asked anything.
        assert len(made.models.built) == built + 1
        spec = made.models.built[-1]
        assert (spec.provider, spec.model, spec.api_key) == ("openai", "served-model", KEY)
        assert made.runtime.applied_model().profile.name == "Local vLLM"
        assert all(model.calls == 0 for model in made.models.models.values())


def test_activating_the_active_profile_changes_nothing(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        first = activate(client, added["id"]).json()
        revision = asyncio.run(made.store.state()).revision
        built = len(made.models.built)
        second = activate(client, added["id"])
        environment = activate(client, "environment").json()
        environment_again = activate(client, "environment")
        after = asyncio.run(made.store.state())
    assert second.status_code == 200
    assert second.json() == first
    assert environment_again.json() == environment
    # One change for the profile and one for the environment, however often they were sent.
    assert (revision, after.revision) == (1, 2)
    assert len(made.models.built) == built + 1


def test_the_environments_profile_can_be_activated_again(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        activate(client, added["id"])
        response = activate(client, "environment")
        body = response.json()
    assert response.status_code == 200
    assert body["active_id"] == "environment"
    assert [(p["name"], p["active"]) for p in body["profiles"]] == [
        ("Environment", True),
        ("Local vLLM", False),
    ]
    assert asyncio.run(made.store.state()).active_profile_id is None


@pytest.mark.parametrize("profile_id", ["0" * 32, "nothing-like-an-id", "x" * 64])
def test_activating_a_profile_that_does_not_exist_changes_nothing(
    make_llm_app: Callable[..., LlmApp], profile_id: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        activate(client, added["id"])
        refusal(activate(client, profile_id), 404, "profile_not_found")
        assert listing(client)["active_id"] == added["id"]


def test_a_profile_whose_key_cannot_be_read_is_not_activated(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    other_secret = SecretStr(Fernet.generate_key().decode())
    unreadable = save(made, "Unreadable", api_key_encrypted=encrypt(KEY, other_secret))
    with TestClient(made.app) as client:
        added = add(client).json()
        activate(client, added["id"])
        built = len(made.models.built)
        message = refusal(activate(client, unreadable.id), 409, "profile_unusable")
        after = listing(client)
    assert "Unreadable" in message
    # The earlier profile stays active, and nothing was rebuilt.
    assert after["active_id"] == added["id"]
    assert len(made.models.built) == built
    assert asyncio.run(made.store.state()).revision == 1


def test_an_environment_with_no_key_is_not_activated(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        assert activate(client, added["id"]).status_code == 200
        refusal(activate(client, "environment"), 409, "profile_unusable")
        assert listing(client)["active_id"] == added["id"]


def test_each_addition_and_activation_is_logged_by_name_and_without_the_key(
    make_llm_app: Callable[..., LlmApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    with caplog.at_level(logging.INFO, logger="freyja_agent"), TestClient(made.app) as client:
        added = add(client).json()
        activate(client, added["id"])
        activate(client, added["id"])  # changes nothing, so it is not logged again
        activate(client, "environment")
        add(client, name="n" * 65)  # refused, so nothing to log

    logged = [r.getMessage() for r in caplog.records if r.name == ROUTES]
    assert len(logged) == 3
    addition, activation, back = logged
    assert "added" in addition and "Local vLLM" in addition and added["id"] in addition
    assert "activated" in activation and "Local vLLM" in activation
    assert "activated" in back and "Environment" in back
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY[:-4] not in record.getMessage()
