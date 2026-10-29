"""Refuses a destructive live run unless the target was named on purpose.

The live integration suite and the context benchmark create and delete data with
every write flag on. A run needs, all at once:

1. ``OPENPROJECT_BASE_URL``, ``OPENPROJECT_API_TOKEN`` and ``OPENPROJECT_TEST_PROJECT``, with no default.
2. A single project identifier as the test project, never a pattern or a list, and one whose name carries a
   disposable token (``test``, ``tst``, ...). The name only catches a pasted production identifier; it proves nothing.
3. For a target that is not loopback, ``OPENPROJECT_TEST_DESTRUCTIVE_TARGET`` naming the same canonical base URL as
   ``OPENPROJECT_BASE_URL``. The value is the acknowledgement: a copy meant for another instance, scheme or path
   prefix does not pass. A loopback target is the local Docker harness and needs no acknowledgement.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import NamedTuple
from urllib.parse import urlsplit

BASE_URL = "OPENPROJECT_BASE_URL"
API_TOKEN = "OPENPROJECT_API_TOKEN"
TEST_PROJECT = "OPENPROJECT_TEST_PROJECT"
DESTRUCTIVE_TARGET = "OPENPROJECT_TEST_DESTRUCTIVE_TARGET"

DISPOSABLE_TOKENS = frozenset({"test", "tst", "tmp", "sandbox", "playground", "scratch", "disposable"})

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]+")
_DEFAULT_PORTS = {"http": 80, "https": 443}


class LiveTargetRefused(RuntimeError):
    pass


class DestructiveTarget(NamedTuple):
    base_url: str
    project: str


def canonical_base_url(raw: str, *, variable: str) -> str:
    parts = urlsplit(raw.strip())
    try:
        explicit_port = parts.port
    except ValueError:
        raise LiveTargetRefused(f"{variable} has an invalid port.") from None
    if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.hostname:
        raise LiveTargetRefused(f"{variable} must be an http(s) URL with a host.")
    if parts.username is not None or parts.password is not None or "?" in raw or "#" in raw:
        raise LiveTargetRefused(f"{variable} must not carry credentials, a query or a fragment.")
    scheme = parts.scheme.lower()
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{explicit_port}" if explicit_port not in (None, _DEFAULT_PORTS[scheme]) else ""
    return f"{scheme}://{host}{port}{parts.path.rstrip('/')}"


def _is_loopback(base_url: str) -> bool:
    host = urlsplit(base_url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_destructive_target(env: Mapping[str, str]) -> DestructiveTarget:
    values = {name: env.get(name, "").strip() for name in (BASE_URL, API_TOKEN, TEST_PROJECT, DESTRUCTIVE_TARGET)}
    missing = [name for name in (BASE_URL, API_TOKEN, TEST_PROJECT) if not values[name]]
    if missing:
        raise LiveTargetRefused(f"A destructive live run needs {', '.join(missing)}; none of them has a default.")

    project = values[TEST_PROJECT]
    if not _IDENTIFIER.fullmatch(project):
        raise LiveTargetRefused(f"{TEST_PROJECT} must be one project identifier, not a pattern or a list: {project!r}.")
    if not DISPOSABLE_TOKENS & set(re.split(r"[-_]", project.casefold())):
        raise LiveTargetRefused(
            f"{TEST_PROJECT}={project!r} does not look disposable; its name needs one of "
            f"{', '.join(sorted(DISPOSABLE_TOKENS))} between '-' or '_'."
        )

    base_url = canonical_base_url(values[BASE_URL], variable=BASE_URL)
    if not _is_loopback(base_url):
        if not values[DESTRUCTIVE_TARGET]:
            raise LiveTargetRefused(
                f"{BASE_URL} is not a loopback address; set {DESTRUCTIVE_TARGET} to the same URL to confirm that "
                "this instance may be written to."
            )
        if canonical_base_url(values[DESTRUCTIVE_TARGET], variable=DESTRUCTIVE_TARGET) != base_url:
            raise LiveTargetRefused(f"{DESTRUCTIVE_TARGET} does not name the same target as {BASE_URL}.")
    return DestructiveTarget(base_url, project)
