"""Structural guard: a project scope decision never reads a project's name or a link's title."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from openproject_ce_mcp.app.policies import scope

APP = Path(scope.__file__).resolve().parents[1]
DECIDING_MODULES = sorted((APP / "policies").glob("*.py")) + [APP / "services" / "project_directory_service.py"]
DISPLAY_KEYS = {"name", "title"}


def _display_key_reads(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        if key in DISPLAY_KEYS:
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize("path", DECIDING_MODULES, ids=lambda path: path.name)
def test_no_scope_deciding_module_reads_a_name_or_title(path: Path) -> None:
    assert _display_key_reads(path) == []


@pytest.mark.parametrize(
    "builder",
    [
        scope.project_record_candidates,
        scope.project_payload_candidates,
        scope.project_link_candidates,
        scope.project_known_by_id_allowed,
        scope.project_named_by_link_allowed,
    ],
    ids=lambda builder: builder.__name__,
)
def test_no_candidate_builder_takes_a_name_or_title(builder) -> None:
    assert not {"name", "title"} & set(inspect.signature(builder).parameters)
