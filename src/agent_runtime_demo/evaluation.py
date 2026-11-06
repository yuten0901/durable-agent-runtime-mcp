from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from .fixtures import load_fixture
from .mcp_server import PROTOCOL_VERSION, SERVER_NOT_INITIALIZED, AgentRuntimeMcpServer
from .runtime import build_runtime


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    expected: str
    actual: str
    passed: bool
    completed_steps: int
    retries: int
    trace_events: int


@dataclass(frozen=True)
class McpCheckResult:
    check: str
    passed: bool


def _initialize(server: AgentRuntimeMcpServer) -> None:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "evaluation", "version": "1"},
            },
        }
    )
    if response is None or "result" not in response:
        raise RuntimeError("MCP initialize failed")
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})


def _tool(
    server: AgentRuntimeMcpServer,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    if response is None:
        raise RuntimeError("MCP tool returned no response")
    return cast(dict[str, object], response["result"])


def _evaluate_mcp(fixtures_dir: Path) -> dict[str, object]:
    checks: list[McpCheckResult] = []
    with TemporaryDirectory() as directory:
        temp = Path(directory)
        server = AgentRuntimeMcpServer(
            fixtures=fixtures_dir,
            database=temp / "runtime.sqlite",
            workspace=temp / "workspace",
        )
        pre_initialize = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        pre_error = (
            cast(dict[str, object], pre_initialize["error"]) if pre_initialize else {}
        )
        _initialize(server)
        checks.append(
            McpCheckResult(
                "handshake_blocks_tools_until_initialized",
                pre_error.get("code") == SERVER_NOT_INITIALIZED,
            )
        )
        interrupted = _tool(
            server,
            "start_workflow",
            {
                "fixture_id": "normal-multi-agent-run",
                "simulate_process_loss_after_step": "collect_registry",
            },
        )
        interrupted_payload = cast(dict[str, object], interrupted["structuredContent"])
        resumed_server = AgentRuntimeMcpServer(
            fixtures=fixtures_dir,
            database=temp / "runtime.sqlite",
            workspace=temp / "workspace",
        )
        _initialize(resumed_server)
        resumed = _tool(
            resumed_server,
            "resume_workflow",
            {"run_id": cast(str, interrupted_payload["run_id"])},
        )
        resumed_payload = cast(dict[str, object], resumed["structuredContent"])
        status = _tool(
            resumed_server,
            "get_run_status",
            {"run_id": cast(str, interrupted_payload["run_id"])},
        )
        status_payload = cast(dict[str, object], status["structuredContent"])
        steps = cast(list[dict[str, object]], status_payload["steps"])
        registry = next(step for step in steps if step["step_id"] == "collect_registry")
        checks.append(
            McpCheckResult(
                "new_server_resumes_without_rerunning_checkpoint",
                resumed_payload["status"] == "completed" and registry["attempts"] == 1,
            )
        )
        schema_error = _tool(resumed_server, "list_fixtures", {"unexpected": True})
        checks.append(
            McpCheckResult(
                "tool_schema_rejects_additional_properties",
                schema_error.get("isError") is True,
            )
        )

    with TemporaryDirectory() as directory:
        temp = Path(directory)
        copied_fixtures = temp / "fixtures"
        shutil.copytree(fixtures_dir, copied_fixtures)
        first = AgentRuntimeMcpServer(
            fixtures=copied_fixtures,
            database=temp / "runtime.sqlite",
            workspace=temp / "workspace",
        )
        _initialize(first)
        interrupted = _tool(
            first,
            "start_workflow",
            {
                "fixture_id": "normal-multi-agent-run",
                "simulate_process_loss_after_step": "collect_registry",
            },
        )
        interrupted_payload = cast(dict[str, object], interrupted["structuredContent"])
        fixture_path = copied_fixtures / "01-normal.json"
        mutated = json.loads(fixture_path.read_text(encoding="utf-8"))
        mutated["output_path"] = "mutated-report.json"
        fixture_path.write_text(json.dumps(mutated), encoding="utf-8")
        second = AgentRuntimeMcpServer(
            fixtures=copied_fixtures,
            database=temp / "runtime.sqlite",
            workspace=temp / "workspace",
        )
        _initialize(second)
        rejected = _tool(
            second,
            "resume_workflow",
            {"run_id": cast(str, interrupted_payload["run_id"])},
        )
        checks.append(
            McpCheckResult(
                "changed_fixture_is_rejected_fail_closed",
                rejected.get("isError") is True,
            )
        )

    passed = sum(check.passed for check in checks)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "summary": {
            "checks": len(checks),
            "passed": passed,
            "failed": len(checks) - passed,
        },
        "checks": [asdict(check) for check in checks],
    }


def evaluate(fixtures_dir: Path) -> dict[str, object]:
    scenarios: list[ScenarioResult] = []
    for path in sorted(fixtures_dir.glob("*.json")):
        fixture = load_fixture(path)
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            runtime, store = build_runtime(
                fixture,
                database=temp / "runtime.sqlite",
                workspace=temp / "workspace",
            )
            try:
                outcome = runtime.run(fixture)
                scenarios.append(
                    ScenarioResult(
                        scenario=fixture.fixture_id,
                        expected=fixture.expected_status,
                        actual=outcome.status,
                        passed=outcome.status == fixture.expected_status,
                        completed_steps=sum(step.status == "completed" for step in outcome.steps),
                        retries=sum(
                            event["event"] == "step_retry_scheduled" for event in outcome.trace
                        ),
                        trace_events=len(outcome.trace),
                    )
                )
            finally:
                store.close()
    passed = sum(scenario.passed for scenario in scenarios)
    return {
        "schema_version": 2,
        "execution_mode": "offline_fixture_only",
        "summary": {
            "scenarios": len(scenarios),
            "passed": passed,
            "failed": len(scenarios) - passed,
        },
        "scenarios": [asdict(scenario) for scenario in scenarios],
        "mcp": _evaluate_mcp(fixtures_dir),
        "limitations": [
            "Apify互換境界はローカルadapterであり、実Apify actorを実行していない",
            "SQLite checkpointを検証し、Cloudflare D1との互換性や本番migrationは未検証",
            "Next.js UI、Cloudflare Workers、外部LLM、外部deploymentは実装・公開していない",
            "中断・runtime再構築・stale回収は同一process内の決定的試験であり、"
            "OS process終了や実時間soak testではない",
            "MCPはローカルstdioとprotocol version 2025-06-18の境界だけを検証し、"
            "実Client互換性は未検証",
            "MCPのremote transport、認証、multi-tenant authorization、rate limitは未実装",
            "fixture snapshotとSHA-256をrunへ固定し変更後resumeを拒否するが、"
            "外部data sourceの内容固定は未検証",
        ],
    }
