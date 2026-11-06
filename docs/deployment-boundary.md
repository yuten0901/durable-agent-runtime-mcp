# Deployment boundary

The repository intentionally stops before external deployment. The current proof is portable Python
plus SQLite and a local MCP stdio process, with no network listener, account, secret or public URL.

| Concern | Demonstrated locally | Not yet demonstrated |
|---|---|---|
| orchestration | dependency-aware five-agent plan | distributed queue or autoscaling |
| durability | per-step checkpoint, fixture snapshot/digest binding and object reconstruction | OS process-death recovery; Cloudflare D1 migration/concurrency |
| actor operations | local run ID, dataset, retry injection | real Apify API/auth/billing |
| sandbox | path containment and JSON allowlist | OS/container isolation for arbitrary code |
| observability | append-only structured trace and health counts | hosted metrics, alerts and retention |
| MCP | 2025-06-18 JSON-RPC stdio lifecycle, schema enforcement and fail-closed resume | other versions, remote transport, auth and client matrix |
| UI | machine-readable CLI output | Next.js operator console |
| deployment | reproducible local command | Worker/Vercel/cloud deployment and rollback |

## Possible adapters, not claims

- An Apify adapter can replace `LocalActorClient` while preserving idempotency and retry semantics.
- A D1 store can replace `DurableStore` after SQL, transaction and concurrency compatibility tests.
- A Cloudflare Worker can expose claim/tick endpoints after authentication, rate limits and lease
  ownership are defined.
- A Next.js UI can consume read-only run/step/trace views after access control and redaction are
  specified.
- A remote MCP transport can be considered only after authentication, tenant isolation, rate limits
  and deployment-specific client compatibility tests are defined.

None of those adapters is present or implied by the local evaluation result.
