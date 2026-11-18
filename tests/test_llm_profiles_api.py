"""004: the LLM profile routes (contracts/openapi.yaml).

The apps are assembled the production way with an in-memory profile store and a model factory
that returns scripted models, so no model endpoint exists to be contacted (research R18).
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from contextlib import ExitStack
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
from tests.fakes import FailingChatModel, ScriptedChatModel

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


# Part 3 (T050): testing a profile.


def check(client: TestClient, profile_id: str) -> Any:
    return client.post(f"/llm/profiles/{profile_id}/test")


def check_values(client: TestClient, **values: Any) -> Any:
    draft = {key: value for key, value in NEW.items() if key != "name"}
    sent = {key: value for key, value in {**draft, **values}.items() if value is not None}
    return client.post("/llm/test", json=sent)


def in_background(call: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """Start a request that will wait inside a held model, and where its answer goes."""
    answer: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: answer.update(response=call()))
    thread.start()
    return thread, answer


def last_test_of(client: TestClient, name: str) -> dict[str, Any] | None:
    (profile,) = [p for p in listing(client)["profiles"] if p["name"] == name]
    return profile["last_test"]


def test_a_test_that_passes_is_answered_and_kept_with_the_profile(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        built = len(made.models.built)
        response = check(client, added["id"])
        body = response.json()
        kept = last_test_of(client, "Local vLLM")

    assert response.status_code == 200, response.text
    assert body == {"ok": True, "step": None, "reason": None, "at": body["at"], "recorded": True}
    assert kept == {"ok": True, "step": None, "reason": None, "at": body["at"]}
    # The model was built for the test from the profile's values, to try nothing twice, and
    # was asked the two steps.
    assert len(made.models.built) == built + 1
    spec = made.models.built[-1]
    assert (spec.provider, spec.model, spec.base_url) == (
        "openai",
        "served-model",
        "http://10.0.0.5:31000/v1",
    )
    assert (spec.api_key, spec.responses_api, spec.max_retries) == (KEY, False, 0)
    assert made.models.model("served-model").calls == 2
    assert KEY not in response.text


def test_a_test_that_fails_says_which_step_and_why_without_the_key(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    made.models.models["served-model"] = FailingChatModel(
        fail_on_call=2, error=f"Error code: 422 - earlier turns refused for {KEY}"
    )
    with TestClient(made.app) as client:
        added = add(client).json()
        response = check(client, added["id"])
        body = response.json()
        kept = last_test_of(client, "Local vLLM")

    # A failed test is still an answer: the result says what failed.
    assert response.status_code == 200, response.text
    assert (body["ok"], body["step"], body["recorded"]) == (False, "conversation", True)
    assert body["reason"] == "Error code: 422 - earlier turns refused for <key>"
    assert kept == {key: body[key] for key in ("ok", "step", "reason", "at")}
    assert KEY not in response.text


def test_a_test_ends_at_the_time_limit(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model", llm_test_timeout_seconds=0.2)
    model = made.models.model("served-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        model.hold()
        body = check(client, added["id"]).json()
        with model.held():
            pass  # let the held call go, now that the test gave up on it
    assert (body["ok"], body["step"], body["recorded"]) == (False, "first_message", True)
    assert "did not answer within 0.2 seconds" in body["reason"]


@pytest.mark.parametrize("change", ["edited", "deleted"])
def test_a_result_for_a_profile_that_changed_while_it_was_tested_is_not_kept(
    make_llm_app: Callable[..., LlmApp], change: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    model = made.models.model("served-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        model.hold()
        thread, answer = in_background(lambda: check(client, added["id"]))
        with model.held():
            if change == "edited":
                client.portal.call(
                    lambda: made.store.update_profile(added["id"], model="another-model")
                )
            else:
                client.portal.call(made.store.delete_profile, added["id"])
        thread.join(10)
        body = answer["response"].json()
        names = [p["name"] for p in listing(client)["profiles"]]

    # The operator is told how the test went, and that it says nothing of the profile now.
    assert answer["response"].status_code == 200
    assert (body["ok"], body["recorded"]) == (True, False)
    if change == "edited":
        record = asyncio.run(made.store.get_profile(added["id"]))
        assert record is not None and record.last_test_ok is None
    else:
        assert names == ["Environment"]


def test_the_environments_result_is_kept_until_the_service_starts_again(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        response = check(client, "environment")
        body = response.json()
        kept = last_test_of(client, "Environment")
        # The environment's model is tested as it is configured: no key or address of its own.
        spec = made.models.built[-1]
        assert (spec.name, spec.api_key, spec.base_url, spec.max_retries) == (
            "openai:env-model",
            None,
            None,
            0,
        )

    assert response.status_code == 200, response.text
    assert (body["ok"], body["recorded"]) == (True, True)
    assert kept == {key: body[key] for key in ("ok", "step", "reason", "at")}

    restarted = make_llm_app(model="openai:env-model", store=made.store)
    with TestClient(restarted.app) as client:
        assert last_test_of(client, "Environment") is None


def test_a_failed_test_of_the_environment_has_its_key_removed(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    made.models.models["env-model"] = FailingChatModel(
        fail_on_call=1, error=f"401: Incorrect API key provided: {ENV_KEY}"
    )
    with TestClient(made.app) as client:
        body = check(client, "environment").json()
    assert (body["ok"], body["step"]) == (False, "first_message")
    assert body["reason"] == "401: Incorrect API key provided: <key>"


def test_unsaved_values_are_tested_and_nothing_is_saved_or_kept(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        response = check_values(client)
        body = response.json()
        after = listing(client)

    assert response.status_code == 200, response.text
    assert body == {"ok": True, "step": None, "reason": None, "at": body["at"], "recorded": False}
    assert [p["name"] for p in after["profiles"]] == ["Environment"]
    assert asyncio.run(made.store.list_profiles()) == []
    spec = made.models.built[-1]
    assert (spec.model, spec.base_url, spec.api_key, spec.responses_api, spec.max_retries) == (
        "served-model",
        "http://10.0.0.5:31000/v1",
        KEY,
        False,
        0,
    )
    assert KEY not in response.text


def test_unsaved_values_with_no_key_are_tested_with_a_saved_profiles_key(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        response = check_values(client, api_key=None, profile_id=added["id"], model="newer-model")
        body = response.json()
        kept = last_test_of(client, "Local vLLM")

    assert response.status_code == 200, response.text
    assert (body["ok"], body["recorded"]) == (True, False)
    # The form's values, with the key the profile already has.
    spec = made.models.built[-1]
    assert (spec.model, spec.api_key) == ("newer-model", KEY)
    # The profile itself was not tested and not changed.
    assert kept is None
    (record,) = asyncio.run(made.store.list_profiles())
    assert (record.model, record.config_version) == ("served-model", 1)


def test_unsaved_values_need_a_key_from_somewhere(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        built = len(made.models.built)
        neither = check_values(client, api_key=None)
        bad_address = check_values(client, base_url="not a web address")
        unknown = check_values(client, api_key=None, profile_id="0" * 32)
    assert neither.status_code == 422
    assert all(set(error) == {"type", "loc", "msg"} for error in neither.json()["detail"])
    assert bad_address.status_code == 422
    assert [error["loc"] for error in bad_address.json()["detail"]] == [["body", "base_url"]]
    refusal(unknown, 404, "profile_not_found")
    assert len(made.models.built) == built


def test_a_profile_that_is_not_usable_or_not_there_is_not_tested(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    other_secret = SecretStr(Fernet.generate_key().decode())
    unreadable = save(made, "Unreadable", api_key_encrypted=encrypt(KEY, other_secret))
    with TestClient(made.app) as client:
        built = len(made.models.built)
        message = refusal(check(client, unreadable.id), 409, "profile_unusable")
        refusal(
            check_values(client, api_key=None, profile_id=unreadable.id), 409, "profile_unusable"
        )
        refusal(check(client, "0" * 32), 404, "profile_not_found")
    assert "Unreadable" in message
    assert len(made.models.built) == built


def test_a_profile_is_tested_once_at_a_time(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    model = made.models.model("served-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        other = add(client, name="Other", model="other-model").json()
        model.hold()
        thread, answer = in_background(lambda: check(client, added["id"]))
        with model.held():
            built = len(made.models.built)
            message = refusal(check(client, added["id"]), 409, "test_busy")
            # Nothing was built for the refused test, and so nothing was sent.
            assert len(made.models.built) == built
            # Another profile can be tested meanwhile.
            assert check(client, other["id"]).json()["ok"] is True
        thread.join(10)
        # Once it has ended, the profile can be tested again.
        again = check(client, added["id"])

    assert "already being tested" in message
    assert answer["response"].json()["ok"] is True
    assert again.status_code == 200
    assert made.models.model("served-model").calls == 4


def test_a_fifth_test_on_the_instance_is_refused(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    held = [made.models.model(f"held-{number}") for number in range(4)]
    with TestClient(made.app) as client:
        added = add(client).json()
        for model in held:
            model.hold()
        running = [
            in_background(lambda label=model.label: check_values(client, model=label))
            for model in held
        ]
        with ExitStack() as stack:
            for model in held:
                stack.enter_context(model.held())
            built = len(made.models.built)
            for refused in (
                check_values(client, model="one-more"),
                check(client, added["id"]),
            ):
                message = refusal(refused, 409, "test_busy")
                assert "4 tests" in message
            assert len(made.models.built) == built
        for thread, _answer in running:
            thread.join(10)
        # A slot is free again.
        assert check(client, added["id"]).json()["ok"] is True

    assert all(answer["response"].json()["ok"] for _thread, answer in running)


def test_a_test_changes_nothing_but_the_profiles_last_result(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        activate(client, added["id"])
        before = listing(client)
        state = asyncio.run(made.store.state())
        built = len(made.models.built)

        check(client, added["id"])
        check(client, "environment")
        check_values(client)

        after = listing(client)
        # No thread exists: a test is not a run.
        assert client.get("/threads/any/state").status_code == 404
        applied = made.runtime.applied_model().profile.name

    def without_tests(body: dict[str, Any]) -> dict[str, Any]:
        profiles = [{**profile, "last_test": None} for profile in body["profiles"]]
        return {**body, "profiles": profiles}

    assert without_tests(after) == without_tests(before)
    assert [p["last_test"] is not None for p in after["profiles"]] == [True, True]
    assert asyncio.run(made.store.state()) == state
    assert applied == "Local vLLM"
    # Three models were built for the three tests, and the one in use was not rebuilt.
    assert len(made.models.built) == built + 3


# Part 4 (T056): changing and deleting a profile.

NEW_KEY = "sk-another-key-for-tests-0002"


def change(client: TestClient, profile_id: str, **values: Any) -> Any:
    return client.patch(f"/llm/profiles/{profile_id}", json=values)


def delete(client: TestClient, profile_id: str) -> Any:
    return client.delete(f"/llm/profiles/{profile_id}")


def stored(made: LlmApp, profile_id: str) -> LlmProfileRecord:
    record = asyncio.run(made.store.get_profile(profile_id))
    assert record is not None
    return record


def test_a_change_touches_only_the_fields_sent(make_llm_app: Callable[..., LlmApp]) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        response = change(client, added["id"], model="newer-model")
        body = response.json()
        listed = listing(client)["profiles"][1]

    assert response.status_code == 200, response.text
    assert body == {**added, "model": "newer-model", "updated_at": body["updated_at"]}
    assert body["updated_at"] >= added["updated_at"]
    assert listed == body
    assert KEY not in response.text
    assert "api_key" not in response.text


def test_a_key_left_out_keeps_the_stored_key_and_a_key_sent_replaces_it(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        token = stored(made, added["id"]).api_key_encrypted

        renamed = change(client, added["id"], name="Renamed", base_url=None).json()
        kept = stored(made, added["id"])
        assert (renamed["name"], renamed["base_url"], renamed["key_hint"]) == (
            "Renamed",
            None,
            "0001",
        )
        assert kept.api_key_encrypted == token

        response = change(client, added["id"], api_key=NEW_KEY)
        replaced = stored(made, added["id"])

    assert response.status_code == 200, response.text
    assert response.json()["key_hint"] == "0002"
    assert decrypt(replaced.api_key_encrypted, made.settings.secret_key) == NEW_KEY
    assert NEW_KEY not in response.text and KEY not in response.text


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "openrouter"},
        {"model": "newer-model"},
        {"base_url": "http://10.0.0.6:31000/v1"},
        {"base_url": None},
        {"api_key": NEW_KEY},
        {"api_key": KEY},
        {"responses_api": True},
    ],
)
def test_a_change_to_how_the_model_is_reached_clears_the_last_test(
    make_llm_app: Callable[..., LlmApp], values: dict[str, Any]
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        assert check(client, added["id"]).json()["recorded"] is True
        assert last_test_of(client, "Local vLLM") is not None
        body = change(client, added["id"], **values).json()
        assert body["last_test"] is None
        assert last_test_of(client, "Local vLLM") is None


def test_a_change_of_name_or_to_the_same_values_keeps_the_last_test(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        tested = check(client, added["id"]).json()
        renamed = change(client, added["id"], name="Renamed").json()
        same = change(
            client, added["id"], model="served-model", responses_api=False, provider="openai"
        ).json()
    assert renamed["last_test"]["at"] == tested["at"]
    assert same["last_test"]["at"] == tested["at"]
    assert same["updated_at"] == renamed["updated_at"]


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"name": "n" * 65}, "name"),
        ({"name": "  "}, "name"),
        ({"model": "m" * 201}, "model"),
        ({"provider": "someprovider"}, "provider"),
        ({"base_url": "not a web address"}, "base_url"),
        ({"api_key": NEW_KEY + "k" * 4000}, "api_key"),
        ({"surprise": "sent-by-mistake"}, "surprise"),
    ],
)
def test_a_change_that_breaks_a_rule_is_refused_by_name_and_changes_nothing(
    make_llm_app: Callable[..., LlmApp], values: dict[str, Any], field: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        response = change(client, added["id"], **values)
        after = listing(client)["profiles"][1]

    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["detail"]] == [["body", field]]
    assert NEW_KEY not in response.text
    assert after == added


@pytest.mark.parametrize(
    "values", [{}, {"name": None}, {"model": None}, {"provider": None}, {"api_key": None}]
)
def test_a_change_must_change_something_and_only_a_base_url_can_be_cleared(
    make_llm_app: Callable[..., LlmApp], values: dict[str, Any]
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        response = change(client, added["id"], **values)
        after = listing(client)["profiles"][1]
    assert response.status_code == 422, response.text
    assert after == added


def test_a_change_cannot_take_a_name_in_use_or_the_environments(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        add(client, name="Other")
        refusal(change(client, added["id"], name="other"), 409, "name_taken")
        refusal(change(client, added["id"], name="ENVIRONMENT"), 409, "name_taken")
        # Its own name in another case is its own to take.
        assert change(client, added["id"], name="LOCAL VLLM").json()["name"] == "LOCAL VLLM"


def test_a_new_key_cannot_be_stored_without_a_secret_and_other_changes_can(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    with_secret = make_llm_app(model="openai:env-model")
    saved = save(with_secret, "Stub")
    made = make_llm_app(model="openai:env-model", store=with_secret.store, secret=None)
    with TestClient(made.app) as client:
        message = refusal(change(client, saved.id, api_key=NEW_KEY), 409, "secret_not_configured")
        renamed = change(client, saved.id, name="Renamed")
    assert "FREYJA_SECRET_KEY" in message
    assert NEW_KEY not in message
    assert renamed.status_code == 200
    assert stored(made, saved.id).api_key_encrypted == saved.api_key_encrypted


def test_entering_its_key_again_makes_an_unreadable_profile_usable(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    other_secret = SecretStr(Fernet.generate_key().decode())
    unreadable = save(made, "Unreadable", api_key_encrypted=encrypt(KEY, other_secret))
    with TestClient(made.app) as client:
        before = listing(client)["profiles"][1]
        body = change(client, unreadable.id, api_key=NEW_KEY).json()
    assert (before["usable"], before["unusable_reason"]) == (False, "key_unreadable")
    assert (body["usable"], body["unusable_reason"], body["key_hint"]) == (True, None, "0002")


def test_the_environments_profile_can_be_neither_changed_nor_deleted(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        before = listing(client)
        refusal(change(client, "environment", name="Mine"), 409, "profile_read_only")
        refusal(change(client, "environment", api_key=NEW_KEY), 409, "profile_read_only")
        refusal(delete(client, "environment"), 409, "profile_read_only")
        assert listing(client) == before


def test_a_profile_that_is_not_active_is_deleted_and_stays_gone(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        response = delete(client, added["id"])
        names = [p["name"] for p in listing(client)["profiles"]]
        # It can no longer be activated, tested, changed, or deleted.
        refusal(activate(client, added["id"]), 404, "profile_not_found")
        refusal(check(client, added["id"]), 404, "profile_not_found")
        refusal(change(client, added["id"], name="Back"), 404, "profile_not_found")
        refusal(delete(client, added["id"]), 404, "profile_not_found")
        # Its name is free again.
        assert add(client).status_code == 201

    assert response.status_code == 204
    assert response.content == b""
    assert names == ["Environment"]

    restarted = make_llm_app(model="openai:env-model", store=made.store)
    with TestClient(restarted.app) as client:
        assert added["id"] not in [p["id"] for p in listing(client)["profiles"]]


def test_the_active_profile_is_not_deleted_and_the_refusal_says_what_to_do(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        activate(client, added["id"])
        message = refusal(delete(client, added["id"]), 409, "profile_active")
        after = listing(client)
        # Once another profile is active it can go.
        activate(client, "environment")
        assert delete(client, added["id"]).status_code == 204
    assert "ctivate another profile first" in message
    assert after["active_id"] == added["id"]


def test_a_change_to_the_active_profile_is_applied_on_this_instance_at_once(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        other = add(client, name="Other").json()
        activate(client, added["id"])
        built = len(made.models.built)

        change(client, other["id"], model="not-in-use")
        assert len(made.models.built) == built  # not the active one: nothing to rebuild

        body = change(client, added["id"], model="newer-model").json()
        assert body["active"] is True
        assert len(made.models.built) == built + 1
        assert made.models.built[-1].model == "newer-model"
        assert all(model.calls == 0 for model in made.models.models.values())


def test_each_change_and_deletion_is_logged_by_name_and_without_a_key(
    make_llm_app: Callable[..., LlmApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        with caplog.at_level(logging.INFO, logger="freyja_agent"):
            caplog.clear()  # the addition was logged too, and is another test's
            change(client, added["id"], name="Renamed", api_key=NEW_KEY)
            change(client, added["id"], name="Renamed")  # changes nothing: not logged
            change(client, added["id"], name="n" * 65)  # refused: not logged
            delete(client, added["id"])

    logged = [r.getMessage() for r in caplog.records if r.name == ROUTES]
    assert len(logged) == 2
    changed, deleted = logged
    assert "changed" in changed and "Renamed" in changed and added["id"] in changed
    # It says what changed, and of the key only that it did.
    assert "name" in changed and "key" in changed
    assert "deleted" in deleted and "Renamed" in deleted
    for record in caplog.records:
        assert NEW_KEY not in record.getMessage() and KEY not in record.getMessage()


# Part 5 (T069): a missing or changed secret, and requests from another origin.

OTHER_ORIGIN = "http://other.example"
LLM_ROUTES = [
    ("GET", "/llm/profiles"),
    ("POST", "/llm/profiles"),
    ("PATCH", "/llm/profiles/{id}"),
    ("DELETE", "/llm/profiles/{id}"),
    ("POST", "/llm/profiles/{id}/activate"),
    ("POST", "/llm/profiles/{id}/test"),
    ("POST", "/llm/test"),
]


def test_without_a_secret_the_environment_works_as_before_and_nothing_is_saved(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    with_secret = make_llm_app(model="openai:env-model")
    saved = save(with_secret, "Stub")
    made = make_llm_app(model="openai:env-model", store=with_secret.store, secret=None)
    with TestClient(made.app) as client:
        body = listing(client)
        message = refusal(add(client), 409, "secret_not_configured")
        with client.stream("POST", "/threads/t-1/runs", json={"message": "hello"}) as stream:
            events = sse_events(stream.iter_lines())
        tested = check(client, "environment").json()
        # A saved profile's key cannot be read, so it is neither activated nor tested.
        refusal(activate(client, saved.id), 409, "profile_unusable")
        refusal(check(client, saved.id), 409, "profile_unusable")
        after = listing(client)

    assert "FREYJA_SECRET_KEY" in message
    assert body["secret_configured"] is False
    environment, profile = body["profiles"]
    # The environment's profile lists, runs, and tests as it does with a secret.
    assert (environment["active"], environment["usable"]) == (True, True)
    assert body["usable_model"] is True
    assert events[0][1]["profile"]["id"] == "environment"
    assert events[-1] == ("end", {"status": "completed"})
    assert tested["ok"] is True
    assert (profile["usable"], profile["unusable_reason"]) == (False, "secret_not_configured")
    assert after["active_id"] == "environment"
    assert len(after["profiles"]) == 2


def test_under_another_secret_the_service_starts_and_a_key_entered_again_is_usable(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    before = make_llm_app(model="openai:env-model")
    first = save(before, "First")
    second = save(before, "Second", model="second-model")
    # The same database, and a service started with a secret the keys were not stored under.
    made = make_llm_app(
        model="openai:env-model",
        store=before.store,
        secret=Fernet.generate_key().decode(),
    )
    with TestClient(made.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        body = listing(client)
        for profile_id in (first.id, second.id):
            refusal(activate(client, profile_id), 409, "profile_unusable")
            refusal(check(client, profile_id), 409, "profile_unusable")

        # Its key is sent again: stored under the secret the service has now.
        fixed = change(client, first.id, api_key=KEY)
        assert activate(client, first.id).status_code == 200
        assert check(client, first.id).json()["ok"] is True
        with client.stream("POST", "/threads/t-1/runs", json={"message": "hello"}) as stream:
            events = sse_events(stream.iter_lines())
        after = listing(client)

    assert body["secret_configured"] is True
    assert [(p["name"], p["usable"], p["unusable_reason"]) for p in body["profiles"]] == [
        ("Environment", True, None),
        ("First", False, "key_unreadable"),
        ("Second", False, "key_unreadable"),
    ]
    assert (fixed.json()["usable"], fixed.json()["unusable_reason"]) == (True, None)
    assert events[0][1]["profile"]["name"] == "First"
    assert events[-1] == ("end", {"status": "completed"})
    # The other one is as it was.
    assert [(p["name"], p["usable"]) for p in after["profiles"]] == [
        ("Environment", True),
        ("First", True),
        ("Second", False),
    ]


@pytest.mark.parametrize(("method", "path"), LLM_ROUTES)
def test_a_page_from_another_origin_is_given_permission_on_no_route(
    make_llm_app: Callable[..., LlmApp], method: str, path: str
) -> None:
    """SC-012: with no permission, a browser lets such a page read nothing and send no JSON."""
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = add(client).json()
        url = path.format(id=added["id"])
        preflight = client.options(
            url,
            headers={
                "Origin": OTHER_ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )
        # The request itself, sent as a page from that origin sends it.
        sent = client.request(method, url, headers={"Origin": OTHER_ORIGIN})

    for response in (preflight, sent):
        allowed = [name for name in response.headers if name.lower().startswith("access-control-")]
        assert allowed == [], (response.status_code, allowed)
    # A preflight is answered with no permission at all, not with a profile.
    assert preflight.status_code in (400, 405)
    assert KEY not in preflight.text
    # Reading is answered, and the browser keeps the answer from the page. Anything that
    # would change something is refused outright.
    if method == "GET":
        assert sent.status_code == 200
    else:
        refusal(sent, 403, "other_origin")


# What a browser sends with a request a page of another origin makes. A form needs no
# preflight, so the service itself has to turn these away (FR-003a).
FROM_ANOTHER_ORIGIN = [
    {"Origin": OTHER_ORIGIN},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    # Another port of this machine is another origin, and the same site.
    {"Origin": "http://localhost:3000", "Sec-Fetch-Site": "same-site"},
    # Through the web app's server, which names the host the browser asked for.
    {"Origin": OTHER_ORIGIN, "X-Forwarded-Host": "localhost:3000"},
]


@pytest.mark.parametrize("headers", FROM_ANOTHER_ORIGIN)
def test_a_form_from_another_origin_cannot_switch_the_model_or_start_a_test(
    make_llm_app: Callable[..., LlmApp], headers: dict[str, str]
) -> None:
    """FR-003a: the two routes that take no body are reached by a plain form post.

    The environment's profile has an id anyone knows, so a page could put it back in use and
    have it tested.
    """
    made = make_llm_app(model="openai:env-model")
    form = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    with TestClient(made.app) as client:
        added = add(client).json()
        assert activate(client, added["id"]).status_code == 200
        asked = made.models.model("env-model").calls
        switched = client.post("/llm/profiles/environment/activate", headers=form, content="")
        tested = client.post("/llm/profiles/environment/test", headers=form, content="")
        deleted = client.delete("/llm/profiles/environment", headers=headers)
        after = listing(client)

    for response in (switched, tested, deleted):
        message = refusal(response, 403, "other_origin")
        assert "another origin" in message
    # Nothing changed, and no model was asked anything.
    assert after["active_id"] == added["id"]
    assert after["profiles"][0]["last_test"] is None
    assert made.models.model("env-model").calls == asked


@pytest.mark.parametrize(
    "headers",
    [
        # A client that is no browser page, such as curl, sends none of these.
        {},
        # The console, straight to the service on the address it was opened at.
        {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        # The console through the web app's server, which passes on the host it was asked for.
        {
            "Origin": "http://localhost:3000",
            "X-Forwarded-Host": "localhost:3000",
            "Sec-Fetch-Site": "same-origin",
        },
    ],
)
def test_the_console_and_a_client_that_is_no_page_are_not_turned_away(
    make_llm_app: Callable[..., LlmApp], headers: dict[str, str]
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = client.post("/llm/profiles", json=NEW, headers=headers)
        profile_id = added.json()["id"]
        switched = client.post(f"/llm/profiles/{profile_id}/activate", headers=headers)
        tested = client.post(f"/llm/profiles/{profile_id}/test", headers=headers)
        changed = client.patch(
            f"/llm/profiles/{profile_id}", json={"name": "Renamed"}, headers=headers
        )
        back = client.post("/llm/profiles/environment/activate", headers=headers)
        deleted = client.delete(f"/llm/profiles/{profile_id}", headers=headers)

    assert [r.status_code for r in (added, switched, tested, changed, back, deleted)] == [
        201,
        200,
        200,
        200,
        200,
        204,
    ]


# How a page of another origin can send a body without asking first: as text, as a form, or
# with no content type at all.
NOT_JSON = ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/threads/t-1/runs", {"message": "hello"}),
        ("/threads/t-1/resume", {"interrupt_id": "any", "decisions": [{"type": "approve"}]}),
    ],
)
@pytest.mark.parametrize("content_type", NOT_JSON)
def test_a_page_of_another_origin_cannot_start_or_resume_a_run(
    make_llm_app: Callable[..., LlmApp], path: str, body: dict[str, Any], content_type: str | None
) -> None:
    """FR-003a is about the service's API, the thread routes included.

    They take a body, so a page of another origin can only reach them with one of these. Each
    is refused before anything is looked at: a resume that was read would be answered with
    `nothing_pending`, and a run would start.
    """
    made = make_llm_app(model="openai:env-model")
    headers = {"Origin": OTHER_ORIGIN, "Sec-Fetch-Site": "cross-site"}
    if content_type:
        headers["content-type"] = content_type
    with TestClient(made.app) as client:
        response = client.post(path, content=json.dumps(body), headers=headers)
        state = client.get("/threads/t-1/state")

    assert response.status_code == 422, response.text
    assert not response.headers.get("content-type", "").startswith("text/event-stream")
    # No run started: the thread does not exist, and no model was asked anything.
    assert state.status_code == 404
    assert all(model.calls == 0 for model in made.models.models.values())


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/llm/profiles"), ("PATCH", "/llm/profiles/{id}"), ("POST", "/llm/test")],
)
@pytest.mark.parametrize("content_type", NOT_JSON)
def test_every_route_that_takes_a_body_refuses_one_not_sent_as_json(
    make_llm_app: Callable[..., LlmApp], method: str, path: str, content_type: str | None
) -> None:
    """FR-003a: these are the bodies a page from another origin can send without asking."""
    made = make_llm_app(model="openai:env-model")
    body = json.dumps({**NEW, "name": "Sent as text"})
    with TestClient(made.app) as client:
        added = add(client).json()
        built = len(made.models.built)
        response = client.request(
            method,
            path.format(id=added["id"]),
            content=body,
            headers={"content-type": content_type} if content_type else {},
        )
        after = listing(client)

    assert response.status_code == 422, response.text
    assert KEY not in response.text
    # Nothing was saved, changed, or tested.
    assert [p["name"] for p in after["profiles"]] == ["Environment", "Local vLLM"]
    assert after["profiles"][1] == added
    assert len(made.models.built) == built
