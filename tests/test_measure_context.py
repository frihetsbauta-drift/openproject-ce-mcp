from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "measure-context.py"
_spec = importlib.util.spec_from_file_location("measure_context", _PATH)
measure_context = importlib.util.module_from_spec(_spec)
sys.modules["measure_context"] = measure_context
_spec.loader.exec_module(measure_context)


@pytest.fixture(autouse=True)
def _offline_token_count(monkeypatch):
    # The real counter needs the optional tiktoken extra and its network-fetched
    # encoding; a byte-based stand-in keeps every comparison meaningful.
    monkeypatch.setattr(measure_context, "_tokens", lambda raw: len(raw.encode()) // 4)


def _catalog_lines(out: str) -> dict[str, int]:
    return {m["label"]: int(m["count"]) for m in re.finditer(r"^(?P<label>[^:\n]+): (?P<count>\d+) tools,", out, re.M)}


def test_catalog_profiles_widen_from_fresh_install_to_every_scope(capsys):
    asyncio.run(measure_context.measure_tools_list())
    counts = list(_catalog_lines(capsys.readouterr().out).values())

    assert len(counts) == 6
    assert counts == sorted(set(counts))


def test_catalog_never_duplicates_the_server_instructions_into_tool_descriptions(capsys):
    asyncio.run(measure_context.measure_tools_list())
    out = capsys.readouterr().out

    assert "server.instructions is CE_INSTRUCTIONS: True" in out
    assert "tools whose description duplicates it: none" in out


def test_export_writes_the_wire_form_of_the_glama_and_full_catalogs(tmp_path):
    asyncio.run(measure_context.export_catalogs(tmp_path))

    glama = json.loads((tmp_path / "tools_glama.json").read_text())["tools"]
    full = json.loads((tmp_path / "tools_full.json").read_text())["tools"]
    assert {tool["name"] for tool in glama} < {tool["name"] for tool in full}
    assert all("inputSchema" in tool and "input_schema" not in tool for tool in glama + full)


def test_report_states_the_saving_against_the_raw_payload(capsys):
    measure_context._report("get_work_package", "x" * 400, "x" * 100)
    out = capsys.readouterr().out

    assert "Raw: 400 bytes, ~100 tokens" in out
    assert "MCP: 100 bytes, ~25 tokens (-75% vs. raw)" in out


@dataclass
class _Row:
    id: int
    subject: str
    responsible: str | None


def test_null_cost_counts_rows_with_the_selected_field_unset(capsys):
    rows = [_Row(1, "a", None), _Row(2, "b", "Jane"), _Row(3, "c", None)]
    measure_context.measure_null_distinguishability_cost(rows)
    out = capsys.readouterr().out

    assert "(3 rows, 2 with responsible=None)" in out
    elided, explicit = (int(n) for n in re.findall(r"~(\d+) tokens", out)[:2])
    assert explicit >= elided


def test_response_measurement_is_skipped_without_a_live_instance(monkeypatch, capsys):
    for name in ("OPENPROJECT_BASE_URL", "OPENPROJECT_API_TOKEN", "OPENPROJECT_TEST_PROJECT"):
        monkeypatch.delenv(name, raising=False)

    assert asyncio.run(measure_context.measure_response_sizes()) is None


async def test_raw_baseline_waits_as_long_as_the_operator_configured(monkeypatch):
    monkeypatch.setenv("OPENPROJECT_TIMEOUT", "37")
    settings = measure_context._measurement_settings("https://op.example.com", "token", "TST")

    async with measure_context._raw_api(settings) as http:
        assert (settings.timeout, http.timeout) == (37.0, httpx.Timeout(37.0))


@pytest.mark.parametrize("project", ["OPM", "tst"], ids=["production-like project", "remote target not acknowledged"])
def test_live_part_refuses_before_any_request_when_the_target_was_not_named(monkeypatch, project):
    monkeypatch.setenv("OPENPROJECT_BASE_URL", "https://op.example.com")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "token")
    monkeypatch.setenv("OPENPROJECT_TEST_PROJECT", project)
    monkeypatch.delenv("OPENPROJECT_TEST_DESTRUCTIVE_TARGET", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("a refused run must not build a client")

    monkeypatch.setattr(measure_context, "OpenProjectClient", forbidden)
    monkeypatch.setattr(measure_context, "_raw_api", forbidden)

    with pytest.raises(SystemExit) as refusal:
        asyncio.run(measure_context.measure_response_sizes())

    assert "Refusing to write measurement work packages" in str(refusal.value)
    assert "token" not in str(refusal.value)


def test_live_part_is_skipped_without_a_live_target(monkeypatch, capsys):
    monkeypatch.delenv("OPENPROJECT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENPROJECT_API_TOKEN", raising=False)

    assert asyncio.run(measure_context.measure_response_sizes()) is None
    assert "Skipped" in capsys.readouterr().out
