"""An instance that limits its API pages below OPENPROJECT_MAX_PAGE_SIZE is read to its end."""

from __future__ import annotations

import dataclasses
import os

import pytest

from openproject_ce_mcp.client import with_instance_page_limit

from .conftest import _integration_settings, _run_rails_script

pytestmark = pytest.mark.integration

INSTANCE_LIMIT = 1


@pytest.fixture
def lowered_instance_limit():
    if "OPENPROJECT_DOCKER_SERVICE" not in os.environ:
        pytest.skip("OPENPROJECT_DOCKER_SERVICE not set; the instance setting is changed through a Rails runner")
    previous = _run_rails_script(
        'puts "previous=#{Setting.apiv3_max_page_size}"; Setting.apiv3_max_page_size = ENV["LIMIT"].to_i',
        result_key="previous",
        env={"LIMIT": str(INSTANCE_LIMIT)},
    )
    yield
    _run_rails_script(
        'Setting.apiv3_max_page_size = ENV["LIMIT"].to_i; puts "restored=1"',
        result_key="restored",
        env={"LIMIT": previous},
    )


@pytest.mark.asyncio
async def test_the_listing_of_a_server_paging_tool_reaches_its_end_below_the_configured_page_size(
    lowered_instance_limit,
    open_client,
) -> None:
    settings = _integration_settings()
    assert settings is not None
    configured = dataclasses.replace(settings, max_page_size=50, default_page_size=20, max_results=100)

    capped = await with_instance_page_limit(configured)
    assert capped.max_page_size == INSTANCE_LIMIT

    client = await open_client(capped)
    ids: list[int] = []
    offset: int | None = 1
    total = 0
    while offset is not None:
        page = await client.user.list_users(offset=offset, limit=50)
        ids.extend(user.id for user in page.results)
        total = page.total
        offset = page.next_offset
    assert len(ids) == len(set(ids)) == total
    assert total > INSTANCE_LIMIT
