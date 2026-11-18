"""The LLM profile routes (feature 004, contracts/openapi.yaml).

Profiles are operator-managed configuration: kept in the service's database and changed only
through these routes (constitution principle IV). No response here carries a key, and a
validation error on these routes never repeats the value it refused, because that value may
be one (research R9).
"""

import logging
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import SecretStr

from freyja_agent.api.schemas import (
    LlmLastTest,
    LlmProfile,
    LlmProfileCreate,
    LlmProfileDraft,
    LlmProfileList,
    LlmProfileUpdate,
    LlmTestResult,
)
from freyja_agent.llm.active import UNUSABLE, unusable_reason
from freyja_agent.llm.check import CheckResult, LastCheck, check_model
from freyja_agent.llm.providers import (
    ENVIRONMENT_ID,
    ENVIRONMENT_NAME,
    PROVIDERS,
    EnvironmentProfile,
    ModelSpec,
)
from freyja_agent.llm.secrets import (
    SecretNotConfiguredError,
    decrypt,
    encrypt,
    key_hint,
    redact,
)
from freyja_agent.llm.store import (
    PROFILE_LIMIT,
    REASON_LIMIT,
    InvalidProfileError,
    LlmProfileRecord,
    NameTakenError,
    ProfileActiveError,
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


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# Requests that only read. A page of another origin is not kept from sending one: with no
# permission given, its browser keeps the answer from it (SC-012).
READS = frozenset({"GET", "HEAD", "OPTIONS"})
# What a browser says of a request a page made to its own origin, or the user made directly.
OWN_SITE = frozenset({"same-origin", "none"})


def refuse_other_origins(request: Request) -> None:
    """Turn away a request that changes something and was made by a page of another origin.

    A browser asks first before it sends JSON across origins, and is given no permission. It
    does not ask before a form post, which is all the routes without a body need (FR-003a).
    So what the browser says of the request decides: ``Sec-Fetch-Site`` where it sends it, and
    ``Origin`` against the host the browser asked for, which the web app's server passes on as
    ``X-Forwarded-Host``. A page of another origin can set none of the three. A client that
    is no page, such as curl, sends none of them and is served.
    """
    if request.method in READS:
        return
    headers = request.headers
    site = headers.get("sec-fetch-site")
    origin = headers.get("origin")
    asked = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0]
    asked = asked.strip().lower()
    other_site = site is not None and site.lower() not in OWN_SITE
    other_origin = origin is not None and (not asked or urlsplit(origin).netloc.lower() != asked)
    if other_site or other_origin:
        logger.warning(
            "Refused %s %s: the request was made by a page of another origin",
            request.method,
            request.url.path,
        )
        raise _error(
            403,
            "other_origin",
            "This request was made by a page of another origin and was refused. Profiles are "
            "changed from the console, or by a client that is not a web page.",
        )


router = APIRouter(
    prefix="/llm",
    tags=["llm"],
    route_class=NoInputRoute,
    dependencies=[Depends(refuse_other_origins)],
)


ProfileIdPath = Annotated[
    str, Path(min_length=1, max_length=64, description="A saved profile's id, or `environment`.")
]


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _saved(
    record: LlmProfileRecord, *, active: bool, secret: SecretStr | None, unbuildable: bool = False
) -> LlmProfile:
    """A saved profile as the list shows it. ``unbuildable``: this instance tried to build its
    model and could not, which a key that can be read does not tell."""
    reason = unusable_reason(record, secret)
    if reason is None and unbuildable:
        reason = "cannot_build"
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


def _environment(
    profile: EnvironmentProfile, *, active: bool, last: LastCheck | None
) -> LlmProfile:
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
        # Kept in memory, so until the service starts again (FR-019a).
        last_test=None
        if last is None
        else LlmLastTest(ok=last.ok, at=last.at, step=last.step, reason=last.reason),
        created_at=None,
        updated_at=None,
    )


async def profile_list(runtime: Runtime) -> LlmProfileList:
    """Every profile as this instance sees it: the environment's first, then the saved by name.

    Read from the database and from what this instance was started with. No model endpoint is
    contacted (FR-007, FR-008).
    """
    # Take up the active choice first, as a run would: whether the active profile's model can
    # be built is known only once this instance has tried, and the list has to say that no
    # usable model is active before a run is turned away here, not after (FR-016). Building
    # opens no connection.
    try:
        await runtime.model_for_run()
    except Exception as exc:
        # The list is still what the database says; a run reports what is wrong.
        logger.warning("The active LLM profile could not be applied: %s", type(exc).__name__)
    state = await runtime.llm_profiles.state()
    secret = runtime.settings.secret_key
    # The active profile's model, as this instance applied it: runs are turned away from one
    # that could not be built, and the list has to say so for as long as they are (FR-016).
    applied = runtime.applied_model()
    unbuildable = (
        applied.profile.id
        if applied.why == "cannot_build" and applied.revision == state.revision
        else None
    )
    profiles = [
        _environment(
            runtime.environment_profile(),
            active=state.active_profile_id is None,
            last=runtime.environment_test,
        ),
        *(
            _saved(
                record,
                active=record.id == state.active_profile_id,
                secret=secret,
                unbuildable=record.id == unbuildable and record.id == state.active_profile_id,
            )
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
        reason = unusable_reason(record, runtime.settings.secret_key)
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
        await _apply_now(runtime, name, profile_id)
    return await profile_list(runtime)


READ_ONLY = (
    "The environment's profile shows what the service was started with. It cannot be changed "
    "or deleted here; change the service's environment and restart it."
)


async def _apply_now(runtime: Runtime, name: str, profile_id: str) -> None:
    """Bring this instance in step at once after the active choice or profile changed (R6).

    The change itself stands whatever happens here: every instance takes it up at its next
    run, this one included, and a run reports what is wrong.
    """
    try:
        await runtime.model_for_run()
    except Exception as exc:
        logger.warning(
            "LLM profile %s (%s) is active but could not be applied at once: %s",
            name,
            profile_id,
            type(exc).__name__,
        )


@router.patch("/profiles/{profile_id}")
async def change_profile(
    profile_id: ProfileIdPath, body: LlmProfileUpdate, request: Request
) -> LlmProfile:
    """Change a saved profile. Only the fields sent are changed (FR-009).

    A key left out keeps the stored key. A change to how the model is reached clears the last
    test result. A change to the active profile takes effect at the next run.
    """
    runtime = _runtime(request)
    if profile_id == ENVIRONMENT_ID:
        raise _error(409, "profile_read_only", READ_ONLY)
    store = runtime.llm_profiles
    secret = runtime.settings.secret_key
    before = await store.get_profile(profile_id)
    if before is None:
        raise _error(404, "profile_not_found", "There is no profile with this id.")

    changes = body.model_dump(exclude_unset=True)
    key = body.api_key.get_secret_value() if body.api_key is not None else None
    changes.pop("api_key", None)
    if key is not None:
        try:
            changes["api_key_encrypted"] = encrypt(key, secret)
        except SecretNotConfiguredError as exc:
            raise _error(
                409,
                "secret_not_configured",
                "A new key cannot be stored: the service has no secret to protect it with. "
                "Set FREYJA_SECRET_KEY and restart the service.",
            ) from exc
        changes["key_hint"] = key_hint(key)
    revision = (await store.state()).revision
    try:
        record = await store.update_profile(profile_id, **changes)
    except ProfileNotFoundError as exc:  # deleted since it was read
        raise _error(404, "profile_not_found", "There is no profile with this id.") from exc
    except NameTakenError as exc:
        raise _error(
            409, "name_taken", "Another profile has this name. Choose a different name."
        ) from exc
    except InvalidProfileError as exc:
        raise _error(500, "internal", "The profile could not be changed.") from exc

    state = await store.state()
    if record != before:
        # What changed, by name; of a key, only that it did.
        what = sorted(
            "key" if name == "api_key_encrypted" else name
            for name in changes
            if name != "key_hint" and getattr(record, name) != getattr(before, name)
        )
        logger.info("LLM profile changed: %s (%s): %s", record.name, record.id, ", ".join(what))
    if state.revision != revision:
        await _apply_now(runtime, record.name, record.id)
    return _saved(record, active=state.active_profile_id == record.id, secret=secret)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: ProfileIdPath, request: Request) -> Response:
    """Delete a saved profile that is not the active one (FR-010)."""
    runtime = _runtime(request)
    if profile_id == ENVIRONMENT_ID:
        raise _error(409, "profile_read_only", READ_ONLY)
    store = runtime.llm_profiles
    record = await store.get_profile(profile_id)
    if record is None:
        raise _error(404, "profile_not_found", "There is no profile with this id.")
    try:
        await store.delete_profile(profile_id)
    except ProfileNotFoundError as exc:
        raise _error(404, "profile_not_found", "There is no profile with this id.") from exc
    except ProfileActiveError as exc:
        raise _error(
            409,
            "profile_active",
            f"Profile {record.name} is the active one and cannot be deleted. "
            "Activate another profile first.",
        ) from exc
    logger.info("LLM profile deleted: %s (%s)", record.name, record.id)
    return Response(status_code=204)


def _stored_key(record: LlmProfileRecord, secret: SecretStr | None) -> str:
    """A saved profile's key, or the refusal that it cannot be used on this instance."""
    reason = unusable_reason(record, secret)
    if reason is not None:
        raise _error(
            409, "profile_unusable", f"Profile {record.name} cannot be used: {UNUSABLE[reason]}."
        )
    return decrypt(record.api_key_encrypted, secret)


async def _check(
    runtime: Runtime, *, profile_id: str | None, spec: ModelSpec | None, secrets: Sequence[str]
) -> CheckResult:
    """Run one test within this instance's limits. ``spec`` None is the environment's model.

    A test beyond a limit is refused before a model is built, so nothing is sent (FR-019b).
    """
    busy = runtime.checks.refusal(profile_id)
    if busy is not None:
        raise _error(409, "test_busy", busy)
    runtime.checks.take(profile_id)
    try:
        try:
            model = runtime.model_to_check(spec)
        except Exception as exc:
            # Values that make no model are a failed test, not a failure of the service.
            reason = redact(f"No model can be built from these values: {exc}", secrets)
            return CheckResult(ok=False, step="first_message", reason=reason[:REASON_LIMIT])
        return await check_model(
            model, limit=runtime.settings.llm_test_timeout_seconds, secrets=secrets
        )
    finally:
        runtime.checks.release(profile_id)


def _log_test(name: str, profile_id: str, result: CheckResult) -> None:
    outcome = "passed" if result.ok else f"failed at {result.step}"
    logger.info("LLM profile tested: %s (%s): %s", name, profile_id, outcome)


@router.post("/profiles/{profile_id}/test")
async def test_profile(profile_id: ProfileIdPath, request: Request) -> LlmTestResult:
    """Test a listed profile, and keep the result with it.

    A failed test is still a 200: the result says which step failed and why. The result is
    not kept when the profile was changed or deleted while the test ran (FR-019a).
    """
    runtime = _runtime(request)
    if profile_id == ENVIRONMENT_ID:
        reason = runtime.environment_profile().unusable_reason
        if reason is not None:
            raise _error(
                409,
                "profile_unusable",
                f"Profile {ENVIRONMENT_NAME} cannot be used: {UNUSABLE[reason]}.",
            )
        result = await _check(
            runtime, profile_id=ENVIRONMENT_ID, spec=None, secrets=runtime.environment_secrets
        )
        at = datetime.now(UTC)
        runtime.environment_test = LastCheck(
            ok=result.ok, at=at, step=result.step, reason=result.reason
        )
        _log_test(ENVIRONMENT_NAME, ENVIRONMENT_ID, result)
        return LlmTestResult(
            ok=result.ok, at=at, step=result.step, reason=result.reason, recorded=True
        )

    store = runtime.llm_profiles
    record = await store.get_profile(profile_id)
    if record is None:
        raise _error(404, "profile_not_found", "There is no profile with this id.")
    key = _stored_key(record, runtime.settings.secret_key)
    spec = ModelSpec(
        provider=record.provider,
        model=record.model,
        base_url=record.base_url,
        api_key=key,
        responses_api=record.responses_api,
    )
    result = await _check(runtime, profile_id=record.id, spec=spec, secrets=[key])
    # Kept only for the values that were tested: the version read before the test began.
    recorded = await store.record_test(
        record.id, record.config_version, ok=result.ok, step=result.step, reason=result.reason
    )
    tested = await store.get_profile(record.id) if recorded else None
    at = tested.last_test_at if tested and tested.last_test_at else datetime.now(UTC)
    _log_test(record.name, record.id, result)
    return LlmTestResult(
        ok=result.ok, at=at, step=result.step, reason=result.reason, recorded=recorded
    )


@router.post("/test")
async def test_draft(body: LlmProfileDraft, request: Request) -> LlmTestResult:
    """Test values that are not saved, for the Add and Edit forms. Nothing is saved or kept.

    With no key, the stored key of ``profile_id`` is used: the Edit form with its key left
    empty (FR-017).
    """
    runtime = _runtime(request)
    if body.api_key is not None:
        key = body.api_key.get_secret_value()
    else:
        record = await runtime.llm_profiles.get_profile(body.profile_id or "")
        if record is None:
            raise _error(404, "profile_not_found", "There is no profile with this id.")
        key = _stored_key(record, runtime.settings.secret_key)
    spec = ModelSpec(
        provider=body.provider,
        model=body.model,
        base_url=body.base_url,
        api_key=key,
        responses_api=body.responses_api,
    )
    result = await _check(runtime, profile_id=None, spec=spec, secrets=[key])
    return LlmTestResult(
        ok=result.ok, at=datetime.now(UTC), step=result.step, reason=result.reason, recorded=False
    )
