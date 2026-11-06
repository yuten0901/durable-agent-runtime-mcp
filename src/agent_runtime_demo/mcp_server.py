from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TextIO, cast

from .fixtures import fixture_digest, load_fixture
from .models import RuntimeOutcome, WorkflowFixture
from .runtime import SimulatedCrash, build_runtime
from .store import DurableStore

PROTOCOL_VERSION = "2025-06-18"
SERVER_NOT_INITIALIZED = -32002


class FixtureCatalog:
    """許可済みディレクトリ内のfixtureだけをIDで解決する。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._fixtures: dict[str, Path] = {}
        for path in sorted(self._root.glob("*.json")):
            resolved = path.resolve()
            try:
                resolved.relative_to(self._root)
            except ValueError as error:
                raise ValueError(f"fixture escapes catalog root: {path.name}") from error
            fixture = load_fixture(resolved)
            if fixture.fixture_id in self._fixtures:
                raise ValueError(f"duplicate fixture_id: {fixture.fixture_id}")
            self._fixtures[fixture.fixture_id] = resolved

    def load(self, fixture_id: str) -> WorkflowFixture:
        path = self._fixtures.get(fixture_id)
        if path is None:
            raise ValueError(f"unknown fixture_id: {fixture_id}")
        return load_fixture(path)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._fixtures))


class AgentRuntimeMcpServer:
    """外部SDKなしで検証できる最小のMCP stdio tool server。"""

    def __init__(self, *, fixtures: Path, database: Path, workspace: Path) -> None:
        self._catalog = FixtureCatalog(fixtures)
        self._database = database
        self._workspace = workspace
        self._initialize_received = False
        self._ready = False

    def handle(self, request: Mapping[str, object]) -> dict[str, object] | None:
        has_id = "id" in request
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params")
        params_are_structured = "params" not in request or isinstance(params, (dict, list))
        if (
            request.get("jsonrpc") != "2.0"
            or not isinstance(method, str)
            or not params_are_structured
            or (has_id and not self._valid_request_id(request_id))
        ):
            return self._error(None, -32600, "Invalid Request")
        if not has_id:
            self._handle_notification(method, params)
            return None
        try:
            if method == "initialize":
                result = self._initialize(request.get("params"))
                return self._result(
                    request_id,
                    result,
                )
            if not self._ready:
                return self._error(request_id, SERVER_NOT_INITIALIZED, "Server not initialized")
            if method == "ping":
                return self._result(request_id, {})
            if method == "tools/list":
                return self._result(request_id, {"tools": self._tools()})
            if method == "tools/call":
                try:
                    tool_result = self._call_tool(request.get("params"))
                except (KeyError, TypeError, ValueError) as error:
                    tool_result = self._tool_error(str(error))
                return self._result(request_id, tool_result)
            return self._error(request_id, -32601, "Method not found")
        except (KeyError, TypeError, ValueError) as error:
            return self._error(request_id, -32602, str(error))
        except Exception as error:  # pragma: no cover - 最終プロトコル境界
            return self._error(request_id, -32603, f"Internal error: {type(error).__name__}")

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            if not line.strip():
                continue
            response: dict[str, object] | None
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                response = self._error(None, -32700, str(error))
            else:
                if isinstance(payload, dict):
                    response = self.handle(cast(dict[str, object], payload))
                else:
                    response = self._error(None, -32600, "Invalid Request")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()

    def _call_tool(self, params_value: object) -> dict[str, object]:
        if not isinstance(params_value, dict):
            raise ValueError("tools/call params must be an object")
        params = cast(dict[str, object], params_value)
        self._reject_unknown_keys(params, {"name", "arguments", "_meta"})
        name = params.get("name")
        arguments_value = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments_value, dict):
            raise ValueError("tool name and arguments are required")
        arguments = cast(dict[str, object], arguments_value)
        self._validate_tool_arguments(name, arguments)

        if name == "list_fixtures":
            payload: dict[str, object] = {"fixture_ids": list(self._catalog.ids())}
        elif name == "start_workflow":
            fixture_id = self._required_string(arguments, "fixture_id")
            checkpoint = arguments.get("simulate_process_loss_after_step")
            if checkpoint is not None and not isinstance(checkpoint, str):
                raise ValueError("simulate_process_loss_after_step must be a string")
            payload = self._start(fixture_id, checkpoint)
        elif name == "resume_workflow":
            payload = self._resume(self._required_string(arguments, "run_id"))
        elif name == "get_run_status":
            payload = self._status(self._required_string(arguments, "run_id"))
        elif name == "get_run_trace":
            payload = self._trace(self._required_string(arguments, "run_id"))
        else:
            raise ValueError(f"unknown tool: {name}")
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return {
            "content": [{"type": "text", "text": rendered}],
            "structuredContent": payload,
            "isError": False,
        }

    def _start(self, fixture_id: str, checkpoint: str | None) -> dict[str, object]:
        fixture = self._catalog.load(fixture_id)
        runtime, store = build_runtime(
            fixture, database=self._database, workspace=self._workspace
        )
        try:
            if store.run_exists(fixture.run_id):
                raise ValueError(f"run already exists: {fixture.run_id}")
            try:
                outcome = runtime.run(fixture, crash_after_step=checkpoint)
            except SimulatedCrash as error:
                return {
                    "run_id": fixture.run_id,
                    "status": "interrupted_after_checkpoint",
                    "checkpoint_step": str(error),
                    "next_action": "resume_workflow",
                }
            return self._outcome_payload(outcome)
        finally:
            store.close()

    def _resume(self, run_id: str) -> dict[str, object]:
        store = DurableStore(self._database)
        try:
            fixture_id = store.fixture_id(run_id)
            stored_digest, _ = store.fixture_binding(run_id)
            stored_status = store.run_status(run_id)
        finally:
            store.close()
        fixture = self._catalog.load(fixture_id)
        if fixture_digest(fixture) != stored_digest:
            raise ValueError("fixture_binding_mismatch")
        if fixture.run_id != run_id:
            raise ValueError("stored run_id does not match fixture")
        if stored_status in {"completed", "failed"}:
            return self._status(run_id)
        runtime, resumed_store = build_runtime(
            fixture, database=self._database, workspace=self._workspace
        )
        try:
            return self._outcome_payload(runtime.run(fixture))
        finally:
            resumed_store.close()

    def _status(self, run_id: str) -> dict[str, object]:
        store = DurableStore(self._database)
        try:
            return self._status_from_store(store, run_id)
        finally:
            store.close()

    def _trace(self, run_id: str) -> dict[str, object]:
        store = DurableStore(self._database)
        try:
            store.run_status(run_id)
            return {"run_id": run_id, "trace": list(store.traces(run_id))}
        finally:
            store.close()

    @staticmethod
    def _status_from_store(store: DurableStore, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "fixture_id": store.fixture_id(run_id),
            "fixture_digest": store.fixture_binding(run_id)[0],
            "status": store.run_status(run_id),
            "steps": [asdict(step) for step in store.steps(run_id)],
        }

    @staticmethod
    def _outcome_payload(outcome: RuntimeOutcome) -> dict[str, object]:
        return {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "steps": [asdict(step) for step in outcome.steps],
            "trace_count": len(outcome.trace),
        }

    @staticmethod
    def _required_string(arguments: Mapping[str, object], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    def _initialize(self, params_value: object) -> dict[str, object]:
        if self._initialize_received:
            raise ValueError("initialize may only be called once")
        if not isinstance(params_value, dict):
            raise ValueError("initialize params must be an object")
        params = cast(dict[str, object], params_value)
        protocol_version = params.get("protocolVersion")
        capabilities = params.get("capabilities")
        client_info = params.get("clientInfo")
        if not isinstance(protocol_version, str) or not protocol_version:
            raise ValueError("protocolVersion must be a non-empty string")
        if not isinstance(capabilities, dict):
            raise ValueError("capabilities must be an object")
        if not isinstance(client_info, dict):
            raise ValueError("clientInfo must be an object")
        name = client_info.get("name")
        version = client_info.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("clientInfo name and version must be non-empty strings")
        self._initialize_received = True
        negotiated = protocol_version if protocol_version == PROTOCOL_VERSION else PROTOCOL_VERSION
        return {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "durable-agent-runtime-demo",
                "version": "0.2.0",
            },
        }

    def _handle_notification(self, method: str, params_value: object) -> None:
        if method != "notifications/initialized" or not self._initialize_received:
            return
        if params_value is not None and not isinstance(params_value, dict):
            return
        self._ready = True

    @staticmethod
    def _valid_request_id(request_id: object) -> bool:
        return isinstance(request_id, str) or (
            isinstance(request_id, int) and not isinstance(request_id, bool)
        )

    @classmethod
    def _validate_tool_arguments(cls, name: str, arguments: Mapping[str, object]) -> None:
        schemas: dict[str, tuple[set[str], set[str], dict[str, type[object]]]] = {
            "list_fixtures": (set(), set(), {}),
            "start_workflow": (
                {"fixture_id", "simulate_process_loss_after_step"},
                {"fixture_id"},
                {
                    "fixture_id": str,
                    "simulate_process_loss_after_step": str,
                },
            ),
            "resume_workflow": ({"run_id"}, {"run_id"}, {"run_id": str}),
            "get_run_status": ({"run_id"}, {"run_id"}, {"run_id": str}),
            "get_run_trace": ({"run_id"}, {"run_id"}, {"run_id": str}),
        }
        schema = schemas.get(name)
        if schema is None:
            raise ValueError(f"unknown tool: {name}")
        allowed, required, types = schema
        cls._reject_unknown_keys(arguments, allowed)
        missing = sorted(required - set(arguments))
        if missing:
            raise ValueError(f"missing required arguments: {', '.join(missing)}")
        for key, expected in types.items():
            if key in arguments and not isinstance(arguments[key], expected):
                raise ValueError(f"{key} must be a {expected.__name__}")
        for key in required:
            if isinstance(arguments[key], str) and not arguments[key]:
                raise ValueError(f"{key} must be a non-empty string")

    @staticmethod
    def _reject_unknown_keys(values: Mapping[str, object], allowed: set[str]) -> None:
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unexpected properties: {', '.join(unknown)}")

    @staticmethod
    def _result(request_id: object, result: dict[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: object, code: int, message: str) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _tool_error(message: str) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": message}],
            "isError": True,
        }

    @staticmethod
    def _tools() -> list[dict[str, object]]:
        run_id_schema = {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        }
        return [
            {
                "name": "list_fixtures",
                "description": "利用可能なローカル評価fixture IDを列挙する",
                "inputSchema": {"type": "object", "additionalProperties": False},
            },
            {
                "name": "start_workflow",
                "description": "許可済みfixtureから永続workflowを開始する",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fixture_id": {"type": "string"},
                        "simulate_process_loss_after_step": {"type": "string"},
                    },
                    "required": ["fixture_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "resume_workflow",
                "description": "checkpoint済みworkflowを未完了stepから再開する",
                "inputSchema": run_id_schema,
            },
            {
                "name": "get_run_status",
                "description": "runと各stepの永続状態を取得する",
                "inputSchema": run_id_schema,
            },
            {
                "name": "get_run_trace",
                "description": "runの順序付き監査traceを取得する",
                "inputSchema": run_id_schema,
            },
        ]


def run_stdio_server(*, fixtures: Path, database: Path, workspace: Path) -> None:
    AgentRuntimeMcpServer(
        fixtures=fixtures,
        database=database,
        workspace=workspace,
    ).serve(sys.stdin, sys.stdout)
