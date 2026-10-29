from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("live_target_guard_under_test", _ROOT / "tools" / "live_target_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

REMOTE = {
    "OPENPROJECT_BASE_URL": "https://op.example.com",
    "OPENPROJECT_API_TOKEN": "token",
    "OPENPROJECT_TEST_PROJECT": "tst",
    "OPENPROJECT_TEST_DESTRUCTIVE_TARGET": "https://op.example.com",
}
SAFE = {
    "OPENPROJECT_BASE_URL": "http://localhost:8178",
    "OPENPROJECT_API_TOKEN": "token",
    "OPENPROJECT_TEST_PROJECT": "tst",
}


def _env(base: dict[str, str] = SAFE, **changes: str | None) -> dict[str, str]:
    env = {**base}
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def test_a_loopback_target_needs_no_acknowledgement() -> None:
    assert guard.require_destructive_target(SAFE) == guard.DestructiveTarget("http://localhost:8178", "tst")


@pytest.mark.parametrize(
    "base_url", ["http://localhost:8178", "http://127.0.0.1:8178", "http://127.0.0.2", "http://[::1]:8178"]
)
def test_every_loopback_spelling_needs_no_acknowledgement(base_url: str) -> None:
    guard.require_destructive_target(_env(OPENPROJECT_BASE_URL=base_url))


@pytest.mark.parametrize(
    "base_url",
    [
        "https://op.example.com",
        "http://localhost.evil.example",
        "http://127.0.0.1.nip.io",
        "http://10.0.0.5",
        "http://0.0.0.0",
        "http://op.localhost.example.com",
    ],
)
def test_a_target_that_only_looks_local_needs_the_acknowledgement(base_url: str) -> None:
    with pytest.raises(guard.LiveTargetRefused, match="OPENPROJECT_TEST_DESTRUCTIVE_TARGET"):
        guard.require_destructive_target(_env(OPENPROJECT_BASE_URL=base_url))


def test_an_acknowledged_remote_target_is_accepted() -> None:
    assert guard.require_destructive_target(REMOTE) == guard.DestructiveTarget("https://op.example.com", "tst")


@pytest.mark.parametrize("name", ["OPENPROJECT_BASE_URL", "OPENPROJECT_API_TOKEN", "OPENPROJECT_TEST_PROJECT"])
@pytest.mark.parametrize("blank", [None, "", "  "], ids=["missing", "empty", "blank"])
def test_a_missing_variable_is_refused_and_named(name: str, blank: str | None) -> None:
    with pytest.raises(guard.LiveTargetRefused, match=name):
        guard.require_destructive_target(_env(**{name: blank}))


@pytest.mark.parametrize("blank", [None, "", "  "], ids=["missing", "empty", "blank"])
def test_a_remote_target_without_acknowledgement_is_refused(blank: str | None) -> None:
    with pytest.raises(guard.LiveTargetRefused, match="OPENPROJECT_TEST_DESTRUCTIVE_TARGET"):
        guard.require_destructive_target(_env(REMOTE, OPENPROJECT_TEST_DESTRUCTIVE_TARGET=blank))


@pytest.mark.parametrize("project", ["tst", "TST", "mcp-test", "it_sandbox", "Scratch-1", " tst "])
def test_a_disposable_project_name_is_accepted(project: str) -> None:
    assert guard.require_destructive_target(_env(OPENPROJECT_TEST_PROJECT=project)).project == project.strip()


@pytest.mark.parametrize("project", ["OPM", "openproject-ce-mcp", "latest", "contest", "ITA1B2C3D4", "a b"])
def test_a_project_without_a_disposable_token_is_refused(project: str) -> None:
    with pytest.raises(guard.LiveTargetRefused, match="OPENPROJECT_TEST_PROJECT"):
        guard.require_destructive_target(_env(OPENPROJECT_TEST_PROJECT=project))


@pytest.mark.parametrize("project", ["tst,opm", "tst*", "t?t", "[ts]t", "tst opm", "tst/x"])
def test_a_pattern_or_list_is_not_a_project(project: str) -> None:
    with pytest.raises(guard.LiveTargetRefused, match="one project identifier"):
        guard.require_destructive_target(_env(OPENPROJECT_TEST_PROJECT=project))


@pytest.mark.parametrize(
    ("base_url", "acknowledged"),
    [
        ("https://op.example.com/", "https://op.example.com"),
        ("HTTPS://Op.Example.com", "https://op.example.com"),
        ("https://op.example.com", "https://op.example.com:443/"),
        ("http://op.example.com:80", "http://op.example.com"),
        ("https://op.example.com:8443", "https://op.example.com:8443/"),
        ("https://op.example.com/openproject/", "https://OP.example.com/openproject"),
        ("http://[2001:db8::1]:8178", "http://[2001:db8::1]:8178/"),
    ],
)
def test_the_acknowledgement_may_spell_the_same_target_differently(base_url: str, acknowledged: str) -> None:
    guard.require_destructive_target(
        _env(REMOTE, OPENPROJECT_BASE_URL=base_url, OPENPROJECT_TEST_DESTRUCTIVE_TARGET=acknowledged)
    )


@pytest.mark.parametrize(
    ("base_url", "acknowledged"),
    [
        ("https://op.example.com", "https://op.example.com:8443"),
        ("https://op.example.com", "http://op.example.com"),
        ("https://op.example.com/openproject", "https://op.example.com"),
        ("https://op.example.com", "https://op.example.com/openproject"),
        ("https://op.example.com", "https://other.example.com"),
        ("http://[2001:db8::1]:8178", "http://[2001:db8::1]:8179"),
    ],
)
def test_an_acknowledgement_for_another_target_is_refused_without_echoing_the_target(
    base_url: str, acknowledged: str
) -> None:
    env = _env(REMOTE, OPENPROJECT_BASE_URL=base_url, OPENPROJECT_TEST_DESTRUCTIVE_TARGET=acknowledged)
    with pytest.raises(guard.LiveTargetRefused, match="does not name the same target") as refusal:
        guard.require_destructive_target(env)
    assert "openproject" not in str(refusal.value).replace("OPENPROJECT_", "")


@pytest.mark.parametrize(
    ("url", "refusal"),
    [
        ("ftp://op.example.com", "must be an http"),
        ("op.example.com", "must be an http"),
        ("https://", "must be an http"),
        ("https://user:secret@op.example.com", "must not carry"),
        ("https://user@op.example.com", "must not carry"),
        ("https://op.example.com?x=1", "must not carry"),
        ("https://op.example.com?", "must not carry"),
        ("https://op.example.com#top", "must not carry"),
        ("https://op.example.com#", "must not carry"),
        ("https://op.example.com:notaport", "has an invalid port"),
    ],
)
@pytest.mark.parametrize("variable", ["OPENPROJECT_BASE_URL", "OPENPROJECT_TEST_DESTRUCTIVE_TARGET"])
def test_a_malformed_url_is_refused_without_echoing_it(variable: str, url: str, refusal: str) -> None:
    # Both values name https://op.example.com otherwise, so only the malformed part can cause the refusal.
    with pytest.raises(guard.LiveTargetRefused, match=rf"{variable} {refusal}") as raised:
        guard.require_destructive_target(_env(REMOTE, **{variable: url}))
    assert "secret" not in str(raised.value)


def _pytest_integration(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("OPENPROJECT_")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "integration",
            "-p",
            "no:cacheprovider",
            "tests/integration/test_meta.py",
        ],
        cwd=_ROOT,
        env={**clean, "PYTHONDONTWRITEBYTECODE": "1", **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_integration_session_stops_before_any_test_when_the_target_was_not_named() -> None:
    result = _pytest_integration({**SAFE, "OPENPROJECT_TEST_PROJECT": "OPM"})

    assert result.returncode == 2
    assert "Refusing to run the destructive integration suite" in result.stdout + result.stderr
    assert "passed" not in result.stdout


def test_the_integration_session_stops_for_a_remote_target_nobody_acknowledged() -> None:
    result = _pytest_integration(_env(REMOTE, OPENPROJECT_TEST_DESTRUCTIVE_TARGET=None))

    assert result.returncode == 2
    assert "OPENPROJECT_TEST_DESTRUCTIVE_TARGET" in result.stdout + result.stderr


def test_the_integration_session_stops_on_a_partial_configuration() -> None:
    result = _pytest_integration({"OPENPROJECT_BASE_URL": "http://localhost:8178", "OPENPROJECT_API_TOKEN": "token"})

    assert result.returncode == 2
    assert "OPENPROJECT_TEST_PROJECT" in result.stdout + result.stderr


def test_the_integration_session_skips_without_a_live_target() -> None:
    result = _pytest_integration({})

    assert result.returncode == 0
    assert "skipped" in result.stdout
