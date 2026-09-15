"""The pipeline-schedule decline for a forge whose clock is in the workflow file.

GitHub and Gitea time a workflow through its own ``schedule`` trigger,
so they have no schedule to create through the API; every method here
raises [livery.forge.Unsupported][] naming the capability, which is
what lets a caller probe ``supports("pipeline_schedules")`` first and
stay idempotent.
"""

from __future__ import annotations

from collections.abc import Mapping

from livery.forge._errors import Unsupported
from livery.forge._types import Schedule


class DeclinedSchedules:
    """The livery.forge.Schedules view of a forge that keeps its clock in the file."""

    def __init__(self, forge_name: str) -> None:
        self._forge_name = forge_name

    def _decline(self) -> Unsupported:
        return Unsupported(
            f"{self._forge_name} keeps its clock in the workflow file's schedule"
            " trigger and has no pipeline schedule to create"
            " (capability: pipeline_schedules)"
        )

    def list(self) -> tuple[Schedule, ...]:
        """Decline by name."""
        raise self._decline()

    def ensure(
        self,
        description: str,
        *,
        ref: str,
        cron: str,
        variables: Mapping[str, str] | None = None,
    ) -> Schedule:
        """Decline by name."""
        raise self._decline()

    def delete(self, description: str) -> bool:
        """Decline by name."""
        raise self._decline()
