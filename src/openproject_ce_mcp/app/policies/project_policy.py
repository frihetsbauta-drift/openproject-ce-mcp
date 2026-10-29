"""Projects-only policy. Pure, no I/O.

Read/write allowlist checks for projects, built on the shared scope.py
primitives rather than duplicating candidate-matching logic here.
"""

from __future__ import annotations

from typing import Any

from ... import policy_observation
from ...config import Settings
from ..errors import ProjectScopeDeniedError
from .scope import project_payload_candidates, project_record_candidates, scope_allows_all, scope_matches_candidates


def _display_value(payload: dict[str, Any]) -> str | None:
    """A single, unambiguous display value for the `project_scope` log
    field -- mirrors scope.py's `_project_scope_display`, but for a payload
    rather than a HAL link. Prefers the identifier over the numeric id,
    since an identifier is what an operator actually recognizes in an allowlist."""
    identifier = payload.get("identifier")
    if identifier:
        return str(identifier)
    project_id = payload.get("id")
    return None if project_id is None else str(project_id)


def ensure_project_read_allowed(payload: dict[str, Any], *, settings: Settings) -> None:
    """Read-allowlist check on an already-resolved project payload."""
    policy_observation.record_project_scope(_display_value(payload))
    if scope_allows_all(settings.read_projects):
        policy_observation.record_policy_decision("project_read_allowed")
        return
    if not scope_matches_candidates(settings.read_projects, project_payload_candidates(payload)):
        policy_observation.record_policy_decision("project_read_denied")
        raise ProjectScopeDeniedError("OpenProject access to this project is disabled by OPENPROJECT_READ_PROJECTS.")
    policy_observation.record_policy_decision("project_read_allowed")


def ensure_project_write_allowed(payload: dict[str, Any], *, settings: Settings) -> None:
    """Read- AND write-allowlist check (write implies read), for update/delete/favorite."""
    ensure_project_read_allowed(payload, settings=settings)
    # The read check above already recorded project_scope/a read-flavored
    # decision; this write-flavored decision, recorded last, is what a human
    # debugging a denial actually sees -- same reasoning as scope.py's
    # ensure_project_write_link_allowed's own nested-call comment.
    if scope_allows_all(settings.write_projects):
        policy_observation.record_policy_decision("project_write_allowed")
        return
    if not scope_matches_candidates(settings.write_projects, project_payload_candidates(payload)):
        policy_observation.record_policy_decision("project_write_denied")
        raise ProjectScopeDeniedError("OpenProject writes to this project are disabled by OPENPROJECT_WRITE_PROJECTS.")
    policy_observation.record_policy_decision("project_write_allowed")


def ensure_project_create_target_allowed(
    *,
    identifier: str,
    settings: Settings,
) -> None:
    """Read- then write-allowlist check on an intended create/copy target.

    No resolved payload with an `id` exists yet at create time, so this checks
    the intended identifier directly -- read is checked first, then
    write, since a writable target must also be readable.
    """
    policy_observation.record_project_scope(identifier)
    candidates = project_record_candidates(None, identifier)
    if not scope_allows_all(settings.read_projects) and not scope_matches_candidates(
        settings.read_projects, candidates
    ):
        policy_observation.record_policy_decision("project_read_denied")
        raise ProjectScopeDeniedError("OpenProject access to this project is disabled by OPENPROJECT_READ_PROJECTS.")
    if not scope_allows_all(settings.write_projects) and not scope_matches_candidates(
        settings.write_projects, candidates
    ):
        policy_observation.record_policy_decision("project_write_denied")
        raise ProjectScopeDeniedError("OpenProject writes to this project are disabled by OPENPROJECT_WRITE_PROJECTS.")
    policy_observation.record_policy_decision("project_write_allowed")
