# AGEM — Agent Workflow & Capability Evolution Platform

AGEM orchestrates workflows across agents built in any framework, and when a
step fails because an agent is *missing a capability*, it diagnoses the gap,
builds the missing tool, tests it in a sandbox, verifies it, registers it and
resumes the exact step that failed.

See [docs/Architecture.md](docs/Architecture.md) and [docs/FRS .md](docs/FRS%20.md).

## Running it

Requires Docker with Compose. One machine, one command (FR-DEP-007):

```bash
cp .env.example .env     # then fill in API_KEY, LLM_API_KEY, FERNET_KEY
./scripts/run_dev.sh
```

That brings up all four services (FR-DEP-001):

| Service | Port | Role |
|---|---|---|
| `frontend` | 5173 | React SPA — polls the API, holds no state |
| `backend` | 8000 | FastAPI + Orchestrator + Master Agent + Capability Engine, one process |
| `postgres` | 5432 | Single source of truth |
| `sandbox` | — | Minimal image, no network; a fresh container runs per capability test |

Health check: `curl -H "X-API-Key: $API_KEY" http://localhost:8000/health`

## Layout

See Architecture §25. `backend/orchestrator/` is the brain,
`backend/capability_engine/` is the gap-resolution pipeline (one file per
stage), `backend/adapters/` is the interoperability contract, and
`sandbox_runner/` is the only place untrusted code ever runs.
