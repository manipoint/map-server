"""Request-scoped persistence dependency for the planning graph."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.domain.planning import PlanningState

PersistRequirements = Callable[
    [PlanningState, bool],
    Awaitable[PlanningState],
]


@dataclass(frozen=True, slots=True)
class PlanningRuntimeContext:
    persist_requirements: PersistRequirements
