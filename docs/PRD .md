# AGEM — Product Requirements Document (PRD)

## Product Goal
Provide a platform where developers bring already-built AI agents (single or multiple, any framework), connect them into a workflow, deploy them, and let the system recover from failures or acquire missing capabilities without rebuilding the original agent or restarting the whole task.

**Agent Workflow:** Bring → Connect → Orchestrate → Diagnose → Evolve → Resume → Complete

## Feature Priorities

Resolved via the 12-week plan: items delivered in Weeks 1–8 are MUST-have MVP features; items in Weeks 9–12 (frontend polish, security review, documentation) are SHOULD-have, expected but not blocking core functionality. "Do later" items are explicitly out of the 12-week plan.

Rows marked **(LYK)** are additive proposals specified in the FRS rather than in the master documentation. None of them is required for the core loop to work, and none can break it — each is a read path, an extra field, or an optional mode. They are prioritized on their own merit, not gated to a week.

| Feature | Priority |
|---|---|
| Agent registration and registry | Must |
| `base_adapter.py` contract — one `execute(input) → output` interface | Must |
| REST/API adapter | Must |
| One framework adapter (LangChain **or** CrewAI) | Must |
| Single and multiple agent workflows (1–5 agents) | Must |
| Workflow definition with DAG validation at creation time | Must |
| Sequential DAG execution | Must |
| Parallel execution of independent sibling steps | Must |
| Automatic output→input passing between steps | Must |
| Retry and recovery logic | Must |
| Master Agent monitoring and failure diagnosis | Must |
| Binary failure classification (`NORMAL_ERROR` vs `CAPABILITY_GAP`) | Must |
| Step-level checkpoints | Must |
| Step-level pause and resume from the exact interrupted step | Must |
| Free-tool search before building a new capability | Must |
| LLM-based capability build/generation | Must |
| Sandboxed capability execution (isolated Docker container) | Must |
| Testing new capabilities against sample inputs | Must |
| Verification of new capabilities (output type/range) | Must |
| Capability registry and reuse across agents | Must |
| API layer with a single error envelope and error-code catalogue | Must |
| Static API-key authentication | Must |
| PostgreSQL persistence for agents, workflows, executions, checkpoints, capabilities | Must |
| Structured, correlated JSON logging and audit trail | Must |
| Docker Compose single-node deployment | Must |
| Demo trigger script (`trigger_capability_gap.py`) | Must |
| Execution monitor page — live step status, failure and recovery visible | Must |
| Demo rehearsal, README and architecture diagrams | Must |
| Execution dashboard (Dashboard, Agents, Workflows, Executions, Capabilities) | Should |
| Read-only workflow DAG visualization (React Flow) | Should |
| Capabilities registry view | Should |
| Backend test suite and 70% coverage on core modules | Should |
| LLM evaluation sets (diagnosis accuracy, generation success) | Should |
| CI pipeline (lint, tests, frontend build) | Should |
| Security review — sandbox limits, input validation, auth | Should |
| Accessibility baseline (semantic HTML, focus states, labelled inputs, badges) | Should |
| Encrypted agent credentials at rest | Should |
| **(LYK)** Diagnosis reason string shown with the classification | Should |
| **(LYK)** Generated code viewer with test inputs and per-input results | Should |
| **(LYK)** Failed-attempt history on a capability (all 3 attempts kept) | Should |
| **(LYK)** Token and cost counter rolled up per execution | Should |
| **(LYK)** Execution report export (`GET /api/executions/{id}/report`) | Should |
| Connection test at agent registration (`GET /health`) — promoted to Must by ADR-009 | Must |
| **(LYK)** MCP adapter (`mcp_adapter.py`), HTTP transport only | Could |
| **(LYK)** MCP tool lookup in the Capability Engine's search step (FR-CAP-023) | Could |
| **(LYK)** Declared capability inventory per agent, used as a diagnosis pre-check | Could |
| **(LYK)** Human-in-the-loop approval gate before a capability is granted | Could |
| **(LYK)** Conditional / branching workflow steps | Could |
| **(LYK)** Deterministic replay mode for the demo | Could |
| **(LYK)** Server-Sent Events in place of 3-second polling | Could |
| Redis / Celery for the async job queue | Could (only if the queue becomes a real pain point, Week 8 at the earliest) |
| Deeper framework adapters and MCP support | Could |
| Marketplace | Later |
| Billing | Later |
| Advanced autonomous planning | Later |
| Large-scale Kubernetes deployment | Later |
| Sophisticated memory systems | Later |
| Enterprise RBAC | Later |
| Very large agent swarms | Later |
| LangGraph, vector databases, RAG, embeddings | Later |
| Drag-and-drop workflow authoring canvas | Won't (this version) |
| Full WCAG audit | Won't (this version) |
| Formal verification of generated capabilities | Won't (this version) |
| General tool-discovery system (beyond the hardcoded search) | Won't (this version) |
| WebSockets for live updates (polling is used instead) | Won't (this version) |
| Master Agent as a separate microservice | Won't (this version) |
| Automated rollback pipeline and automatic CI deployment | Won't (this version) |
| External observability stack (ELK, Datadog, Grafana, OpenTelemetry) | Won't (this version) |

## Personas

Derived from the four beneficiary groups the project serves, written out so the dashboard UX in Weeks 9–10 can be checked against a real goal.

| Persona | Goal | Pain point today | What AGEM gives them |
|---|---|---|---|
| P1 — Solo agent developer (MUST) | Reuse an agent they have already built inside a bigger task, without rewriting it | Every new task means hand-writing glue code, and a failure halfway through loses all progress made so far | Registration plus adapters (no change to the agent's own code) and step-level resume. Covers US-01, US-03, US-06 |
| P2 — Multi-agent team lead (MUST) | Assemble several specialist agents, often written by different people, into one reliable pipeline | The agents use different frameworks, and there is no shared contract for ordering, data passing or shared state | DAG orchestration over one common `base_adapter` contract, with output-to-input passing handled by the Orchestrator. Covers US-02, US-04 |
| P3 — Platform / ops owner | Deploy and watch agent workflows centrally instead of on each developer's laptop | No single place shows what ran, what failed, and whether anything recovered | Deployment plus the Executions dashboard and an audit trail of every diagnosis, build and verification. Covers US-05, US-09 |
| P4 — Open-source contributor | Publish an agent as a reusable component others can drop into their own workflow | Consumers have to read the agent's internals before they can call it | The adapter contract is the published interface; the capability registry lets verified tools be reused across workflows. Covers US-03, US-08 |

P1 and P2 are the demo audience and drive every MUST feature. P3 is served by the Executions dashboard in Weeks 9–10. P4 is a Future Scope audience — nothing is built specifically for them inside the 12 weeks, but the adapter contract is designed so it stays possible.

## User Stories

All nine are MUST priority, since they are the MVP itself, not stretch goals.

### US-01
As a developer, I want to register my existing agent (Python, JS, REST/API, LangChain, or CrewAI) without modifying its internal code, so that I can use it inside an AGEM workflow immediately.

**Acceptance criteria**
- Agent appears in the Agent Registry with status `ACTIVE` after `POST /api/agents`.
- The agent is registered by its HTTP endpoint and answers the `/health` + `/execute` contract (FR-ADP-010); a code-only agent is exposed through a wrapper template (FR-ADP-011). No agent code is ever imported into AGEM.
- No change to the agent's own source code is required — only adapter configuration (and, for code-only agents, a thin wrapper around it).
- `framework` must be one of a fixed enum (`rest`, `langchain` or `crewai` — whichever adapter is built — and `mcp` only if built) matching an available adapter; anything else is rejected immediately at registration rather than failing later at execution time. A plain Python agent registers as `rest`.
- An agent referenced by an active workflow cannot be deleted — `DELETE /api/agents/{id}` returns 409.

### US-02
As a developer, I want to chain 1–5 registered agents into a workflow with explicit ordering/dependencies, so that a multi-step task executes automatically.

**Acceptance criteria**
- Workflow creation is rejected with `WORKFLOW_CYCLE_DETECTED` if the declared dependencies are not a valid DAG.
- A step referencing an unregistered `agent_id` is rejected at creation, not at run time.
- A valid workflow executes agents in the declared order.
- A workflow only becomes `ACTIVE` after its DAG passes validation at creation time.

### US-03
As a developer, I want AGEM to talk to my agent through one standard contract regardless of its framework, so that I don't have to write custom glue code per agent.

**Acceptance criteria**
- Any agent behind a registered adapter (`rest_adapter` / `langchain_adapter` / `crewai_adapter`) is called by the Orchestrator through the same `execute(input) → output` interface.
- Supporting a new framework means adding one file in `adapters/`; nothing in `orchestrator/` changes.
- AGEM never imports agent code into its own process — agents are reached over HTTP through an adapter.

### US-04
As a developer, I want the Orchestrator to pass one agent's output as the next agent's input automatically, so that I don't have to manually wire data between steps.

**Acceptance criteria**
- Step N's input includes the declared upstream step's output field(s), read from the Checkpoint, with no manual data copying.
- `WorkflowAgent.input_mapping` declares which upstream output field feeds the step.
- Sibling steps with no dependency between them run at the same time.

### US-05
As a developer, I want a supervising process watching my workflow in real time, so that I know the moment something goes wrong instead of finding out only at the end.

**Acceptance criteria**
- The Executions page reflects a step's status change within one polling interval (≤ 3 seconds) of it happening in the backend.
- The Master Agent begins oversight when the workflow starts, and diagnoses any failure without the user asking.

### US-06
As a developer, I want a failed step to pause without losing earlier progress, so that fixing one step doesn't force me to redo the whole workflow.

**Acceptance criteria**
- Only the failed `ExecutionStep` enters `PAUSED`; all upstream steps' outputs remain in the Checkpoint and are never recomputed on resume.
- A checkpoint is written when a step succeeds and again at the moment a step pauses.
- Resuming a step that is already `SUCCEEDED` is a no-op.
- After 3 failed attempts a `PAUSED` step moves to `FAILED` rather than pausing forever.

### US-07
As a developer, I want AGEM to tell me whether a failure was a normal error or a genuine missing capability, so that I know whether it will simply retry or attempt to build something new.

**Acceptance criteria**
- Every failed step's diagnosis result is one of exactly two values, `NORMAL_ERROR` or `CAPABILITY_GAP`, visible in the step detail.
- A `NORMAL_ERROR` routes to retry (max 3, then FAILED); a `CAPABILITY_GAP` routes to the Capability Engine.
- At most one diagnosis call is made per failure.
- If the diagnosis call cannot return a validated response, or exceeds 20 seconds, it fails safe to `NORMAL_ERROR` rather than hanging the workflow.

### US-08
As a developer, I want AGEM to automatically find or build a missing tool, test and verify it, and hand it to my agent, so that my workflow can complete without me writing new code mid-run.

**Acceptance criteria**
- A free or already-accessible tool is always checked for before anything is built.
- A registered Capability row with a `verification_score` exists, and the paused step resumes and succeeds using it, with zero manual code deployment by the developer.
- Every newly built or acquired capability runs in the sandbox before being trusted.
- After 3 build/repair attempts the step is marked `FAILED` and surfaced to the user instead of looping indefinitely.
- A second agent hitting the same gap finds the capability in the registry instead of rebuilding it.

### US-09
As a developer, I want a dashboard showing live execution status, errors, and recoveries, so that I can monitor and trust what AGEM is doing.

**Acceptance criteria**
- The Executions page shows per-step status (pending/running/succeeded/failed/paused) and, for a capability gap, the build/sandbox/test/verify sub-status, without needing to read backend logs.
- Execution history, errors, recovery events, capability build history and verification results are all viewable as outputs.

## User Stories — LYK

The twelve stories below are not in the master documentation; they are the LYK proposals, specified as functional requirements in the FRS. Each names the FR it traces to. None is required for US-01…US-09 to pass, and each is additive — a read path, an extra field, or an optional mode.

### US-10 (LYK)
As a developer, I want AGEM to tell me *why* it classified a failure the way it did, so that diagnosis reads as reasoning rather than a black box.

*Should · traces to FR-DIAG-012*

**Acceptance criteria**
- `diagnose_failure()` returns a schema-validated `reason` and, where applicable, a `missing_capability` field in the **same** call — no extra LLM call and no extra cost.
- `reason` is rendered in the step detail view next to the classification.

### US-11 (LYK)
As a developer, I want to read the code AGEM generated for a capability, so that I can judge whether to trust it.

*Should · traces to FR-CAP-020, FR-UI-013*

**Acceptance criteria**
- The Capabilities page opens a read-only detail view showing the stored source code, the `verification_score`, the 3 test inputs, and pass/fail per input.
- No new data is collected — all of it is already stored by `Registry.register()`.

### US-12 (LYK)
As a developer, I want to see the build attempts that failed, not just the one that worked, so that I understand what the repair loop actually did.

*Should · traces to FR-CAP-021*

**Acceptance criteria**
- All 3 build attempts for a gap are stored with their code and failure reason, not only the final state.
- The attempts are viewable in the capability detail view, in order.

### US-13 (LYK)
As a developer, I want to see what an execution cost, so that the cost claims are demonstrated rather than asserted.

*Should · traces to FR-OBS-007*

**Acceptance criteria**
- Prompt and completion tokens are logged on every LLM call and rolled up onto the `Execution` row.
- One cost figure is shown on the execution detail page.

### US-14 (LYK)
As a developer, I want to export a summary of a finished run, so that I have a shareable artefact from a real execution.

*Should · traces to FR-API-007*

**Acceptance criteria**
- `GET /api/executions/{id}/report` returns a JSON or Markdown summary containing steps, statuses, diagnosis, capability built, verification score and timings.

### US-15 (promoted to Must — ADR-009)
As a developer, I want a bad agent endpoint caught when I register it, so that a typo doesn't surface mid-run.

*Must · traces to FR-AGT-011, FR-ADP-010*

**Acceptance criteria**
- AGEM calls the agent's `GET /health` once at registration.
- The agent is set `ACTIVE` only if it responds; an unreachable endpoint is rejected with a reason.

### US-16 (LYK)
As a developer, I want to connect an MCP server's tools as an agent, so that AGEM's interoperability claim holds against a current standard.

*Could · traces to FR-ADP-009*

**Acceptance criteria**
- `mcp_adapter.py` implements the same `base_adapter.py` contract as every other adapter.
- Adding it requires one new file in `adapters/` and no change to `orchestrator/` — the same proof US-03 already asserts.
- Only the HTTP (streamable HTTP) transport is accepted; `stdio` is rejected, because it would launch agent code on AGEM's machine (ADR-009).

### US-17 (LYK)
As a developer, I want an agent to declare what it can already do, so that obvious cases are resolved without spending an LLM call.

*Could · traces to FR-AGT-012, FR-DIAG-013*

**Acceptance criteria**
- An agent can optionally declare its existing capabilities at registration.
- Diagnosis checks that inventory first; a clear match short-circuits to `NORMAL_ERROR` with no diagnosis call made.

### US-18 (LYK)
As an operator, I want to approve a generated capability before an agent is given it, so that nothing self-built runs unattended.

*Could · traces to FR-CAP-022, FR-UI-014*

**Acceptance criteria**
- A capability can optionally require approval; the step enters an `APPROVAL_PENDING` sub-state and an approval card appears on the execution detail view.
- Approve grants the capability and resumes the step; reject fails the step; the decision is recorded.
- The approval endpoint is narrowly scoped to capability approval only — it is **not** a general resume endpoint, which the product still forbids.

### US-19 (LYK)
As a developer, I want a step to run only when a condition holds, so that a workflow can branch instead of always running every step.

*Could · traces to FR-WFL-010, FR-ORC-016*

**Acceptance criteria**
- A step can declare a condition on an upstream output field.
- When the condition isn't met the step is marked `SKIPPED` — a distinct outcome from `FAILED` — and the execution rollup reflects that difference.

### US-20 (LYK)
As a presenter, I want to replay a recorded run, so that a flaky network can't break the demo's one critical moment.

*Could · traces to FR-DEP-009*

**Acceptance criteria**
- Agent responses and LLM responses from one good run can be recorded and replayed.
- A replayed run reproduces the capability-gap recovery sequence without contacting any external service.

### US-21 (LYK)
As an operator, I want live updates to feel instant, so that the recovery sequence animates smoothly rather than stepping every 3 seconds.

*Could · traces to FR-UI-015*

**Acceptance criteria**
- The execution detail view subscribes to a Server-Sent Events stream using the browser's native `EventSource`.
- If the stream drops, the page falls back to 3-second polling with no loss of state.

## Illustrative Scenarios

### Single-Agent Workflow
Example: "Extract tables from this PDF and summarize them." — one PDF-analysis agent is registered by its endpoint and deployed.

Flow: User task → AGEM → Agent → Execute → Success? → Final result. On failure, AGEM diagnoses the step; if a capability is missing, the Capability Engine builds/verifies it and the same agent resumes.

### Multi-Agent Workflow
Example: "Create a market research report." — Web Research, Competitor Analysis, Data Analysis, Quality Checker, Report Writer agents.

Flow: Research → Competitor/Data analysis (parallel where possible) → Quality Check → Writer → Final Report. Agents stay independently built components; AGEM only supplies the coordination layer.

### End-to-End Example
- **Task:** Create a Tesla investment report.
- **Workflow:** Research Agent → Finance Agent → Fact Checker → Writer Agent.
- **Failure:** Finance Agent hits a calculation it cannot perform.
- **Diagnosis:** Master Agent identifies a capability gap.
- **Recovery:** Capability Engine checks for a free/existing calculator tool first; if unavailable, builds/acquires one, runs it in the Sandbox, tests, and verifies it.
- **Resume:** the verified capability is given to the Finance Agent, which resumes the interrupted calculation exactly where it left off. The workflow continues to Fact Checker and Writer.
- **Result:** final report produced without rebuilding the Finance Agent or restarting the workflow.

## Product States

**Execution step states**
`PENDING → RUNNING → SUCCEEDED / FAILED / PAUSED`

Allowed transitions:
- `PENDING` → `RUNNING` only (waiting to start → started).
- `RUNNING` → `SUCCEEDED`, `FAILED` or `PAUSED`.
- `PAUSED` → `RUNNING` (when resumed) or `FAILED` (if it has been tried 3 times and still isn't working).

`SUCCEEDED` and `FAILED` are final — once a step reaches one of these, it can't change. A single `can_transition(old, new)` function enforces this; there is no State-pattern class hierarchy.

`RESUMED` is not a separate state — a resumed step simply returns to `RUNNING`. Resume is an action/transition, not a state.

**Recovery stage** (only meaningful while a step is `PAUSED`)
`NONE → DIAGNOSING → SEARCHING → BUILDING → SANDBOXING → TESTING → VERIFYING → REGISTERED`

This is what the Executions page renders as the "capability gap in progress" node state; keeping it separate is what lets the step status enum stay at five values.

**Execution states**
`PENDING`, `RUNNING`, `PAUSED`, `SUCCEEDED`, `FAILED` — rolled up from its steps: `PAUSED` if any step is `PAUSED`, `FAILED` if any step is `FAILED`, `SUCCEEDED` only when every step is `SUCCEEDED`.

**Agent states**
`ACTIVE`, `INACTIVE`. Registration creates `ACTIVE`. An `INACTIVE` agent cannot be added to a new workflow, but executions that already reference it still resolve — which is why `DELETE /api/agents/{id}` returns 409 rather than cascading.

**Workflow states**
`DRAFT → ACTIVE → ARCHIVED`. A workflow can only become `ACTIVE` after its DAG passes validation at creation time, which guarantees a broken or invalid workflow can never reach the Orchestrator for execution.

**Capability states**
`BUILDING`, `VERIFIED`, `FAILED`. Only `VERIFIED` rows are ever handed to an agent or reused from the registry; `FAILED` rows are kept for the audit trail rather than deleted, because "why was this tool rejected" is a viva question.

**Diagnosis outcomes** (never merged)
`NORMAL_ERROR` · `CAPABILITY_GAP` — exactly two values, no third.

**States introduced only if the matching LYK story is built**
- `APPROVAL_PENDING` — a sub-state while a verified capability waits for user approval (US-18). It does not add a sixth step status; it sits alongside `recovery_stage` in the same way.
- `SKIPPED` — a step outcome distinct from `FAILED`, used when a declared branch condition isn't met (US-19).

Every enum above is defined once in `backend/app/models/` and mirrored in `frontend/src/types/`, so no status string is ever hardcoded in a component or an API handler. These values are written into `models/execution.py` in Week 2 alongside the DB schema, not deferred — the checkpoint and the dashboard both depend on them.

## Product Principles
- Bring existing agents rather than rebuilding them.
- Separate supervision/diagnosis (Master Agent) from execution (Orchestrator) conceptually, even though for MVP they run in the same service.
- Never restart a whole workflow — resume only the failed step.
- Check for a free/accessible tool before generating a new capability.
- Never trust a newly generated/acquired capability without sandboxing, testing, and verification.
- Reuse verified capabilities via a capability registry instead of rebuilding them.
- Start with the simplest version that runs, then layer in recovery and capability evolution — build first, document what was actually built.

## UX Requirements

**Frontend purpose:** a dashboard for agents, workflows, deployments, execution status, and capability events, built in React + TypeScript with Tailwind CSS and React Flow for the workflow/DAG visualization.

### Primary User Flow
Register agent(s) → Create workflow → Deploy/run → Monitor execution live → Observe capability events on failure → View final result.

Navigation is a persistent left sidebar with exactly five items — Dashboard, Agents, Workflows, Executions, Capabilities — matching the five pages fixed in the monorepo structure. No deeper navigation hierarchy is needed for an MVP of this size.

### Key Decision: Workflow Creation Is Form-Based, Not a Drag-and-Drop Canvas
This is the single most important UX decision for the MVP. React Flow is used to **visualize** a workflow's DAG (read-only, on the Workflows and Executions pages), not to author it. Workflow creation itself is a simple ordered form — pick agents from a dropdown, declare each step's upstream dependency from a select list — not a free-form drag-and-drop graph editor.

**Why:** a real drag-and-drop graph editor (node placement, edge drawing, cycle prevention in the UI, layout persistence) is a multi-week frontend project on its own. For a 2-person, 12-week build where Person 1 owns the entire frontend alongside orchestration and adapters, that scope would crowd out the Execution monitor page and the Capabilities view, which matter far more for the demo. A form-based creator with a read-only visual confirmation gets 90% of the perceived value (the DAG is still visible and understandable) for a fraction of the build cost.

### Page-by-Page Requirements

| Page | Purpose | Key States |
|---|---|---|
| Dashboard | Landing page: summary cards for active agents, saved workflows, currently running executions, and recent capability builds | Loading skeleton; empty state ("No agents registered yet") with a call-to-action linking to Agents |
| Agents | Table of registered agents (name, framework, status) with a "Register Agent" form (name, framework dropdown, endpoint/connection info — no code upload in MVP) | Loading; empty ("Register your first agent"); error toast on a failed registration (e.g. invalid endpoint) |
| Workflows | List of saved workflow definitions; "Create Workflow" opens the form-based step/dependency builder; React Flow renders the resulting DAG read-only for confirmation before saving | Loading; empty; validation error inline if the declared steps form a cycle (rejected before saving, not at run time) |
| Executions | List of runs; clicking one opens the ExecutionDetail view: WorkflowGraph (React Flow, read-only) with each node color-coded via StepStatusCard, plus a live timeline/log underneath, refreshed by polling every 3 seconds | Loading; running (live-updating); a distinct "capability gap in progress" node state (build/sandbox/test/verify) so a viewer can see recovery happening, not just failure/success; succeeded; failed |
| Capabilities | Read-only registry view of built/acquired capabilities (name, version, `verification_score`, source: built vs. acquired). Capabilities are created automatically by the Capability Engine; this page exists purely for visibility and audit, not manual creation | Loading; empty (before the first capability gap is ever hit) |

### Accessibility
A full WCAG audit is out of scope for the 12-week MVP — this is a stated, deliberate scope decision, not an oversight. A minimal, low-cost baseline is still expected everywhere: semantic HTML elements over generic divs, visible keyboard focus states, labeled form inputs, and status badges that pair color with a text label or icon rather than relying on color alone. This last point directly protects the demo, since an examiner or teammate should be able to read a step's status even on an unfamiliar screen or a color-limited projector.

### Evidence Expected in a Demo
Dashboard, agent registry, workflow graph, execution trace, step status, capability creation/acquisition, verification result, final output, failure/recovery example.
