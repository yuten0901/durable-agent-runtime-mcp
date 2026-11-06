# Evaluation

The evaluator creates a fresh database and sandbox for every fixture, executes the workflow to a
terminal state, and records expected/actual status, completed steps, retries and trace count.

| Fixture | Expected | What it proves |
|---|---|---|
| normal multi-agent run | completed | dependency handoff, dedupe, verify, publish |
| actor retry and recovery | completed | two transient failures, bounded retry, stable identity |
| sandbox path escape | failed | fail-closed policy and no escaped artifact |

Run:

```bash
agent-runtime-demo evaluate fixtures --output reports/evaluation.json
pytest -q
```

The automated suite separately checks controlled-interruption-and-resume, completed-run idempotency, stale-lease
recovery, retry exhaustion, actor run identity, sandbox extension policy, CLI output and health.
It also verifies MCP initialization and tool discovery, protocol counter-offer, request-ID and
JSON-RPC error handling, runtime enforcement of all published tool schemas, end-to-end tool
execution, status and trace queries, a new server object resuming from a committed checkpoint,
changed-fixture fail-closed behavior, fixture path denial and stdio notification handling.

The generated `reports/evaluation.json` contains four executable MCP checks in addition to the
three workflow fixtures. Its limitations explicitly separate this local protocol proof from real
MCP client compatibility, remote transport, authorization and external data immutability.

The interruption test raises an exception and reconstructs runtime/store or server objects inside
one Python process. It does not directly exercise OS subprocess termination; that is intentionally
left in the production-evidence list below.

## Production evidence still required

- a consented Apify test using a test actor, dataset and injected/observed failure;
- multi-hour or multi-day soak runs with memory, queue age and completion SLOs;
- concurrent workers competing for the same step lease;
- actual LLM tool-call validation and adversarial prompt/tool inputs;
- target deployment rollback, secret injection and audit-log retention;
- D1 behavior under its real transaction, size and concurrency constraints;
- a real operator UI only if the selected deployment requires one.
- compatibility runs against the specific MCP clients selected for deployment;
- recovery after actual OS process termination using a separately started process;
- protocol generations other than `2025-06-18` with the selected clients;
- authorization and transport security if the server moves beyond local stdio.
