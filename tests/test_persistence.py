"""What every Postgres store uses of ``runtime/persistence.py`` that needs no database."""

from typing import Any

import pytest

from freyja_agent.runtime.persistence import one_row


class _Cursor:
    """A cursor that has the rows it is given, as psycopg's hands them out."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows.pop(0) if self._rows else None


async def test_the_row_of_a_statement_that_returns_one_is_handed_on() -> None:
    cursor: Any = _Cursor([{"count": 3}])

    assert await one_row(cursor) == {"count": 3}


async def test_a_statement_that_should_return_a_row_and_returns_none_is_an_error() -> None:
    """Said where it is found, in the store's words, and not as a ``TypeError`` at the first
    use of a row that is not there."""
    cursor: Any = _Cursor([])

    with pytest.raises(RuntimeError, match="returns one row returned none"):
        await one_row(cursor)
