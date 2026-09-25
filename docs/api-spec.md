# AGEM — API Specification

REST over `/api/v1`, `X-API-Key` on every request (Architecture §16).
The AGEM endpoint list is written out in Prompt 3 onwards.

---

## Agent contract (AGEM → agent) — FROZEN

Every registered agent must answer these two calls (Architecture §16.1, ADR-009).
Changing this contract requires agreement from both team members.

### `GET {endpoint}/health`

Returns `200 OK`. Called once when the agent is registered; if it fails, registration is rejected.

### `POST {endpoint}/execute`

Request:

```json
{
  "task": "Create a company investment report",
  "input": { "...": "the step's input, built from upstream outputs" },
  "context": {
    "tool_results": { "calculate_compound_interest": 1628.89 }
  }
}
```

`context.tool_results` is only present when a paused step resumes after a verified capability (ADR-008).

Success response:

```json
{ "status": "SUCCEEDED", "output": { "...": "..." } }
```

Failure response when the agent lacks a tool:

```json
{ "status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest" }
```

Any other failure (non-2xx status, timeout, unreachable, malformed JSON, or a `FAILED` reply with another `error`) is normalised by `step_executor.py` into:

```json
{ "status": "FAILED", "error_type": "<code>", "raw_error": "...", "capability": "<only if named>" }
```

where `<code>` is one of `MISSING_CAPABILITY`, `TIMEOUT`, `CONNECTION_ERROR`, `HTTP_5XX`, `INVALID_JSON`, `AGENT_ERROR`.

### Registering an agent with AGEM

```json
POST /api/agents
{
  "name": "Finance Agent",
  "framework": "rest",
  "endpoint": "http://localhost:9002",
  "credentials": "optional — Fernet-encrypted at rest, never returned"
}
```

`framework` is one of `rest`, `langchain` or `crewai` (whichever adapter is built), and `mcp` only if the MCP adapter is built.
