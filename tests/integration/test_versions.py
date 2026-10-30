"""Integration tests for version CRUD operations."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from openproject_ce_mcp.client import OpenProjectClient, PermissionDeniedError

from .conftest import disposable_project_identifier

pytestmark = pytest.mark.integration


async def test_list_versions(client: OpenProjectClient, test_project: str, version_ids: list[int]) -> None:
    name = f"[integration-test-list] {uuid.uuid4().hex[:8]}"
    create_result = await client.version.create(project=test_project, name=name, confirm=True)
    assert create_result.ready, create_result.validation_errors
    version_ids.append(create_result.version_id)

    result = await client.version.list(project=test_project)
    assert result is not None
    assert result.count > 0
    assert any(v.name == name for v in result.results)


async def test_list_versions_with_search(client: OpenProjectClient, test_project: str, version_ids: list[int]) -> None:
    name = f"[integration-test-search] {uuid.uuid4().hex[:8]}"
    result = await client.version.create(project=test_project, name=name, confirm=True)
    assert result.ready, result.validation_errors
    version_id = result.version_id
    version_ids.append(version_id)

    found = await client.version.list(project=test_project, search=name)
    assert [v.id for v in found.results] == [version_id]

    no_match = await client.version.list(project=test_project, search=uuid.uuid4().hex)
    assert no_match.results == []


async def test_list_versions_search_walks_every_server_page(
    client: OpenProjectClient, test_project: str, version_ids: list[int], open_client
) -> None:
    """Regression: list_versions' search branch fetched a single server page
    capped at settings.max_page_size and filtered client-side, on the
    assumption that real result counts never exceed one page -- any
    matching version beyond that first page was silently unreachable.

    settings.max_page_size doubles as BOTH the server page size AND the
    final result-slicing limit (_resolve_limit clamps to it), so shrinking
    it also shrinks limit= -- a single list_versions() call can therefore
    never observe more than max_page_size results regardless of the fix.
    Instead, call list_versions() itself multiple times (its own offset=
    pagination, walking RESULT pages, not server pages) with a tiny
    max_page_size, and take the union: every one of the few created
    versions must show up across those calls, proving _fetch_all_pages
    (invoked fresh on every one of these calls) doesn't lose any of them to
    a single-server-page cap on its own end.
    """
    shared_marker = uuid.uuid4().hex[:8]
    created_ids = []
    for i in range(3):
        name = f"[integration-test-search] {shared_marker} {i}"
        result = await client.version.create(project=test_project, name=name, confirm=True)
        assert result.ready, result.validation_errors
        created_ids.append(result.version_id)
        version_ids.append(result.version_id)

    tiny_page_settings = dataclasses.replace(client.settings, max_page_size=1)
    tiny_page_client = await open_client(tiny_page_settings)

    found_ids: set[int] = set()
    result_offset = 1
    for _ in range(len(created_ids) + 2):  # bounded: len(created_ids) pages + margin, never infinite
        page = await tiny_page_client.version.list(project=test_project, search=shared_marker, offset=result_offset)
        found_ids.update(v.id for v in page.results)
        if page.next_offset is None:
            break
        result_offset = page.next_offset

    assert found_ids == set(created_ids)


async def test_create_get_update_delete_version(
    client: OpenProjectClient, test_project: str, version_ids: list[int]
) -> None:
    name = f"[integration-test] {uuid.uuid4().hex[:8]}"

    # Create
    result = await client.version.create(
        project=test_project,
        name=name,
        confirm=True,
    )
    assert result.ready, result.validation_errors
    version_id = result.version_id
    assert version_id > 0
    version_ids.append(version_id)

    # Read
    version = await client.version.get(version_id)
    assert version.name == name
    assert version.id == version_id

    # Update
    update_result = await client.version.update(
        version_id=version_id,
        name=f"{name} updated",
        confirm=True,
    )
    assert update_result.ready, update_result.validation_errors

    updated = await client.version.get(version_id)
    assert "updated" in updated.name

    # Delete
    delete_result = await client.version.delete(version_id=version_id, confirm=True)
    assert delete_result.ready and delete_result.state == "confirmed"
    version_ids.remove(version_id)


async def test_update_and_delete_version_denied_outside_write_allowlist(
    denied_client: OpenProjectClient, client: OpenProjectClient, test_project: str, version_ids: list[int]
) -> None:
    result = await client.version.create(
        project=test_project, name=f"[integration-test] denial {uuid.uuid4().hex[:8]}", confirm=True
    )
    assert result.ready, result.validation_errors
    version_id = result.version_id
    version_ids.append(version_id)

    with pytest.raises(PermissionDeniedError):
        await denied_client.version.update(version_id=version_id, name="denied update", confirm=True)

    with pytest.raises(PermissionDeniedError):
        await denied_client.version.delete(version_id=version_id, confirm=True)


async def test_project_version_list_leaves_out_shared_versions_of_projects_outside_the_read_scope(
    test_project: str, project_refs: list[str], start_scoped_client
) -> None:
    """A version shared system-wide is listed by its project's own versions route but is
    defined in a project the allowlist does not cover."""
    everywhere = await start_scoped_client(read_projects=("*",), write_projects=("*",))
    other_project = disposable_project_identifier()
    created = await everywhere.project.create(
        name=f"[integration-test] {other_project}", identifier=other_project, confirm=True
    )
    assert created.ready, created.validation_errors
    project_refs.append(other_project)

    name = f"[integration-test] shared {uuid.uuid4().hex[:8]}"
    version = await everywhere.version.create(project=other_project, name=name, sharing="system", confirm=True)
    assert version.ready, version.validation_errors

    listed_everywhere = await everywhere.version.list(project=test_project, search=name)
    assert [v.name for v in listed_everywhere.results] == [name]

    scoped = await start_scoped_client(read_projects=(test_project,), write_projects=(test_project,))
    assert (await scoped.version.list(project=test_project, search=name)).results == []


async def test_version_projects_lists_every_sharing_project_and_a_restricted_scope_keeps_only_its_own(
    test_project: str, project_refs: list[str], version_ids: list[int], start_scoped_client
) -> None:
    everywhere = await start_scoped_client(read_projects=("*",), write_projects=("*",))
    other_project = disposable_project_identifier()
    created = await everywhere.project.create(
        name=f"[integration-test] {other_project}", identifier=other_project, confirm=True
    )
    assert created.ready, created.validation_errors
    project_refs.append(other_project)

    version = await everywhere.version.create(
        project=test_project, name=f"[integration-test] shared {uuid.uuid4().hex[:8]}", sharing="system", confirm=True
    )
    assert version.ready, version.validation_errors
    version_ids.append(version.version_id)

    full = await everywhere.version.list_projects(version.version_id, limit=50)
    assert {test_project.lower(), other_project.lower()} <= {p.identifier.lower() for p in full.results if p.identifier}

    scoped = await start_scoped_client(read_projects=(test_project,), write_projects=(test_project,))
    own = await scoped.version.list_projects(version.version_id, limit=50)
    assert [p.identifier.lower() for p in own.results if p.identifier] == [test_project.lower()]


async def test_version_projects_refuses_a_version_defined_outside_the_read_scope(
    test_project: str, project_refs: list[str], start_scoped_client
) -> None:
    everywhere = await start_scoped_client(read_projects=("*",), write_projects=("*",))
    other_project = disposable_project_identifier()
    created = await everywhere.project.create(
        name=f"[integration-test] {other_project}", identifier=other_project, confirm=True
    )
    assert created.ready, created.validation_errors
    project_refs.append(other_project)
    version = await everywhere.version.create(
        project=other_project, name=f"[integration-test] foreign {uuid.uuid4().hex[:8]}", sharing="system", confirm=True
    )
    assert version.ready, version.validation_errors

    scoped = await start_scoped_client(read_projects=(test_project,), write_projects=(test_project,))
    with pytest.raises(PermissionDeniedError):
        await scoped.version.list_projects(version.version_id)
