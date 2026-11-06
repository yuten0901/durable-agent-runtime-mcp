# MCP tool surface

The demo exposes a narrow MCP server over standard input/output. It has no network listener and
does not need credentials. Start it from the repository root:

```bash
agent-runtime-demo mcp \
  --fixtures fixtures \
  --database reports/mcp-runtime.sqlite \
  --workspace workspace/mcp
```

Each request and response is a single JSON-RPC object followed by a newline. A client initializes
the session and discovers the five tools through `tools/list`.

| Tool | Purpose | Side effect |
|---|---|---|
| `list_fixtures` | list allowlisted local scenarios | none |
| `start_workflow` | create and execute a durable run | SQLite checkpoints and sandboxed JSON |
| `resume_workflow` | continue pending steps from a stored checkpoint | same as start |
| `get_run_status` | inspect run and step state | none |
| `get_run_trace` | inspect ordered audit events | none |

## Executable session example

The following newline-delimited transcript uses the server's required initialization contract.
Send the `initialize` request first, wait for its response, and then send the
`notifications/initialized` notification before listing or calling tools. The notification has no
response by design.

```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"example-client","version":"1.0.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"start_workflow","arguments":{"fixture_id":"normal-multi-agent-run","simulate_process_loss_after_step":"collect_registry"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"resume_workflow","arguments":{"run_id":"run-normal-001"}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_run_trace","arguments":{"run_id":"run-normal-001"}}}
```

The controlled interruption is a demo-only argument. The checkpoint is committed before the
exception is raised; `resume_workflow` reconstructs the runtime from the fixture identity stored in
SQLite and does not rerun completed work. Identity includes canonical fixture JSON and its SHA-256,
not only the human-readable fixture ID. Content changes after interruption cause a fail-closed tool
error before another step runs.

## Security boundary

- A tool caller supplies a fixture ID, never a filesystem path.
- The fixture catalog reads only `*.json` files directly below the configured fixture directory.
- Output remains under the configured workspace and only JSON artifacts are accepted.
- SQL parameters are bound and run identity is loaded from the durable store during resume.
- Every tool rejects undeclared properties and validates required string fields at runtime, matching
  its advertised `inputSchema`.
- Tools are unavailable until `initialize` succeeds and the client sends
  `notifications/initialized`; invalid request IDs and non-object JSON are rejected as Invalid
  Request rather than treated as notifications or parse failures. An object without `id` is only a
  notification when its `jsonrpc`, `method`, and optional structured `params` fields are valid;
  malformed objects receive `-32600` with `id: null`, while valid notifications receive no response.
- There is no socket, remote authentication, external API call, arbitrary command or arbitrary code
  execution surface.

This is evidence for tool schema design, durable execution and failure semantics under protocol
version `2025-06-18`. It is not evidence for a remotely exposed MCP service, OAuth, multi-tenant
authorization, other protocol generations or production client compatibility.
