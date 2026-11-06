from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from agent_runtime_demo.mcp_server import (
    PROTOCOL_VERSION,
    SERVER_NOT_INITIALIZED,
    AgentRuntimeMcpServer,
)

ROOT = Path(__file__).parents[1]


def build_server(
    tmp_path: Path,
    *,
    fixtures: Path | None = None,
    ready: bool = True,
) -> AgentRuntimeMcpServer:
    server = AgentRuntimeMcpServer(
        fixtures=fixtures or ROOT / "fixtures",
        database=tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
    )
    if ready:
        complete_handshake(server)
    return server


def complete_handshake(
    server: AgentRuntimeMcpServer,
    *,
    protocol_version: str = PROTOCOL_VERSION,
) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
    )
    assert response is not None
    assert "error" not in response
    assert (
        server.handle(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        is None
    )
    return cast(dict[str, object], response["result"])


def tool_response(
    server: AgentRuntimeMcpServer,
    request_id: int,
    name: str,
    arguments: object,
) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    return cast(dict[str, object], response["result"])


def call_tool(
    server: AgentRuntimeMcpServer,
    request_id: int,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    result = tool_response(server, request_id, name, arguments)
    assert result["isError"] is False
    return cast(dict[str, object], result["structuredContent"])


def test_initialize_negotiates_known_version_and_discovers_tools(tmp_path: Path) -> None:
    server = build_server(tmp_path, ready=False)

    initialized_result = complete_handshake(server)
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert initialized_result["protocolVersion"] == PROTOCOL_VERSION
    assert listed is not None
    listed_result = cast(dict[str, object], listed["result"])
    tools = cast(list[dict[str, object]], listed_result["tools"])
    assert {tool["name"] for tool in tools} == {
        "list_fixtures",
        "start_workflow",
        "resume_workflow",
        "get_run_status",
        "get_run_trace",
    }


def test_initialize_counter_offers_supported_version_for_unknown_version(tmp_path: Path) -> None:
    server = build_server(tmp_path, ready=False)

    result = complete_handshake(server, protocol_version="2099-01-01")

    assert result["protocolVersion"] == PROTOCOL_VERSION


def test_tools_are_rejected_until_initialize_notification(tmp_path: Path) -> None:
    server = build_server(tmp_path, ready=False)
    before = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert before is not None
    assert cast(dict[str, object], before["error"])["code"] == SERVER_NOT_INITIALIZED

    initialized = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
    )
    waiting = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

    assert initialized is not None and "result" in initialized
    assert waiting is not None
    assert cast(dict[str, object], waiting["error"])["code"] == SERVER_NOT_INITIALIZED

    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    ready = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    assert ready is not None and "result" in ready


def test_invalid_initialize_params_do_not_initialize_server(tmp_path: Path) -> None:
    server = build_server(tmp_path, ready=False)

    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert response is not None
    assert cast(dict[str, object], response["error"])["code"] == -32602
    assert listed is not None
    assert cast(dict[str, object], listed["error"])["code"] == SERVER_NOT_INITIALIZED


@pytest.mark.parametrize("invalid_id", [None, True, 1.5, [], {}])
def test_invalid_request_ids_return_invalid_request(tmp_path: Path, invalid_id: object) -> None:
    server = build_server(tmp_path, ready=False)

    response = server.handle(
        {"jsonrpc": "2.0", "id": invalid_id, "method": "initialize", "params": {}}
    )

    assert response is not None
    assert response["id"] is None
    assert cast(dict[str, object], response["error"])["code"] == -32600


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "foo": "bar"},
        {"jsonrpc": "1.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": 3},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": "bad"},
    ],
)
def test_malformed_objects_without_id_return_invalid_request(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    server = build_server(tmp_path, ready=False)

    response = server.handle(payload)

    assert response is not None
    assert response["id"] is None
    assert cast(dict[str, object], response["error"])["code"] == -32600


def test_valid_notification_has_no_response(tmp_path: Path) -> None:
    server = build_server(tmp_path, ready=False)

    response = server.handle(
        {"jsonrpc": "2.0", "method": "unknown/notification", "params": {"value": 1}}
    )

    assert response is None


def test_mcp_tools_run_and_query_workflow(tmp_path: Path) -> None:
    server = build_server(tmp_path)
    fixtures = call_tool(server, 1, "list_fixtures", {})
    assert "normal-multi-agent-run" in cast(list[str], fixtures["fixture_ids"])

    started = call_tool(server, 2, "start_workflow", {"fixture_id": "normal-multi-agent-run"})
    status = call_tool(
        server, 3, "get_run_status", {"run_id": cast(str, started["run_id"])}
    )
    trace = call_tool(
        server, 4, "get_run_trace", {"run_id": cast(str, started["run_id"])}
    )

    assert started["status"] == "completed"
    assert status["status"] == "completed"
    assert len(cast(str, status["fixture_digest"])) == 64
    assert len(cast(list[dict[str, object]], status["steps"])) == 5
    assert cast(list[dict[str, object]], trace["trace"])[-1]["event"] == "run_completed"


def test_new_server_instance_resumes_without_rerunning_checkpoint(tmp_path: Path) -> None:
    first_server = build_server(tmp_path)
    interrupted = call_tool(
        first_server,
        1,
        "start_workflow",
        {
            "fixture_id": "normal-multi-agent-run",
            "simulate_process_loss_after_step": "collect_registry",
        },
    )

    resumed_server = build_server(tmp_path)
    resumed = call_tool(
        resumed_server,
        2,
        "resume_workflow",
        {"run_id": cast(str, interrupted["run_id"])},
    )
    status = call_tool(
        resumed_server,
        3,
        "get_run_status",
        {"run_id": cast(str, interrupted["run_id"])},
    )
    steps = cast(list[dict[str, object]], status["steps"])
    registry = next(step for step in steps if step["step_id"] == "collect_registry")

    assert interrupted["status"] == "interrupted_after_checkpoint"
    assert resumed["status"] == "completed"
    assert registry["attempts"] == 1


def test_changed_fixture_is_rejected_on_resume_by_new_server_instance(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(ROOT / "fixtures", fixture_dir)
    first_server = build_server(tmp_path, fixtures=fixture_dir)
    interrupted = call_tool(
        first_server,
        1,
        "start_workflow",
        {
            "fixture_id": "normal-multi-agent-run",
            "simulate_process_loss_after_step": "collect_registry",
        },
    )
    fixture_path = fixture_dir / "01-normal.json"
    mutated = json.loads(fixture_path.read_text(encoding="utf-8"))
    mutated["sources"]["news"] = [
        {"title": "Mutated event", "url": "https://mutated.test/new"}
    ]
    mutated["output_path"] = "mutated-report.json"
    fixture_path.write_text(json.dumps(mutated), encoding="utf-8")

    resumed_server = build_server(tmp_path, fixtures=fixture_dir)
    result = tool_response(
        resumed_server,
        2,
        "resume_workflow",
        {"run_id": cast(str, interrupted["run_id"])},
    )

    assert result["isError"] is True
    content = cast(list[dict[str, object]], result["content"])
    assert content[0]["text"] == "fixture_binding_mismatch"
    assert not (tmp_path / "workspace" / "mutated-report.json").exists()


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("list_fixtures", {"unexpected": True}, "unexpected properties"),
        ("start_workflow", {}, "missing required arguments"),
        ("start_workflow", {"fixture_id": 3}, "fixture_id must be a str"),
        ("start_workflow", {"fixture_id": "x", "unexpected": True}, "unexpected properties"),
        ("resume_workflow", {}, "missing required arguments"),
        ("resume_workflow", {"run_id": 3}, "run_id must be a str"),
        ("resume_workflow", {"run_id": "x", "unexpected": True}, "unexpected properties"),
        ("get_run_status", {}, "missing required arguments"),
        ("get_run_status", {"run_id": 3}, "run_id must be a str"),
        ("get_run_status", {"run_id": "x", "unexpected": True}, "unexpected properties"),
        ("get_run_trace", {}, "missing required arguments"),
        ("get_run_trace", {"run_id": 3}, "run_id must be a str"),
        ("get_run_trace", {"run_id": "x", "unexpected": True}, "unexpected properties"),
    ],
)
def test_tool_runtime_enforces_declared_input_schema(
    tmp_path: Path,
    name: str,
    arguments: dict[str, object],
    message: str,
) -> None:
    server = build_server(tmp_path)

    result = tool_response(server, 1, name, arguments)

    assert result["isError"] is True
    content = cast(list[dict[str, object]], result["content"])
    assert message in cast(str, content[0]["text"])


def test_unknown_fixture_is_a_tool_error_without_arbitrary_file_access(tmp_path: Path) -> None:
    server = build_server(tmp_path)
    result = tool_response(server, 1, "start_workflow", {"fixture_id": "../secret"})

    assert result["isError"] is True
    content = cast(list[dict[str, object]], result["content"])
    assert "unknown fixture_id" in cast(str, content[0]["text"])


def test_stdio_distinguishes_parse_error_invalid_request_and_notification(
    tmp_path: Path,
) -> None:
    server = build_server(tmp_path, ready=False)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "stdio-test", "version": "1"},
        },
    }
    stdin = io.StringIO(
        "{broken\n"
        + json.dumps([])
        + "\n"
        + json.dumps(initialize)
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        + "\n"
    )
    stdout = io.StringIO()

    server.serve(stdin, stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(responses) == 4
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["error"]["code"] == -32600
    assert responses[2]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert responses[3] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_documented_mcp_session_is_executable(tmp_path: Path) -> None:
    document = (ROOT / "docs" / "mcp-tools.md").read_text(encoding="utf-8")
    transcript = document.split("```jsonl", 1)[1].split("```", 1)[0].strip()
    server = build_server(tmp_path, ready=False)
    stdout = io.StringIO()

    server.serve(io.StringIO(transcript + "\n"), stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3, 4, 5]
    assert responses[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert len(responses[1]["result"]["tools"]) == 5
    assert responses[2]["result"]["structuredContent"]["status"] == (
        "interrupted_after_checkpoint"
    )
    assert responses[3]["result"]["structuredContent"]["status"] == "completed"
