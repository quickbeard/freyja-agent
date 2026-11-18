"""004: the LLM profile routes (contracts/openapi.yaml).

The apps are assembled the production way with an in-memory profile store and a model factory
that returns scripted models, so no model endpoint exists to be contacted (research R18).
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from freyja_agent.api.llm import NoInputRoute
from freyja_agent.api.schemas import LlmProfileCreate
from freyja_agent.llm.secrets import encrypt
from freyja_agent.llm.store import LlmProfileRecord
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
