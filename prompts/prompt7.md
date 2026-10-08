# Prompt 7 — Workflow Creation with DAG Validation (explained)

## 1. The short version

Prompt 7 lets the user **chain registered agents into a workflow**, for example Research → Finance → Fact Checker → Writer. When the workflow is saved, AGEM checks it **once**:

1. every agent **exists** and is **ACTIVE**,
2. there is **no loop** (A waits for B and B waits for A),
3. it works out the **running order** and saves it.

A broken workflow is **never saved**, so it can never reach the Orchestrator (BR-06).

**Hotel picture:** Prompt 5 was the guest register. Prompt 7 is the **tour plan**: "first the museum, then lunch, then the boat". Reception checks the plan once, when it is booked: every guide exists, and no stop says "go after lunch" while lunch says "go after this stop". On the day, the driver just follows the numbered list and never re-plans.

---

## 2. How Prompt 7 connects to earlier prompts

| From | What Prompt 7 uses |
|---|---|
| **Prompt 2** (database) | The `workflows` and `workflow_agents` tables. `step_order`, `depends_on` and `input_mapping` were already columns, just empty until now |
| **Prompt 3** (API skeleton) | The `X-API-Key` check, the error envelope, `StrictModel` (unknown fields → 422), and the existing error code `WORKFLOW_CYCLE_DETECTED` (400) |
| **Prompt 5** (agents) | Steps point to **registered agents**. Unknown agent → 404 `AGENT_NOT_FOUND`; `INACTIVE` agent → 422. And Prompt 5's 409 now matters: an agent used in a saved workflow **cannot be deleted** |
| **Prompt 5** demo agents | The demo workflow Research → Finance → Fact Checker → Writer uses exactly the fields each demo agent returns |

**And forward:**
- **Prompt 8 (Orchestrator)** reads `step_order` and runs steps in that order. A step is ready when every step in its `depends_on` has succeeded. It **never sorts again**.
- Prompt 8 uses `input_mapping` to build each step's input from earlier outputs.
- **Prompt 14** (output drift check) uses the `type` in `input_mapping` to check a resumed step's output still fits the next agent.
- **Prompt 16** (frontend) draws `graph` with React Flow without changing it.

---

## 3. What the task asked for

From `prompts/CLAUDEBUILDPROMPTS`, Prompt 7:

- `POST /api/workflows` (FR-WFL-001), `GET /api/workflows` and `GET /api/workflows/{id}` (FR-WFL-007); the detail returns the DAG in the shape React Flow uses
- **FR-WFL-002** — topological sort; a loop → `400 WORKFLOW_CYCLE_DETECTED`
- **FR-WFL-003** — an unknown `agent_id` is rejected **at creation**, not at run time
- **FR-WFL-004** — sort **once** and store the result in `step_order`
- **FR-WFL-005** — `input_mapping` says which earlier output field feeds this step
- **FR-WFL-006** — `DRAFT → ACTIVE → ARCHIVED`; `ACTIVE` only after validation passes
- **FR-WFL-009** — 1–5 agents per workflow

**Done when:** a loop is rejected before saving, and a valid workflow stores a fixed `step_order`.

---

## 4. Key words

| Word | Meaning |
|---|---|
| **DAG** | "Directed Acyclic Graph": boxes joined by one-way arrows, with **no way to go round in a circle**. A workflow must be a DAG, otherwise some step would wait forever |
| **Cycle / loop** | A → B → A. Nothing in the loop can ever start |
| **Topological sort** | Putting the boxes in an order where every box comes **after** everything it depends on |
| **Kahn's algorithm** | The simple way we do that sort (see Part 3) |

---

## 5. Decisions we made (the docs did not say)

| Question | Decision | Why |
|---|---|---|
| How do steps refer to each other in the request? | A short **`key`** you choose (`"research"`) | Database ids don't exist yet when the request is sent. AGEM swaps keys for `workflow_agent_id`s when it saves |
| What does `input_mapping` look like? | `"revenue": {"from": "research", "field": "revenue", "type": "number"}` | Says **where** the value comes from and **what type** it must be. The type is needed later by the drift check (Prompt 14) |
| What can `from` be? | A step in this step's `depends_on`, or `"input"` (the starting input) | Only a direct dependency is **guaranteed** to have finished first |
| Error for an unknown agent | `404 AGENT_NOT_FOUND` (existing code) | No new code needed |
| Error for an `INACTIVE` agent | `422 VALIDATION_ERROR` | The agent exists, but the request is not allowed |
| Step depends on itself | `400 WORKFLOW_CYCLE_DETECTED` | It is a loop of length one, so same answer as any other loop |
| `DRAFT` status | Never actually stored | Validation happens **before** saving, so an invalid workflow is never saved, and a saved one is `ACTIVE` straight away |

Written down in `docs/api-spec.md` → "Creating a workflow".

---

## 6. Everything that was built, part by part

### Part 1 — Request and response shapes
**File:** `backend/app/schemas/workflow.py`

What you send:

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

Checks that happen **before** any database work (all give 422):
- 1 to 5 steps
- step keys are unique, and `"input"` can't be used as a key
- every `depends_on` names a step that exists, with no duplicates
- every `input_mapping` source is `"input"` or one of the step's own dependencies
- `type` is one of `string, number, integer, boolean, object, array, any`

### Part 2 — The endpoints
**File:** `backend/app/api/workflows.py`

```
POST /api/workflows
   1. topological sort            → loop?            400 WORKFLOW_CYCLE_DETECTED
   2. check agents                → unknown?         404 AGENT_NOT_FOUND
                                  → INACTIVE?        422 VALIDATION_ERROR
   3. give each step an id, swap keys for ids
   4. save workflow (ACTIVE) + one workflow_agents row per step, with step_order 1, 2, 3…
   → 201 with the full detail

GET /api/workflows          → list: id, name, status, step_count
GET /api/workflows/{id}     → detail: steps + graph, or 404 WORKFLOW_NOT_FOUND
```

Nothing is written until **all** checks pass. That's how "a broken workflow can never reach the Orchestrator" is guaranteed.

### Part 3 — The topological sort (Kahn's algorithm)
**Function:** `topological_order()` in `workflows.py`

The idea in plain words:

```
repeat:
    find every step that is waiting for nothing      → "ready"
    if nothing is ready but steps are left            → LOOP → 400
    give the ready steps the next numbers in order
    cross them off everyone else's waiting list
until no steps are left
```

**Example** with the demo workflow:

| Round | Waiting for nothing | Order so far |
|---|---|---|
| 1 | research | research |
| 2 | finance | research, finance |
| 3 | checker | …, checker |
| 4 | writer | …, writer |

**Loop example:** a waits for b, b waits for a. Round 1: nothing is ready, but two steps are left → `400 WORKFLOW_CYCLE_DETECTED`, details `{"steps_in_cycle": ["a", "b"]}`.

When two steps are ready at the same time (siblings), they keep the order you listed them in. That makes the result **the same every time**.

### Part 4 — What is stored
For each step, one `workflow_agents` row:

| Column | Example |
|---|---|
| `step_order` | `2` |
| `depends_on` | `["<research's workflow_agent_id>"]` |
| `input_mapping` | `{"revenue": {"from": "<research's id>", "field": "revenue", "type": "number"}}` |

`workflows.definition` keeps the original request (with keys), so the detail view can show the keys again.

### Part 5 — The graph for React Flow
`GET /api/workflows/{id}` returns:

```json
"graph": {
  "nodes": [ { "id": "<step id>", "position": {"x": 0, "y": 0},
               "data": {"label": "Research Agent", "key": "research", "agent_id": "...", "step_order": 1} } ],
  "edges": [ { "id": "<a>-><b>", "source": "<a>", "target": "<b>" } ]
}
```

`nodes`, `edges`, `id`, `position`, `data`, `source` and `target` are exactly the names React Flow expects, so the frontend passes them straight in. Positions are a simple layout: each "level" of dependency is one column further right (250 px), and siblings stack downward (120 px).

---

## 7. How it was checked

**Tests — 152 pass (18 new for workflows):**
- A 3-step workflow sent **out of order** (writer first) is saved as research 1, finance 2, writer 3. Ids, `depends_on` and `input_mapping` are stored as ids. Detail and list match
- Diamond (research → finance and checker → writer): correct edges, siblings in the same column, research first and writer last
- Loops `a↔b`, `a→c→b→a` and `a→a` → 400, and **nothing saved**
- Unknown agent → 404 and nothing saved; `INACTIVE` agent → 422
- 0 or 6 steps → 422; 5 steps → 201
- Duplicate key, unknown dependency, mapping from a non-dependency, reserved key `input`, duplicate dependency, unknown type → 422
- Unknown workflow → 404
- An agent used in a saved workflow can't be deleted → 409

**Checked on real PostgreSQL (Docker):**
- The full demo workflow Research → Finance → Fact Checker → Writer, sent out of order → 201 `ACTIVE`, order `research 1, finance 2, checker 3, writer 4`, graph with 4 nodes and 3 edges
- Read back → identical
- A two-step loop → 400 `WORKFLOW_CYCLE_DETECTED` with `steps_in_cycle: [a, b]`
- Deleting the Finance agent now → 409 `AGENT_IN_USE`
- In the database, `workflow_agents` holds `step_order` 1–4, with `depends_on` and `input_mapping` as ids

This demo workflow (**"Investment report"**) is left in your database, ready for Prompt 8 to run.

---

## 8. What Prompt 7 does NOT do (comes later)

- **Running** a workflow (`POST /api/workflows/{id}/executions`) → **Prompt 8**
- **Archiving** a workflow (`ACTIVE → ARCHIVED`): no endpoint in the Prompt 7 list, so not built yet
- The **Create Workflow form** and the React Flow picture → **Prompt 16** (frontend)
- Conditional / branching steps (FR-WFL-010) → optional "L" prompt

---

## 9. Files changed in Prompt 7

| File | New / changed |
|---|---|
| `backend/app/schemas/workflow.py` | new: request/response shapes and step checks |
| `backend/app/api/workflows.py` | changed: the 3 endpoints, the topological sort, the graph |
| `backend/tests/test_api.py` | changed: 18 new tests |
| `docs/api-spec.md` | changed: "Creating a workflow" section |
| `prompts/prompt7.md` | new: this file |

---

## 10. Check questions

1. Why is the order worked out **once at creation** instead of every time the workflow runs?
2. In Kahn's algorithm, how do we know there is a loop?
3. Why must `from` in `input_mapping` be one of the step's own `depends_on`, and not any earlier step?
4. Why does the request use `key`s like `"research"` instead of database ids?
5. What does the `type` in `input_mapping` get used for later?
