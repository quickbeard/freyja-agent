"""The two guards the routers of operator-managed configuration share (005 T001).

Their behavior on the LLM routes is covered where it was, in ``tests/test_llm_profiles_api.py``.
These pin what moving them into a module of their own must not change, and the one thing it
adds: the refusal names what the router changes.
"""

from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from freyja_agent.api import llm
from freyja_agent.api.guards import NoInputRoute, refuse_other_origins

OTHER = {"Origin": "http://other.example"}


class Body(BaseModel):
    value: int


def client_for(what: str) -> TestClient:
    router = APIRouter(route_class=NoInputRoute, dependencies=[Depends(refuse_other_origins(what))])

    @router.get("/things")
    def read() -> dict[str, Any]:
        return {"things": []}

    @router.post("/things")
    def change(body: Body) -> dict[str, Any]:
        return {"value": body.value}

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_refusal_for_profiles_is_worded_as_it_always_was() -> None:
    response = client_for("Profiles").post("/things", json={"value": 1}, headers=OTHER)
    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "other_origin",
            "message": (
                "This request was made by a page of another origin and was refused. Profiles "
                "are changed from the console, or by a client that is not a web page."
            ),
        }
    }


def test_the_refusal_names_what_the_router_changes() -> None:
    response = client_for("Agents").post("/things", json={"value": 1}, headers=OTHER)
    assert response.json()["detail"]["message"] == (
        "This request was made by a page of another origin and was refused. Agents are "
        "changed from the console, or by a client that is not a web page."
    )


@pytest.mark.parametrize(
    "headers",
    [OTHER, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}],
    ids=["origin", "cross-site", "same-site"],
)
def test_a_change_from_another_origin_is_refused_and_a_read_is_not(headers: dict[str, str]) -> None:
    client = client_for("Things")
    assert client.post("/things", json={"value": 1}, headers=headers).status_code == 403
    assert client.get("/things", headers=headers).status_code == 200


def test_a_client_that_is_no_page_and_a_page_of_this_origin_are_served() -> None:
    client = client_for("Things")
    assert client.post("/things", json={"value": 1}).status_code == 200
    own = {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}
    assert client.post("/things", json={"value": 1}, headers=own).status_code == 200


def test_a_refused_value_is_not_repeated() -> None:
    response = client_for("Things").post("/things", json={"value": "a-secret-value"})
    assert response.status_code == 422
    assert "a-secret-value" not in response.text
    (error,) = response.json()["detail"]
    assert set(error) == {"type", "loc", "msg"}


def test_the_llm_routes_still_offer_the_route_class() -> None:
    """``tests/test_llm_profiles_api.py`` imports it from there."""
    assert llm.NoInputRoute is NoInputRoute
