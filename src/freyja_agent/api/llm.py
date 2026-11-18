"""The LLM profile routes (feature 004, contracts/openapi.yaml).

Profiles are operator-managed configuration: kept in the service's database and changed only
through these routes (constitution principle IV). No response here carries a key, and a
validation error on these routes never repeats the value it refused, because that value may
be one (research R9).
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import SecretStr

from freyja_agent.api.schemas import LlmLastTest, LlmProfile, LlmProfileCreate, LlmProfileList
from freyja_agent.llm.providers import (
    ENVIRONMENT_ID,
    ENVIRONMENT_NAME,
    PROVIDERS,
    EnvironmentProfile,
)
from freyja_agent.llm.secrets import (
    KeyUnreadableError,
    SecretNotConfiguredError,
    decrypt,
    encrypt,
    key_hint,
    redact,
)
from freyja_agent.llm.store import (
    PROFILE_LIMIT,
    InvalidProfileError,
    LlmProfileRecord,
    NameTakenError,
    ProfileLimitError,
    ProfileNotFoundError,
)
from freyja_agent.runtime.runtime import Runtime

logger = logging.getLogger(__name__)


class NoInputRoute(APIRoute):
    """A route whose validation errors say where and why, and never what was sent.

    FastAPI's own 422 repeats the refused value under ``input``. On these routes that value
    may be an API key (FR-020).
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def without_input(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                detail = [
                    {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
                    for error in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detail})

        return without_input


router = APIRouter(prefix="/llm", tags=["llm"], route_class=NoInputRoute)


ProfileIdPath = Annotated[
    str, Path(min_length=1, max_length=64, description="A saved profile's id, or `environment`.")
]

UNUSABLE = {
    "key_unreadable": "its stored key cannot be read with the service's current secret",
    "secret_not_configured": "no secret is set on the service, so its stored key cannot be read",
    "not_configured": "the environment names no key for its provider",
    "cannot_build": "no model can be built from its values",
}


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _unusable(record: LlmProfileRecord, secret: SecretStr | None) -> str | None:
    """Why a saved profile cannot be used on this instance, or None (FR-011a)."""
    try:
        decrypt(record.api_key_encrypted, secret)
    except SecretNotConfiguredError:
        return "secret_not_configured"
    except KeyUnreadableError:
        return "key_unreadable"
    return None


def _saved(record: LlmProfileRecord, *, active: bool, secret: SecretStr | None) -> LlmProfile:
    reason = _unusable(record, secret)
    last = None
    if record.last_test_ok is not None and record.last_test_at is not None:
        last = LlmLastTest(
            ok=record.last_test_ok,
            at=record.last_test_at,
            step=record.last_test_step,
            reason=record.last_test_reason,
        )
    return LlmProfile(
        id=record.id,
        name=record.name,
        source="saved",
        provider=record.provider,
        model=record.model,
        base_url=record.base_url,
        # Stored for every profile and meaningful for a provider that has the choice.
        responses_api=record.responses_api if PROVIDERS[record.provider].has_api_choice else None,
        key_hint=record.key_hint,
        active=active,
        usable=reason is None,
        unusable_reason=reason,
        last_test=last,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _environment(profile: EnvironmentProfile, *, active: bool) -> LlmProfile:
    return LlmProfile(
        id=profile.id,
        name=profile.name,
        source="environment",
        provider=profile.provider,
        model=profile.model,
        base_url=profile.base_url,
        responses_api=profile.responses_api,
        key_hint=profile.key_hint,
        active=active,
        usable=profile.usable,
        unusable_reason=profile.unusable_reason,
        last_test=None,
        created_at=None,
        updated_at=None,
    )


async def profile_list(runtime: Runtime) -> LlmProfileList:
    """Every profile as this instance sees it: the environment's first, then the saved by name.

    Read from the database and from what this instance was started with. No model endpoint is
    contacted (FR-007, FR-008).
    """
    state = await runtime.llm_profiles.state()
    secret = runtime.settings.secret_key
    profiles = [
        _environment(runtime.environment_profile(), active=state.active_profile_id is None),
        *(
            _saved(record, active=record.id == state.active_profile_id, secret=secret)
            for record in await runtime.llm_profiles.list_profiles()
        ),
    ]
    return LlmProfileList(
        profiles=profiles,
        active_id=state.active_profile_id or ENVIRONMENT_ID,
        usable_model=any(profile.active and profile.usable for profile in profiles),
        secret_configured=secret is not None,
        limit=PROFILE_LIMIT,
    )


@router.get("/profiles")
async def list_profiles(request: Request) -> LlmProfileList:
    """List every LLM profile and say which one is active."""
    return await profile_list(_runtime(request))


@router.post("/profiles", status_code=201)
async def add_profile(body: LlmProfileCreate, request: Request) -> LlmProfile:
    """Save a new profile. It is not active, and its key is stored under the service's secret."""
    runtime = _runtime(request)
    secret = runtime.settings.secret_key
    key = body.api_key.get_secret_value()
    try:
        stored_key = encrypt(key, secret)
    except SecretNotConfiguredError as exc:
        raise _error(
            409,
            "secret_not_configured",
            "No profile can be saved: the service has no secret to protect its key with. "
            "Set FREYJA_SECRET_KEY and restart the service.",
        ) from exc
    try:
        record = await runtime.llm_profiles.create_profile(
            name=body.name,
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            api_key_encrypted=stored_key,
            key_hint=key_hint(key),
            responses_api=body.responses_api,
        )
    except NameTakenError as exc:
        raise _error(
            409, "name_taken", "Another profile has this name. Choose a different name."
        ) from exc
    except ProfileLimitError as exc:
        raise _error(
            409,
            "profile_limit",
            f"No more than {PROFILE_LIMIT} profiles can be saved. Delete one to add another.",
        ) from exc
    except InvalidProfileError as exc:
        # The request's own rules are the store's, so this is a rule the two disagree on.
        raise _error(500, "internal", "The profile could not be saved.") from exc
    logger.info(
        "LLM profile added: %s (%s): %s model %s",
        record.name,
        record.id,
        record.provider,
        redact(record.model, [key]),
    )
    return _saved(record, active=False, secret=secret)


@router.post("/profiles/{profile_id}/activate")
async def activate_profile(profile_id: ProfileIdPath, request: Request) -> LlmProfileList:
    """Make this profile the one the orchestrator uses, from the next run on.

    No model endpoint is contacted and no test is needed (FR-012). The choice is one row for
    every instance on the database; this instance applies it at once (FR-013, research R6).
    """
    runtime = _runtime(request)
    store = runtime.llm_profiles
    if profile_id == ENVIRONMENT_ID:
        target, name = None, ENVIRONMENT_NAME
        reason = runtime.environment_profile().unusable_reason
    else:
        record = await store.get_profile(profile_id)
        if record is None:
            raise _error(404, "profile_not_found", "There is no profile with this id.")
        target, name = record.id, record.name
        reason = _unusable(record, runtime.settings.secret_key)
    if reason is not None:
        raise _error(
            409,
            "profile_unusable",
            f"Profile {name} cannot be used: {UNUSABLE[reason]}. "
            "The profile that was active still is.",
        )

    before = await store.state()
    try:
        after = await store.activate(target)
    except ProfileNotFoundError as exc:  # deleted since it was read
        raise _error(404, "profile_not_found", "There is no profile with this id.") from exc
    if after.revision != before.revision:
        logger.info("LLM profile activated: %s (%s)", name, profile_id)
        try:
            await runtime.model_for_run()
        except Exception as exc:
            # The choice is made and every instance will take it up at its next run, this one
            # included. What failed here will fail there, where the run reports it.
            logger.warning(
                "LLM profile %s (%s) is active but could not be applied at once: %s",
                name,
                profile_id,
                type(exc).__name__,
            )
    return await profile_list(runtime)
