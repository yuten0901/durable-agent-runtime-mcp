from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from .evaluation import evaluate
from .fixtures import load_fixture
from .mcp_server import run_stdio_server
from .runtime import SimulatedCrash, build_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ローカルmulti-agent runtime demo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="1件のworkflow fixtureを実行する")
    run.add_argument("fixture", type=Path)
    run.add_argument("--database", type=Path, default=Path("reports/runtime.sqlite"))
    run.add_argument("--workspace", type=Path, default=Path("workspace"))
    run.add_argument("--output", type=Path)
    evaluation = subparsers.add_parser("evaluate", help="全fixtureを隔離環境で評価する")
    evaluation.add_argument("fixtures_dir", type=Path)
    evaluation.add_argument("--output", type=Path)
    demo = subparsers.add_parser(
        "demo", help="checkpoint後の制御例外と新runtime objectからの再開を実演する"
    )
    demo.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).with_name("demo_fixture.json"),
        help="省略時はpackage同梱のoffline fixtureを使う",
    )
    demo.add_argument("--checkpoint", default="collect_registry")
    mcp = subparsers.add_parser("mcp", help="MCP stdio tool serverを開始する")
    mcp.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    mcp.add_argument("--database", type=Path, default=Path("reports/mcp-runtime.sqlite"))
    mcp.add_argument("--workspace", type=Path, default=Path("workspace/mcp"))
    return parser


def _write(payload: dict[str, object], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        print(rendered)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    print(output)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        _write(evaluate(args.fixtures_dir), args.output)
        return 0
    if args.command == "mcp":
        run_stdio_server(
            fixtures=args.fixtures,
            database=args.database,
            workspace=args.workspace,
        )
        return 0
    if args.command == "demo":
        return _run_demo(args.fixture, args.checkpoint)

    fixture = load_fixture(args.fixture)
    runtime, store = build_runtime(fixture, database=args.database, workspace=args.workspace)
    try:
        outcome = runtime.run(fixture)
        payload: dict[str, object] = {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "steps": [asdict(step) for step in outcome.steps],
            "trace": list(outcome.trace),
            "health": store.health(),
        }
    finally:
        store.close()
    _write(payload, args.output)
    return 0 if outcome.status == fixture.expected_status else 1


def _run_demo(fixture_path: Path, checkpoint: str) -> int:
    fixture = load_fixture(fixture_path)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "runtime.sqlite"
        workspace = root / "workspace"
        first_runtime, first_store = build_runtime(
            fixture, database=database, workspace=workspace
        )
        interrupted = False
        try:
            first_runtime.run(fixture, crash_after_step=checkpoint)
        except SimulatedCrash:
            interrupted = True
        finally:
            first_store.close()

        resumed_runtime, resumed_store = build_runtime(
            fixture, database=database, workspace=workspace
        )
        try:
            outcome = resumed_runtime.run(fixture)
            checkpoint_step = next(
                (step for step in outcome.steps if step.step_id == checkpoint), None
            )
            artifact_written = (workspace / fixture.output_path).is_file()
            payload: dict[str, object] = {
                "demo": "durable_resume_after_checkpoint",
                "run_id": fixture.run_id,
                "interrupted_after": checkpoint if interrupted else None,
                "final_status": outcome.status,
                "checkpoint_attempts": checkpoint_step.attempts if checkpoint_step else None,
                "completed_steps": sum(step.status == "completed" for step in outcome.steps),
                "trace_events": len(outcome.trace),
                "artifact_written": artifact_written,
            }
        finally:
            resumed_store.close()
    _write(payload, None)
    return 0 if interrupted and outcome.status == "completed" and artifact_written else 1


if __name__ == "__main__":
    raise SystemExit(main())
