"""009: who may call, and the settings of receiving tasks (contracts/settings.md).

Part 1, the settings the service reads from request 1 on: the switch, the two limits, and the
files directory. Each rule of the contract is one test, and no error repeats a value. With the
feature off the rules are not applied: those settings are then not used (contracts/
settings.md, "Off: every other variable here may be set and is not used").
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from freyja_agent.config import Settings

NAMES = (
    "FREYJA_A2A_SERVER",
    "FREYJA_A2A_SERVER_CALLER_TASKS",
    "FREYJA_A2A_SERVER_TASKS",
    "FREYJA_A2A_SERVER_FILES_DIR",
    "FREYJA_A2A_SERVER_WAIT_SECONDS",
)


@pytest.fixture(autouse=True)
def no_feature_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def refused(monkeypatch: pytest.MonkeyPatch, workspace: Path, **values: str) -> str:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as caught:
        Settings(workspace_dir=str(workspace))
    return str(caught.value)


def test_the_feature_is_off_by_default_with_the_contract_s_defaults(workspace: Path) -> None:
    settings = Settings(workspace_dir=str(workspace))
    assert settings.a2a_server is False
    assert settings.a2a_server_caller_tasks == 5
    assert settings.a2a_server_tasks == 20
    assert settings.a2a_server_files_dir == "caller-files"


def test_the_switch_takes_a_boolean_only(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "on-please")
    with pytest.raises(ValidationError, match="a2a_server"):
        Settings(workspace_dir=str(workspace))
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    assert Settings(workspace_dir=str(workspace)).a2a_server is True


@pytest.mark.parametrize("value", ["0", "101", "-3"])
def test_a_caller_s_limit_is_1_to_100(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, value: str
) -> None:
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_CALLER_TASKS=value)
    assert "FREYJA_A2A_SERVER_CALLER_TASKS" in error
    assert f"input_value='{value}'" not in error


@pytest.mark.parametrize(("caller", "total"), [("5", "4"), ("5", "1001")])
def test_the_total_limit_is_at_least_a_caller_s_and_at_most_1000(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, caller: str, total: str
) -> None:
    error = refused(
        monkeypatch,
        workspace,
        FREYJA_A2A_SERVER_CALLER_TASKS=caller,
        FREYJA_A2A_SERVER_TASKS=total,
    )
    assert "FREYJA_A2A_SERVER_TASKS" in error
    assert total not in error.replace("1000", "")


def test_limits_at_their_bounds_are_taken(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLER_TASKS", "100")
    monkeypatch.setenv("FREYJA_A2A_SERVER_TASKS", "1000")
    settings = Settings(workspace_dir=str(workspace))
    assert (settings.a2a_server_caller_tasks, settings.a2a_server_tasks) == (100, 1000)


@pytest.mark.parametrize("value", ["-1", "600.5", "nan"])
def test_a_send_waits_0_to_600_seconds(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, value: str
) -> None:
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_WAIT_SECONDS=value)
    assert "FREYJA_A2A_SERVER_WAIT_SECONDS" in error
    assert f"input_value='{value}'" not in error


def test_a_wait_at_its_bounds_is_taken_and_30_by_default(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    assert Settings(workspace_dir=str(workspace)).a2a_server_wait_seconds == 30
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    for value in ("0", "600"):
        monkeypatch.setenv("FREYJA_A2A_SERVER_WAIT_SECONDS", value)
        assert Settings(workspace_dir=str(workspace)).a2a_server_wait_seconds == float(value)


@pytest.mark.parametrize("where", ["the workspace", "inside it", "around it"])
def test_the_files_directory_is_never_the_workspace_inside_it_or_around_it(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, where: str
) -> None:
    path = {
        "the workspace": workspace,
        "inside it": workspace / "callers",
        "around it": workspace.parent,
    }[where]
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_FILES_DIR=str(path))
    assert "FREYJA_A2A_SERVER_FILES_DIR" in error
    assert str(path) not in error


def test_a_relative_files_directory_is_taken_from_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.chdir(workspace)
    # "." is the workspace itself once it is the working directory.
    assert "FREYJA_A2A_SERVER_FILES_DIR" in refused(
        monkeypatch, workspace, FREYJA_A2A_SERVER_FILES_DIR="."
    )
    # Beside the workspace, which is what the default gives in the service's own directory.
    monkeypatch.chdir(workspace.parent)
    monkeypatch.setenv("FREYJA_A2A_SERVER_FILES_DIR", "caller-files")
    settings = Settings(workspace_dir=str(workspace))
    assert settings.a2a_server and settings.a2a_server_files_dir == "caller-files"


def test_with_the_feature_off_the_rules_are_not_applied(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLER_TASKS", "0")
    monkeypatch.setenv("FREYJA_A2A_SERVER_TASKS", "-1")
    monkeypatch.setenv("FREYJA_A2A_SERVER_FILES_DIR", str(workspace))
    settings = Settings(workspace_dir=str(workspace))
    assert settings.a2a_server is False
