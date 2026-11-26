"""Who is calling over A2A (feature 009, research R5 and R15).

``Callers`` is built from ``FREYJA_A2A_SERVER_CALLERS``. It turns a request's
``Authorization`` header into a caller's name: at the service's own address no header is the
local caller, and at the outside listener every request needs a named caller's token. A
presented token is compared with every caller's, by ``hmac.compare_digest`` on the bytes of
both and without stopping at a match, so the time a comparison takes says nothing of how
much of a token was right (FR-005).

A caller's token is taken out of every log record and of what a caller sends, by its exact
text (``delegates/addresses.py``, ``TOKENS``), and refused credentials are warned of
sparingly: once, then at most once in ten seconds for each of the service's two addresses,
with how many were refused since (FR-028).

Nothing here imports the protocol's SDK.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from freyja_agent.config import LOCAL_CALLER, A2aCallerConfig
from freyja_agent.delegates.addresses import TOKENS

logger = logging.getLogger(__name__)

# The least time between two warnings of refused credentials at one address (FR-028).
WARN_SECONDS = 10
OWN = "the service's own address"
OUTSIDE = "the outside listener"
# Why a request was refused, as a warning says it of one request and of several.
_REASONS = {
    "credential": ("for its credential", "for their credential"),
    "origin": (
        "as made by a page of another origin",
        "as made by pages of another origin",
    ),
}


@dataclass
class _Refusals:
    """Refused credentials at one address since its last warning."""

    since: int = 0
    last: float | None = None


class Callers:
    """The callers a deployment names, and who a request is from."""

    def __init__(
        self,
        configured: Mapping[str, A2aCallerConfig],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tokens = [
            (name, caller.token.get_secret_value().encode("ascii"))
            for name, caller in configured.items()
        ]
        self.names = frozenset(configured)
        # The clock the warnings are spaced by; the suite moves its own.
        self.clock = clock
        self._refused: dict[tuple[bool, str], _Refusals] = {}
        TOKENS.set(caller.token.get_secret_value() for caller in configured.values())

    def who(self, authorization: str | None, *, outside: bool) -> str | None:
        """The caller a request is from by its ``Authorization`` header; None when it is
        refused. No header is the local caller at the service's own address, and refused
        at the outside listener; another scheme than ``Bearer``, or a token no caller has,
        is refused at both."""
        if authorization is None:
            return None if outside else LOCAL_CALLER
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return None
        given = presented.strip().encode("utf-8", "replace")
        found: str | None = None
        for name, token in self._tokens:
            # Every caller's is compared, also after a match.
            if hmac.compare_digest(given, token) and found is None:
                found = name
        return found

    def configured(self, name: str) -> bool:
        """Whether the deployment names ``name`` now; the local caller always is."""
        return name == LOCAL_CALLER or name in self.names

    @staticmethod
    def scrub(text: str) -> str:
        """``text`` without any named caller's token."""
        return TOKENS.scrub(text)

    @staticmethod
    def forget() -> None:
        """The service stops: its callers' tokens are no longer looked for."""
        TOKENS.set(())

    def refused(self, *, outside: bool, why: str = "credential") -> None:
        """Count a request refused at one of the two addresses, for its credential or as a
        page of another origin's (``why``), and warn of the first, then at most once in
        ``WARN_SECONDS`` with how many were refused since (FR-028)."""
        counted = self._refused.setdefault((outside, why), _Refusals())
        where = OUTSIDE if outside else OWN
        one, many = _REASONS[why]
        now = self.clock()
        counted.since += 1
        if counted.last is None:
            logger.warning(
                "A request at %s was refused %s; further refusals there are counted and said "
                "at most once in %d seconds",
                where,
                one,
                WARN_SECONDS,
            )
        elif now - counted.last >= WARN_SECONDS:
            logger.warning(
                "%d requests at %s were refused %s since the last warning",
                counted.since,
                where,
                many,
            )
        else:
            return
        counted.last, counted.since = now, 0
