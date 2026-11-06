# Architecture and invariants

## Components

`AgentRuntime` is a deterministic coordinator. It selects one ready step per tick, claims it,
invokes its adapter, commits the result and appends a trace. The plan contains two independent
collector subagents followed by analyst, verifier and publisher agents.

`DurableStore` owns three SQLite tables:

- `runs`: terminal and active workflow state;
- `steps`: dependency list, attempt count, result checkpoint and failure reason;
- `traces`: append-only structured events ordered by sequence.

`LocalActorClient` models the boundary needed by an Apify integration: actor name, idempotency key,
stable run ID, terminal status and dataset. `FileSandbox` resolves every requested path beneath its
workspace and accepts only JSON artifacts.

`AgentRuntimeMcpServer` exposes the runtime through MCP JSON-RPC over stdio. The transport accepts
one JSON object per line, supports initialization, ping, tool discovery and tool calls, and flushes
one response per request. Tool callers select fixtures by an allowlisted `fixture_id`; they cannot
pass a filesystem path. The durable store binds each `run_id` to its fixture identity, allowing a
new server/runtime object to resume pending work without trusting a caller-supplied fixture path.
The `runs` row stores the fixture ID, canonical JSON snapshot and SHA-256 digest. Both ordinary
runtime resume and MCP resume compare the current fixture against that binding before executing a
pending step, so a run cannot combine checkpoints from different fixture generations.

## Invariants

1. A step is eligible only when every declared dependency is completed.
2. A completed step is never claimed again for the same `run_id` and `step_id`.
3. An actor retry uses the same idempotency key and is capped at three attempts.
4. A result checkpoint is committed before the controlled interruption hook can fire.
5. A path outside the sandbox root is denied before any file is written.
6. A failed step makes the run terminal; downstream steps do not execute.
7. Every attempt, retry, completion and failure has a structured trace event.
8. Stale running work is explicitly returned to pending; it is not silently treated as successful.
9. MCP workflow start accepts only catalogued fixture IDs and rejects duplicate run creation.
10. MCP resume derives the fixture from the durable store and rejects ID, snapshot or digest mismatch.
11. Tool arguments enforce the same required fields, types and additional-property policy published
    by `tools/list`.
12. Tool calls remain unavailable until a valid initialize exchange and initialized notification.

## Concurrency boundary

The demo executes one step per runtime scheduling tick. SQLite is sufficient for the deterministic local proof,
but this is not evidence of distributed locking or high-concurrency safety. A production adapter
must use compare-and-swap claims, lease ownership and the target database's transaction guarantees.

## MCP interoperability boundary

The MCP surface is intentionally limited to tools and stdio. It does not implement resources,
prompts, sampling, HTTP/SSE transport, authorization or client-specific extensions. The automated
suite verifies the server's JSON-RPC shape, version negotiation, request IDs, initialization state,
tool schemas, notifications, workflow execution, trace query, invalid fixture denial, changed-fixture
denial and checkpoint resume from a new server instance. A real client
compatibility matrix remains deployment evidence, not a claim of this local proof.
