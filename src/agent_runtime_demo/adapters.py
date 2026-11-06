from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path


class TransientActorError(RuntimeError):
    """外部actor相当の一時障害。"""


class SandboxViolation(RuntimeError):
    """sandbox policyに反するtool要求。"""


class LocalActorClient:
    """Apify Actorのstart/run/dataset境界を再現するローカルfixture adapter。"""

    def __init__(
        self,
        sources: dict[str, list[dict[str, str]]],
        transient_failures: dict[str, int],
    ) -> None:
        self._sources = sources
        self._remaining_failures = dict(transient_failures)
        self._run_ids: dict[str, str] = {}

    def run_actor(self, actor_name: str, *, idempotency_key: str) -> dict[str, object]:
        remaining = self._remaining_failures.get(actor_name, 0)
        if remaining > 0:
            self._remaining_failures[actor_name] = remaining - 1
            raise TransientActorError(f"{actor_name}:temporary_unavailable")
        run_id = self._run_ids.setdefault(
            idempotency_key,
            f"local-actor-{sha256(idempotency_key.encode()).hexdigest()[:10]}",
        )
        return {
            "actor_run_id": run_id,
            "status": "SUCCEEDED",
            "dataset": self._sources.get(actor_name, []),
        }


class FileSandbox:
    """allowlist済みJSON artifactだけを専用workspaceへ書くsandbox。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative_path: str, payload: dict[str, object]) -> Path:
        target = (self._root / relative_path).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise SandboxViolation("path_escape_denied") from error
        if target.suffix != ".json":
            raise SandboxViolation("only_json_artifacts_allowed")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target
