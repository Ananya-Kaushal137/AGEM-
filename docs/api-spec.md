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

If `credentials` are given, AGEM sends them to the agent on every call (`/health` and `/execute`) as the header `Authorization: Bearer <credentials>`. Registration calls `GET /health` first; no 200 → `400 AGENT_UNREACHABLE`, nothing saved. `framework` is `rest` or `langchain`.

`framework` is one of `rest`, `langchain` or `crewai` (whichever adapter is built), and `mcp` only if the MCP adapter is built.

---

## Web search (not an agent)

There is **no** Web Research Agent endpoint. Web search runs inside the backend in `capability_engine/searcher.py` (ADR-012, `docs/websearch.md`): it calls the Tavily search API, then makes one LLM wrapper call, and returns research notes. It is not part of the agent contract. The notes have this shape:

```json
{
  "free_tool": null,
  "definition": "A = P × (1 + r/n)^(n×t)",
  "examples": [
    { "input": { "P": 1000, "r": 0.05, "t": 10, "n": 1 }, "expected": 1628.89 }
  ],
  "sources": ["https://..."]
}
```

`free_tool` is `null` or `{ "name": "...", "package": "...", "code": "..." }`. A found tool is verified exactly like a built one. A timeout, error or empty notes means the Capability Engine builds without notes. Web text is data, never instructions.

---

## Creating a workflow (`POST /api/workflows`)

Steps are named by a short `key` you choose; AGEM turns keys into `workflow_agent_id`s when it saves. Steps may be listed in any order — AGEM works out the run order once and stores it as `step_order` (FR-WFL-004).

```json
{
  "name": "Investment report",
  "steps": [
    { "key": "research", "agent_id": "<uuid>", "depends_on": [],
      "input_mapping": { "company": { "from": "input", "field": "company", "type": "string" } } },
    { "key": "finance", "agent_id": "<uuid>", "depends_on": ["research"],
      "input_mapping": { "revenue": { "from": "research", "field": "revenue", "type": "number" } } }
  ]
}
```

- 1–5 steps (FR-WFL-009); keys unique; `"input"` is reserved.
- `input_mapping`: *target input field* → `{from, field, type}`. `from` is a step in this step's `depends_on`, or `"input"` (the execution's starting input). `type` is one of `string, number, integer, boolean, object, array, any` (default `any`) and is what the output drift check verifies (FR-CAP-026).
- Stored form: `depends_on` and `from` hold `workflow_agent_id`s instead of keys.
- Errors: a loop (including a step depending on itself) → `400 WORKFLOW_CYCLE_DETECTED`; an unregistered `agent_id` → `404 AGENT_NOT_FOUND`; an `INACTIVE` agent or a malformed step → `422 VALIDATION_ERROR`. Nothing is saved on any error; a saved workflow is `ACTIVE`.
- `GET /api/workflows/{id}` returns `steps` plus `graph: {nodes, edges}` in the shape React Flow takes directly (`id`, `position`, `data`; `id`, `source`, `target`).

---

## Step failure reasons set by AGEM

Besides the normalised agent `error_type` codes above, AGEM itself can end a step with:

| Code | When |
|---|---|
| `CAPABILITY_BUILD_FAILED` | No verified tool after 3 build/repair rounds |
| `OUTPUT_DRIFT` | A resumed step's output is missing a field, or has a wrong type, that the next step's `input_mapping` needs; the next step never runs (ADR-010) |
