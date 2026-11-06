from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from .models import WorkflowFixture


def load_fixture(path: Path) -> WorkflowFixture:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WorkflowFixture(
        fixture_id=payload["fixture_id"],
        run_id=payload["run_id"],
        expected_status=payload["expected_status"],
        sources=payload["sources"],
        transient_failures=payload.get("transient_failures", {}),
        output_path=payload.get("output_path", "event-report.json"),
    )


def canonical_fixture_json(fixture: WorkflowFixture) -> str:
    """runへ束縛するfixture内容を決定的JSONへ正規化する。"""
    return json.dumps(
        asdict(fixture),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fixture_digest(fixture: WorkflowFixture) -> str:
    return sha256(canonical_fixture_json(fixture).encode("utf-8")).hexdigest()
