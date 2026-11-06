from __future__ import annotations

from pathlib import Path
from typing import cast

from .adapters import FileSandbox, LocalActorClient, SandboxViolation, TransientActorError
from .fixtures import canonical_fixture_json, fixture_digest
from .models import RuntimeOutcome, StepRecord, StepSpec, WorkflowFixture
from .store import DurableStore


class SimulatedCrash(RuntimeError):
    """checkpoint再開試験でのみ使う、同一process内の制御例外。"""


PLAN = (
    StepSpec("collect_registry", "registry_collector", "registry"),
    StepSpec("collect_news", "news_collector", "news"),
    StepSpec(
        "analyze_events",
        "event_analyst",
        "analyze",
        ("collect_registry", "collect_news"),
    ),
    StepSpec("verify_evidence", "evidence_verifier", "verify", ("analyze_events",)),
    StepSpec("publish_report", "report_publisher", "publish", ("verify_evidence",)),
)


class AgentRuntime:
    """逐次tick、永続checkpoint、bounded retryを備えるmulti-agent runtime。"""

    def __init__(
        self,
        *,
        store: DurableStore,
        actor: LocalActorClient,
        sandbox: FileSandbox,
        max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._actor = actor
        self._sandbox = sandbox
        self._max_attempts = max_attempts

    def run(
        self,
        fixture: WorkflowFixture,
        *,
        crash_after_step: str | None = None,
        max_ticks: int = 50,
    ) -> RuntimeOutcome:
        is_resume = self._store.run_exists(fixture.run_id)
        self._store.create_run(
            fixture.run_id,
            fixture.fixture_id,
            PLAN,
            fixture_digest=fixture_digest(fixture),
            fixture_snapshot_json=canonical_fixture_json(fixture),
        )
        self._store.trace(
            fixture.run_id,
            "run_resumed" if is_resume else "run_started",
            fixture_id=fixture.fixture_id,
        )

        for _ in range(max_ticks):
            current = self._store.run_status(fixture.run_id)
            if current in {"completed", "failed"}:
                return self._outcome(fixture.run_id)

            step = self._store.next_ready_step(fixture.run_id)
            if step is None:
                if all(item.status == "completed" for item in self._store.steps(fixture.run_id)):
                    self._store.set_run_status(fixture.run_id, "completed")
                    self._store.trace(fixture.run_id, "run_completed")
                    return self._outcome(fixture.run_id)
                self._store.set_run_status(fixture.run_id, "failed")
                self._store.trace(fixture.run_id, "run_failed", reason="dependency_deadlock")
                return self._outcome(fixture.run_id)

            attempts = self._store.claim_step(fixture.run_id, step.step_id)
            self._store.trace(
                fixture.run_id,
                "step_started",
                agent=step.agent,
                step_id=step.step_id,
                attempt=attempts,
            )
            try:
                result = self._execute(fixture, step)
            except TransientActorError as error:
                if attempts < self._max_attempts:
                    self._store.retry_step(fixture.run_id, step.step_id, str(error))
                    self._store.trace(
                        fixture.run_id,
                        "step_retry_scheduled",
                        agent=step.agent,
                        step_id=step.step_id,
                        reason=str(error),
                        next_attempt=attempts + 1,
                    )
                    continue
                self._fail(fixture.run_id, step, str(error))
                return self._outcome(fixture.run_id)
            except (SandboxViolation, ValueError) as error:
                self._fail(fixture.run_id, step, str(error))
                return self._outcome(fixture.run_id)

            self._store.complete_step(fixture.run_id, step.step_id, result)
            self._store.trace(
                fixture.run_id,
                "step_completed",
                agent=step.agent,
                step_id=step.step_id,
                result_keys=sorted(result),
            )
            if step.step_id == crash_after_step:
                self._store.trace(
                    fixture.run_id,
                    "simulated_process_loss_after_checkpoint",
                    agent=step.agent,
                    step_id=step.step_id,
                )
                raise SimulatedCrash(step.step_id)

        self._store.set_run_status(fixture.run_id, "failed")
        self._store.trace(fixture.run_id, "run_failed", reason="tick_budget_exhausted")
        return self._outcome(fixture.run_id)

    def _execute(self, fixture: WorkflowFixture, step: StepRecord) -> dict[str, object]:
        if step.action in {"registry", "news"}:
            return self._actor.run_actor(
                step.action,
                idempotency_key=f"{fixture.run_id}:{step.step_id}",
            )
        if step.action == "analyze":
            records: list[dict[str, str]] = []
            for dependency in ("collect_registry", "collect_news"):
                dataset_value = self._store.result(fixture.run_id, dependency)["dataset"]
                if not isinstance(dataset_value, list):
                    raise ValueError(f"{dependency} returned a non-list dataset")
                for item in dataset_value:
                    if not isinstance(item, dict) or not all(
                        isinstance(key, str) and isinstance(value, str)
                        for key, value in item.items()
                    ):
                        raise ValueError(f"{dependency} returned an invalid dataset item")
                    records.append(cast(dict[str, str], item))
            deduplicated: dict[str, dict[str, str]] = {}
            for record in records:
                deduplicated[record["url"]] = record
            events = [
                {
                    **record,
                    "event_type": self._classify(record["title"]),
                }
                for record in deduplicated.values()
            ]
            return {"events": events, "source_records": len(records), "unique_events": len(events)}
        if step.action == "verify":
            analyzed_events_value = self._store.result(fixture.run_id, "analyze_events")["events"]
            if not isinstance(analyzed_events_value, list):
                raise ValueError("analyze_events returned a non-list payload")
            analyzed_events = cast(list[dict[str, object]], analyzed_events_value)
            invalid = [
                event for event in analyzed_events if not event.get("title") or not event.get("url")
            ]
            return {
                "verified": not invalid and bool(analyzed_events),
                "event_count": len(analyzed_events),
                "invalid_count": len(invalid),
            }
        if step.action == "publish":
            verification = self._store.result(fixture.run_id, "verify_evidence")
            analysis = self._store.result(fixture.run_id, "analyze_events")
            target = self._sandbox.write_json(
                fixture.output_path,
                {"run_id": fixture.run_id, "verification": verification, "analysis": analysis},
            )
            return {"artifact": str(target), "written": True}
        raise ValueError(f"unknown action: {step.action}")

    def _fail(self, run_id: str, step: StepRecord, error: str) -> None:
        self._store.fail_step(run_id, step.step_id, error)
        self._store.trace(
            run_id,
            "step_failed",
            agent=step.agent,
            step_id=step.step_id,
            reason=error,
        )
        self._store.trace(run_id, "run_failed", reason=error)

    def _outcome(self, run_id: str) -> RuntimeOutcome:
        return RuntimeOutcome(
            run_id=run_id,
            status=self._store.run_status(run_id),
            steps=self._store.steps(run_id),
            trace=self._store.traces(run_id),
        )

    @staticmethod
    def _classify(title: str) -> str:
        lowered = title.casefold()
        if "appoint" in lowered or "director" in lowered:
            return "leadership_change"
        if "launch" in lowered or "release" in lowered:
            return "product_launch"
        return "company_update"


def build_runtime(
    fixture: WorkflowFixture, *, database: Path, workspace: Path
) -> tuple[AgentRuntime, DurableStore]:
    store = DurableStore(database)
    runtime = AgentRuntime(
        store=store,
        actor=LocalActorClient(fixture.sources, fixture.transient_failures),
        sandbox=FileSandbox(workspace),
    )
    return runtime, store
