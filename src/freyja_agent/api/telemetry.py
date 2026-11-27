"""A span and a duration for every request the service answers (feature 011, research R6).

``RequestTelemetry`` is a plain ASGI middleware, added outside everything else on both
applications: the service's own, and the one other agents call (feature 009). It tells
telemetry of a request three things and nothing a client wrote: the method, the route as
the service declares it, and the status code. The path as it was written, the query, the
headers, the body, the host name, and the client's address are never read, and a trace's
id that a client sent is not used. Nothing that is sent back is changed.

A request to a route that says whether the service is up or ready is not recorded: a probe
asks every few seconds, and would be most of what a collector holds.

While a request is served it is what a run started inside belongs under
(``runtime/telemetry.py``), which is how a run's span comes to stand under its request's.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from freyja_agent.runtime.telemetry import SERVICE_LISTENER, Telemetry

# The routes that say whether the service is up or ready, by their paths.
PROBES = frozenset({"/healthz", "/readyz"})


def declared_route(scope: Scope) -> str | None:
    """The path, as the application declares it, of the route that answered; None when the
    request reached none.

    The router writes what it matched into the scope: the route itself for a route of
    FastAPI's, and for any other route, such as the A2A ones, its endpoint, by which the
    route is found among the application's. A request with a method its route does not take
    was matched by its path, and is named by that route.
    """
    path = getattr(scope.get("route"), "path", None)
    if path is not None:
        return path
    endpoint = scope.get("endpoint")
    if endpoint is None:
        return None
    for route in getattr(scope.get("app"), "routes", ()):
        if getattr(route, "endpoint", None) is endpoint:
            return getattr(route, "path", None)
    return None


class RequestTelemetry:
    """Record each request of the application it wraps.

    ``telemetry`` is what records; given none, it is the telemetry of the runtime the
    application's lifespan opened, and a request that arrives before there is one is
    passed on unrecorded. ``listener`` says which of the two addresses the application is.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        listener: str = SERVICE_LISTENER,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.app = app
        self.listener = listener
        self.telemetry = telemetry

    def _telemetry(self, scope: Scope) -> Telemetry | None:
        if self.telemetry is not None:
            return self.telemetry
        state = getattr(scope.get("app"), "state", None)
        runtime = getattr(state, "runtime", None)
        return getattr(runtime, "telemetry", None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        telemetry = None
        if scope["type"] == "http" and scope["path"] not in PROBES:
            telemetry = self._telemetry(scope)
        if telemetry is None:
            await self.app(scope, receive, send)
            return
        with telemetry.request(scope["method"], self.listener) as request:

            async def noting(message: Message) -> None:
                if message["type"] == "http.response.start":
                    request.status = message["status"]
                await send(message)

            try:
                await self.app(scope, receive, noting)
            finally:
                # Known only now: the router matched while the application ran. None when it
                # matched nothing or the request was refused before it was asked.
                request.route = declared_route(scope)
