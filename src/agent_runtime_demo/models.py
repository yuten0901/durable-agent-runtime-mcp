from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RunStatus = Literal["pending", "running", "completed", "failed"]
StepStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class StepSpec:
    step_id: str
    agent: str
    action: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowFixture:
    fixture_id: str
    run_id: str
    expected_status: Literal["completed", "failed"]
    sources: dict[str, list[dict[str, str]]]
    transient_failures: dict[str, int]
    output_path: str


@dataclass(frozen=True)
class StepRecord:
    step_id: str
    agent: str
    action: str
    status: StepStatus
    attempts: int
    result: dict[str, object] | None
    error: str | None


@dataclass(frozen=True)
class RuntimeOutcome:
    run_id: str
    status: RunStatus
    steps: tuple[StepRecord, ...]
    trace: tuple[dict[str, object], ...]
