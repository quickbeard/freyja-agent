"""What a middleware adds to the system message for one model call, and that the service
imports from Deep Agents' private modules only what it has to.

``append_to_system_message`` was imported from ``deepagents.middleware._utils``, a module the
library does not export and may change in any release. The service has its own now.
"""

import ast
from pathlib import Path

from langchain_core.messages import SystemMessage

import freyja_agent
from freyja_agent.agent.prompts import append_to_system_message

# The private names of Deep Agents the service still imports, each with why. The factory
# builds the general-purpose subagent with the middleware list of ``deepagents.graph``, whose
# last entry the library adds with this function; ``tests/test_subagent.py`` and the
# ``deepagents<0.8`` pin guard that match.
PRIVATE_IMPORTS = {
    (
        "agent/factory.py",
        "deepagents.middleware._prompt_caching",
        "append_prompt_caching_middleware",
    ),
}


def _texts(message: SystemMessage) -> list[str]:
    return [block["text"] for block in message.content_blocks if block["type"] == "text"]


def test_text_added_to_no_system_message_is_the_whole_message() -> None:
    message = append_to_system_message(None, "You are working on thread `t1`.")

    assert isinstance(message, SystemMessage)
    assert _texts(message) == ["You are working on thread `t1`."]
    assert message.text == "You are working on thread `t1`."


def test_text_added_to_a_system_message_follows_it_after_a_blank_line() -> None:
    message = append_to_system_message(SystemMessage(content="You are Freyja."), "One more line.")

    assert _texts(message) == ["You are Freyja.", "\n\nOne more line."]
    assert message.text == "You are Freyja.\n\nOne more line."


def test_the_blocks_that_were_there_are_kept_as_they_are() -> None:
    """A block's cache marker is what the provider's prompt cache is keyed on: it stays where
    it was, and the added text comes after it."""
    marked = {"type": "text", "text": "You are Freyja.", "cache_control": {"type": "ephemeral"}}
    before = SystemMessage(content=[marked, {"type": "text", "text": "Second block."}])

    message = append_to_system_message(before, "Third.")

    assert message.content_blocks == [
        marked,
        {"type": "text", "text": "Second block."},
        {"type": "text", "text": "\n\nThird."},
    ]


def test_text_added_to_an_empty_system_message_is_set_off_by_nothing() -> None:
    assert _texts(append_to_system_message(SystemMessage(content=""), "Only line.")) == [
        "Only line."
    ]


def test_the_message_handed_in_is_not_changed() -> None:
    """Two middlewares add to the same request one after the other; the first one's request
    must not grow when the second adds its text."""
    before = SystemMessage(content="You are Freyja.")

    first = append_to_system_message(before, "First.")
    second = append_to_system_message(first, "Second.")

    assert before.text == "You are Freyja."
    assert _texts(first) == ["You are Freyja.", "\n\nFirst."]
    assert _texts(second) == ["You are Freyja.", "\n\nFirst.", "\n\nSecond."]


def _private_imports() -> set[tuple[str, str, str]]:
    """Every name the service imports from a module of Deep Agents whose path has a part that
    starts with an underscore, as (file under the package, module, name)."""
    package = Path(freyja_agent.__file__).parent
    found: set[tuple[str, str, str]] = set()
    for path in sorted(package.rglob("*.py")):
        file = path.relative_to(package).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [(node.module, alias.name) for alias in node.names]
            elif isinstance(node, ast.Import):
                modules = [(alias.name, "") for alias in node.names]
            else:
                continue
            for module, name in modules:
                parts = module.split(".")
                if parts[0] == "deepagents" and any(
                    part.startswith("_") for part in [*parts[1:], name]
                ):
                    found.add((file, module, name))
    return found


def test_the_service_imports_no_private_name_of_deep_agents_but_the_ones_listed() -> None:
    """A new import from a private module fails here, so that it is a decision and not an
    accident; one that is no longer needed is taken out of the table."""
    assert _private_imports() == PRIVATE_IMPORTS
