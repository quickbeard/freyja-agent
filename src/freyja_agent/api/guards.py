"""What the routers that change operator-managed configuration share.

Two guards, written for the LLM profile routes (feature 004, research R9 and R12) and used by
every router that changes what an operator manages while the service runs (AGENTS.md,
Conventions; feature 005, research R16): a validation error that never repeats the value it
refused, and the refusal of a request a page of another origin made.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)


class NoInputRoute(APIRoute):
    """A route whose validation errors say where and why, and never what was sent.

    FastAPI's own 422 repeats the refused value under ``input``. On the routes that use this
    class that value may be one the service returns nowhere, such as an API key (004 FR-020).
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


# Requests that only read. A page of another origin is not kept from sending one: with no
# permission given, its browser keeps the answer from it (004 SC-012).
READS = frozenset({"GET", "HEAD", "OPTIONS"})
# What a browser says of a request a page made to its own origin, or the user made directly.
OWN_SITE = frozenset({"same-origin", "none"})


def refuse_other_origins(what: str) -> Callable[[Request], None]:
    """The dependency that turns away a changing request made by a page of another origin.

    ``what`` names, in the plural and with a capital, what the router's routes change, such as
    ``"Profiles"``: it opens the last sentence of the refusal.

    A browser asks first before it sends JSON across origins, and is given no permission. It
    does not ask before a form post, which is all the routes without a body need (004
    FR-003a). So what the browser says of the request decides: ``Sec-Fetch-Site`` where it
    sends it, and ``Origin`` against the host the browser asked for, which the web app's
    server passes on as ``X-Forwarded-Host``. A page of another origin can set none of the
    three. A client that is no page, such as curl, sends none of them and is served.
    """

    def refuse(request: Request) -> None:
        if request.method in READS:
            return
        headers = request.headers
        site = headers.get("sec-fetch-site")
        origin = headers.get("origin")
        asked = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0]
        asked = asked.strip().lower()
        other_site = site is not None and site.lower() not in OWN_SITE
        other_origin = origin is not None and (
            not asked or urlsplit(origin).netloc.lower() != asked
        )
        if other_site or other_origin:
            logger.warning(
                "Refused %s %s: the request was made by a page of another origin",
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "other_origin",
                    "message": (
                        "This request was made by a page of another origin and was refused. "
                        f"{what} are changed from the console, or by a client that is not a "
                        "web page."
                    ),
                },
            )

    return refuse
