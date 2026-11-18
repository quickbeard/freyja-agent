"""The model one service instance runs on (feature 004, research R1, R6).

The active profile is one choice for every instance on a database. Each instance builds its
orchestrator for that choice at startup and remembers the revision it built for. Before every
run it reads the current revision, which is one single-row read, and rebuilds when it moved:
that is how a change made through any instance reaches this one without a restart (FR-013,
FR-014).

A rebuild goes through the same factory as startup and replaces the orchestrator as a whole. A
run binds its orchestrator when it starts, so a run in progress keeps the one it started on.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from freyja_agent.agent.factory import Orchestrator
from freyja_agent.llm.guard import guard_model
from freyja_agent.llm.providers import (
    ENVIRONMENT_ID,
    ENVIRONMENT_NAME,
    ModelFactory,
    ModelSpec,
    UnusableChatModel,
)
from freyja_agent.llm.secrets import KeyUnreadableError, SecretNotConfiguredError, decrypt
from freyja_agent.llm.store import LlmProfileRecord, LlmProfileStore, LlmState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunProfile:
    """The profile a run uses, as its first event and its log line name it (FR-024a)."""

    id: str
    name: str
    source: Literal["saved", "environment"]


ENVIRONMENT_PROFILE = RunProfile(ENVIRONMENT_ID, ENVIRONMENT_NAME, "environment")

# Why a profile cannot be used, in words, by the code the list reports (FR-011a).
UNUSABLE = {
    "key_unreadable": "its stored key cannot be read with the service's current secret",
    "secret_not_configured": "no secret is set on the service, so its stored key cannot be read",
    "not_configured": "the environment names no key for its provider",
    "cannot_build": "no model can be built from its values",
}


def unusable_reason(record: LlmProfileRecord, secret: SecretStr | None) -> str | None:
    """Why a saved profile cannot be used on this instance, as a code, or None (FR-011a)."""
    try:
        decrypt(record.api_key_encrypted, secret)
    except SecretNotConfiguredError:
        return "secret_not_configured"
    except KeyUnreadableError:
        return "key_unreadable"
    return None


@dataclass(frozen=True)
class ActiveModel:
    """What this instance built its orchestrator for (data-model.md "Model in use")."""

    # The revision of the active choice this instance last applied.
    revision: int
    profile: RunProfile
    usable: bool = True
    # Why not: one of the ``UNUSABLE`` codes, and its sentence, which holds no key. None when
    # usable.
    why: str | None = None
    reason: str | None = None
    # The keys that text about this model is cleaned of (FR-021). Never shown.
    secrets: tuple[str, ...] = field(default=(), repr=False)


class ModelManager:
    """Keeps one instance's orchestrator in step with the active profile.

    ``environment`` builds the environment's model and ``factory`` a saved profile's; ``build``
    assembles the orchestrator around a model, with everything else as it was at startup.
    """

    def __init__(
        self,
        *,
        store: LlmProfileStore,
        secret: SecretStr | None,
        factory: ModelFactory,
        environment: Callable[[], BaseChatModel],
        build: Callable[[BaseChatModel], Orchestrator],
        environment_secrets: tuple[str, ...] = (),
        environment_unusable: str | None = None,
    ) -> None:
        self._store = store
        self._secret = secret
        self._factory = factory
        self._environment = environment
        self._build = build
        self._environment_secrets = environment_secrets
        # Why the environment's profile cannot be used before a model is even built: its
        # provider's key is not set (FR-011a). None when nothing is known against it.
        self._environment_unusable = environment_unusable
        # Two runs that start together rebuild once, not twice.
        self._lock = asyncio.Lock()
        self.active = ActiveModel(revision=-1, profile=ENVIRONMENT_PROFILE)
        self.orchestrator: Orchestrator | None = None

    async def load(self) -> Orchestrator:
        """Build the orchestrator for the active profile, at startup."""
        async with self._lock:
            return await self._apply(await self._store.state())

    async def current(self) -> tuple[Orchestrator, ActiveModel]:
        """The orchestrator for the active profile as it is now, rebuilt if the choice moved."""
        state = await self._store.state()
        if state.revision != self.active.revision or self.orchestrator is None:
            async with self._lock:
                # Another run may have rebuilt while this one waited for the lock.
                state = await self._store.state()
                if state.revision != self.active.revision or self.orchestrator is None:
                    await self._apply(state)
        if self.orchestrator is None:  # _apply either sets it or raises
            raise RuntimeError("the orchestrator was not built")
        return self.orchestrator, self.active

    async def _apply(self, state: LlmState) -> Orchestrator:
        """Build the orchestrator for the active profile, usable or not (FR-016, research R8).

        A profile that cannot be turned into a model does not stop the service: the reason is
        recorded, and the orchestrator is built around a placeholder so that the graph, the
        catalog, and the thread routes exist. Runs are turned away before the placeholder is
        asked anything. Nothing here falls back to another profile.
        """
        model: BaseChatModel | None = None
        secrets: tuple[str, ...] = ()
        if state.active_profile_id is None:
            profile = ENVIRONMENT_PROFILE
            secrets = self._environment_secrets
            why = None
            # Built as it always was, key or no key, so that an environment-only deployment
            # takes the path it took before this feature (FR-023). Whether the result may be
            # used is decided after: a provider of the three with no key is not usable,
            # whatever its client made of that (FR-011a).
            try:
                model = self._environment()
            except Exception:
                why = "cannot_build"
            if self._environment_unusable is not None:
                why, model = self._environment_unusable, None
        else:
            record = await self._store.get_profile(state.active_profile_id)
            if record is None:
                # The database refuses to delete the active profile, so this is a store that
                # broke its own rule, not a state to recover from.
                raise RuntimeError("the active LLM profile no longer exists")
            profile = RunProfile(record.id, record.name, "saved")
            why = unusable_reason(record, self._secret)
            if why is None:
                key = decrypt(record.api_key_encrypted, self._secret)
                secrets = (key,)
                try:
                    model = self._factory(
                        ModelSpec(
                            provider=record.provider,
                            model=record.model,
                            base_url=record.base_url,
                            api_key=key,
                            responses_api=record.responses_api,
                        )
                    )
                except Exception:
                    why = "cannot_build"
        reason = UNUSABLE[why] if why is not None else None
        if model is None:
            model = UnusableChatModel(reason=f"profile {profile.name} cannot be used: {reason}")
        else:
            # A provider may repeat the key in an error, and LangGraph shows a failed step's
            # error in the debug stream and saves it with the thread (FR-020, FR-021).
            model = guard_model(model, secrets)
        orchestrator = self._build(model)
        if self.orchestrator is None:
            pass  # startup: the runtime logs every profile that is not usable, this one too
        elif reason is not None:
            logger.warning(
                "The active LLM profile %s (%s) is not usable: %s. Runs end with "
                "no_usable_model until a usable profile is active.",
                profile.name,
                profile.id,
                reason,
            )
        else:
            logger.info(
                "LLM profile in use is now %s (%s, %s)", profile.name, profile.id, profile.source
            )
        self.orchestrator = orchestrator
        self.active = ActiveModel(
            revision=state.revision,
            profile=profile,
            usable=reason is None,
            why=why,
            reason=reason,
            secrets=secrets,
        )
        return orchestrator
