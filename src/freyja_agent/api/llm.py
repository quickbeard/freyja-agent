"""The LLM profile routes (feature 004, contracts/openapi.yaml).

Profiles are operator-managed configuration: kept in the service's database and changed only
through these routes (constitution principle IV). No response here carries a key, and a
validation error on these routes never repeats the value it refused, because that value may
be one (research R9).
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import SecretStr

from freyja_agent.api.schemas import LlmLastTest, LlmProfile, LlmProfileList
from freyja_agent.llm.providers import ENVIRONMENT_ID, PROVIDERS, EnvironmentProfile
from freyja_agent.llm.secrets import KeyUnreadableError, SecretNotConfiguredError, decrypt
from freyja_agent.llm.store import PROFILE_LIMIT, LlmProfileRecord
from freyja_agent.runtime.runtime import Runtime


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


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


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
