"""Cleanup helpers for the live integration suite.

A cleanup that fails leaves data on the instance, so it must say so. ``delete_all`` keeps going after a failure,
tolerates only a resource that is already gone, and reports everything else at the end. ``ClientRegistry`` closes
every client a test opened, including one whose ``initialize`` failed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol, TypeVar

from openproject_ce_mcp.client import NotFoundError, OpenProjectClient

T = TypeVar("T")


class LiveCleanupFailed(AssertionError):
    pass


class _Closable(Protocol):
    async def initialize(self) -> Any: ...

    async def aclose(self) -> None: ...


async def delete_all(label: str, items: Iterable[T], delete: Callable[[T], Awaitable[object]]) -> None:
    leaked = []
    for item in items:
        try:
            await delete(item)
        except NotFoundError:
            continue
        except Exception as exc:
            leaked.append(f"{label} {item!r}: {type(exc).__name__}: {exc}")
    if leaked:
        raise LiveCleanupFailed("Cleanup left data behind:\n" + "\n".join(leaked))


class ClientRegistry:
    def __init__(self, factory: Callable[[Any], _Closable] = OpenProjectClient) -> None:
        self._factory = factory
        self._clients: list[_Closable] = []

    async def open(self, settings: Any) -> Any:
        client = self._factory(settings)
        self._clients.append(client)
        await client.initialize()
        return client

    async def close(self) -> None:
        failures = []
        while self._clients:
            client = self._clients.pop()
            try:
                await client.aclose()
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
        if failures:
            raise LiveCleanupFailed("A live client did not close:\n" + "\n".join(failures))
