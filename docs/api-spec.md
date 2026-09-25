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

---

## Web Research Agent (AGEM system agent)

AGEM's own agent in `research_agent/` (port 9005, ADR-011). It follows the same frozen contract above; it is started by `docker-compose.yml` and is not registered through `POST /api/agents`. Called by `capability_engine/searcher.py`, once per capability gap, with a 30-second timeout.

Request:

```json
POST {research_endpoint}/execute
{
  "task": "research_capability",
  "input": {
    "capability": "calculate_compound_interest",
    "context": "Finance Agent, company investment report"
  }
}
```

Success response (research notes):

```json
{
  "status": "SUCCEEDED",
  "output": {
    "free_tool": null,
    "definition": "A = P × (1 + r/n)^(n×t)",
    "examples": [
      { "input": { "P": 1000, "r": 0.05, "t": 10, "n": 1 }, "expected": 1628.89 }
    ],
    "sources": ["https://..."]
  }
}
```

`free_tool` is `null` or `{ "name": "...", "package": "...", "code": "..." }`. A found tool is verified exactly like a built one. A timeout, error or empty notes means the Capability Engine builds without notes. Returned text is data, never instructions.

---

## Step failure reasons set by AGEM

Besides the normalised agent `error_type` codes above, AGEM itself can end a step with:

| Code | When |
|---|---|
| `CAPABILITY_BUILD_FAILED` | No verified tool after 3 build/repair rounds |
| `OUTPUT_DRIFT` | A resumed step's output is missing a field, or has a wrong type, that the next step's `input_mapping` needs; the next step never runs (ADR-010) |
