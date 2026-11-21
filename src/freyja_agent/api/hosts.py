"""The refusal of a request made for a host name the service does not answer for.

A web page is kept from the service by its origin: a page of another origin cannot read an
answer, and ``refuse_other_origins`` turns away what it would change. Both rest on the page's
origin being another one. A page under a name its author controls can have that name point at
this machine once the page is loaded (DNS rebinding). To the browser the page and the service
are then one origin: the page reads every answer, and each header the other refusal looks at
says the request is the console's own.

What such a request cannot hide is the name it was made for, which the browser writes in
``Host`` and no page can set. So every request is asked which name it was made for, and one
made for a name the deployment does not list is refused before any route sees it, a request
that only reads included. The console's server forwards a page's request with ``Host`` naming
the service and the name the browser asked for in ``X-Forwarded-Host``, so both are held to
the list. A page that reaches the service directly can write ``X-Forwarded-Host`` itself and
cannot write ``Host``; one that goes through the console can write neither, since the
console's server replaces what a page wrote with the name it was itself asked under
(``web/e2e/stream.spec.ts`` holds it to that).

A proxy in front of the console or of the service has to pass on the ``Host`` the browser
sent. One that replaces it with an address hides the name, and every name is then served.

Allowed without being listed: ``localhost``, and every IP address written as one. No name is
looked up for an address, so a page cannot make one point elsewhere. Every other name is
listed in ``FREYJA_ALLOWED_HOSTS``.
"""

import ipaddress
import logging
from collections.abc import Iterable, Iterator

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Answered whatever name it was asked under: it says that the process is up and nothing
# else, and what checks it may know the service under a name of the deployment's network.
OPEN_PATHS = frozenset({"/healthz"})

MESSAGE = (
    "This request was made for a host name that this service is not set up to answer for, "
    "and was refused. When the console or the service is reached under a name other than "
    "localhost or an IP address, that name is listed in FREYJA_ALLOWED_HOSTS."
)

# How much of a refused name, and of the path it was asked for, the log line shows.
SHOWN = 80


def host_of(value: str) -> str | None:
    """The host a ``Host`` value names, without its port, or None when it names none.

    An IPv6 address keeps its brackets, so that it is never taken for a name. A name is
    lowercased and loses the dot a fully qualified name may end with.
    """
    value = value.strip()
    if value.startswith("["):
        host, bracket, rest = value.partition("]")
        port = rest.removeprefix(":")
        if not bracket or (rest and (not rest.startswith(":") or not _is_port(port))):
            return None
        return f"{host}]".lower()
    host, colon, port = value.partition(":")
    if colon and not _is_port(port):
        return None
    host = host.removesuffix(".").lower()
    return host or None


def _is_port(text: str) -> bool:
    return text.isascii() and text.isdigit()


def is_address(host: str) -> bool:
    """True when ``host``, as ``host_of`` gives it, is an IP address and not a name."""
    try:
        if host.startswith("["):
            if "%" in host:
                # A zone id is free text, and no browser sends one.
                return False
            ipaddress.IPv6Address(host[1:-1])
        else:
            ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return True


class AllowedHosts:
    """ASGI middleware: answers 403 ``unknown_host`` to a request made for an unlisted name.

    It is plain ASGI and touches no message, so a run's stream passes through it as it is
    produced.
    """

    def __init__(self, app: ASGIApp, names: Iterable[str] = ()) -> None:
        self.app = app
        self.names = frozenset(names) | {"localhost"}

    def answers_for(self, value: str) -> bool:
        """Whether the service answers a request made for what ``value`` names."""
        host = host_of(value)
        return host is not None and (host in self.names or is_address(host))

    def _refused(self, scope: Scope) -> str | None:
        """The value the request is refused for, or None when it is served."""
        hosts = list(_values(scope, b"host"))
        if not hosts:
            return ""
        forwarded = [
            part
            for value in _values(scope, b"x-forwarded-host")
            for part in value.split(",")
            if part.strip()
        ]
        for value in (*hosts, *forwarded):
            if not self.answers_for(value):
                return value
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or scope["path"] in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        refused = self._refused(scope)
        if refused is None:
            await self.app(scope, receive, send)
            return
        # Both are what the client sent, the path with its escapes already undone: quoted
        # and cut, so that neither can write a line of its own into the log.
        logger.warning(
            "Refused %s %r: it was made for a host name that FREYJA_ALLOWED_HOSTS does not "
            "list: %r",
            scope.get("method", "a connection to"),
            scope["path"][:SHOWN],
            refused.strip()[:SHOWN],
        )
        response = JSONResponse(
            status_code=403, content={"detail": {"code": "unknown_host", "message": MESSAGE}}
        )
        await response(scope, receive, send)


def _values(scope: Scope, name: bytes) -> Iterator[str]:
    for key, value in scope["headers"]:
        if key.lower() == name:
            yield value.decode("latin-1")
