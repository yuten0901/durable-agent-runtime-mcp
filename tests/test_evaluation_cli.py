from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from agent_runtime_demo.cli import main
from agent_runtime_demo.evaluation import evaluate

ROOT = Path(__file__).parents[1]


def test_evaluation_passes_all_scenarios() -> None:
    report = evaluate(ROOT / "fixtures")

    assert report["summary"] == {"scenarios": 3, "passed": 3, "failed": 0}
    assert report["execution_mode"] == "offline_fixture_only"
    assert cast(dict[str, object], report["mcp"])["summary"] == {
        "checks": 4,
        "passed": 4,
        "failed": 0,
    }
    assert len(cast(list[object], report["limitations"])) == 7


def test_cli_writes_machine_readable_evaluation(tmp_path: Path) -> None:
    output = tmp_path / "evaluation.json"

    result = main(["evaluate", str(ROOT / "fixtures"), "--output", str(output)])

    assert result == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["failed"] == 0


def test_run_cli_persists_trace_and_health(tmp_path: Path) -> None:
    output = tmp_path / "run.json"
    result = main(
        [
            "run",
            str(ROOT / "fixtures" / "01-normal.json"),
            "--database",
            str(tmp_path / "runtime.sqlite"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["health"]["completed"] == 1
    assert payload["trace"][-1]["event"] == "run_completed"


def test_demo_cli_proves_resume_without_repeating_checkpoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["demo"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["interrupted_after"] == "collect_registry"
    assert payload["final_status"] == "completed"
    assert payload["checkpoint_attempts"] == 1
    assert payload["completed_steps"] == 5
    assert payload["artifact_written"] is True


def test_readme_local_links_resolve() -> None:
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", content)
    local_targets = [target for target in targets if "://" not in target]

    assert local_targets
    assert [target for target in local_targets if not (ROOT / target).is_file()] == []
