from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_runtime_demo.adapters import FileSandbox, LocalActorClient, SandboxViolation
from agent_runtime_demo.fixtures import load_fixture
from agent_runtime_demo.runtime import PLAN, AgentRuntime, SimulatedCrash, build_runtime
from agent_runtime_demo.store import DurableStore

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures"


def test_normal_run_orchestrates_subagents_and_writes_verified_artifact(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES / "01-normal.json")
    runtime, store = build_runtime(
        fixture,
        database=tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
    )
    try:
        outcome = runtime.run(fixture)
    finally:
        store.close()

    assert outcome.status == "completed"
    assert {step.agent for step in outcome.steps} == {
        "registry_collector",
        "news_collector",
        "event_analyst",
        "evidence_verifier",
        "report_publisher",
    }
    report = json.loads((tmp_path / "workspace" / "normal-report.json").read_text())
    assert report["verification"] == {"event_count": 2, "invalid_count": 0, "verified": True}
    assert report["analysis"]["source_records"] == 3
    assert report["analysis"]["unique_events"] == 2


def test_transient_actor_failure_retries_with_bound(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES / "02-transient-actor.json")
    runtime, store = build_runtime(
        fixture,
        database=tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
    )
    try:
        outcome = runtime.run(fixture)
    finally:
        store.close()

    assert outcome.status == "completed"
    registry = next(step for step in outcome.steps if step.step_id == "collect_registry")
    assert registry.attempts == 3
    assert sum(event["event"] == "step_retry_scheduled" for event in outcome.trace) == 2


def test_sandbox_path_escape_fails_closed(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES / "03-sandbox-violation.json")
    runtime, store = build_runtime(
        fixture,
        database=tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
    )
    try:
        outcome = runtime.run(fixture)
    finally:
        store.close()

    assert outcome.status == "failed"
    publisher = next(step for step in outcome.steps if step.step_id == "publish_report")
    assert publisher.error == "path_escape_denied"
    assert not (tmp_path / "escape.json").exists()


def test_completed_checkpoint_is_resumed_without_rerunning_step(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES / "01-normal.json")
    runtime, store = build_runtime(
        fixture,
        database=tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
    )
    with pytest.raises(SimulatedCrash, match="collect_registry"):
        runtime.run(fixture, crash_after_step="collect_registry")

    before = next(
        step for step in store.steps(fixture.run_id) if step.step_id == "collect_registry"
    )
    outcome = runtime.run(fixture)
    after = next(step for step in outcome.steps if step.step_id == "collect_registry")
    store.close()

    assert outcome.status == "completed"
    assert before.attempts == after.attempts == 1
    assert any(
        event["event"] == "simulated_process_loss_after_checkpoint" for event in outcome.trace
    )


def test_rerunning_completed_run_is_idempotent(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES / "01-normal.json")
    runtime, store = build_runtime(
        fixture,
        database=tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
    )
    first = runtime.run(fixture)
    second = runtime.run(fixture)
    store.close()

    assert first.status == second.status == "completed"
    assert [step.attempts for step in second.steps] == [1, 1, 1, 1, 1]


def test_monitor_recovers_expired_running_step(tmp_path: Path) -> None:
    store = DurableStore(tmp_path / "runtime.sqlite")
    store.create_run(
        "stale-run",
        "fixture",
        PLAN,
        fixture_digest="test-digest",
        fixture_snapshot_json='{"fixture_id":"fixture"}',
    )
    store.claim_step("stale-run", "collect_registry")

    recovered = store.recover_stale(before=datetime.now(UTC) + timedelta(seconds=1))
    step = next(item for item in store.steps("stale-run") if item.step_id == "collect_registry")
    health = store.health()
    store.close()

    assert recovered == 1
    assert step.status == "pending"
    assert step.error == "stale_lease_recovered"
    assert health["running"] == 1


def test_actor_idempotency_key_reuses_run_id() -> None:
    actor = LocalActorClient({"news": []}, {})

    first = actor.run_actor("news", idempotency_key="same")
    second = actor.run_actor("news", idempotency_key="same")

    assert first["actor_run_id"] == second["actor_run_id"]


def test_sandbox_rejects_non_json_artifact(tmp_path: Path) -> None:
    sandbox = FileSandbox(tmp_path)

    with pytest.raises(SandboxViolation, match="only_json_artifacts_allowed"):
        sandbox.write_json("report.txt", {"safe": True})


def test_retry_exhaustion_marks_run_failed(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES / "02-transient-actor.json")
    store = DurableStore(tmp_path / "runtime.sqlite")
    runtime = AgentRuntime(
        store=store,
        actor=LocalActorClient(fixture.sources, {"registry": 3}),
        sandbox=FileSandbox(tmp_path / "workspace"),
        max_attempts=3,
    )

    outcome = runtime.run(fixture)
    store.close()

    assert outcome.status == "failed"
    registry = next(step for step in outcome.steps if step.step_id == "collect_registry")
    assert registry.attempts == 3
    assert registry.error == "registry:temporary_unavailable"
