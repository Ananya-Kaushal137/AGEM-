# AGEM — Functional Requirements Specification (FRS)

**Product:** AGEM — Agent Workflow & Capability Evolution Platform
**Version:** 1.1 (aligned with the AGEM Master Documentation — BRD · PRD · TRD · HLD · LLD · ADRs · Roadmap)
**Team:** Ananya (Orchestration, Integration & Frontend) · Guthal (Intelligence, Capability Engine & Safety)
**Delivery model:** Single-node, self-hosted web app — React + TypeScript frontend, FastAPI backend, PostgreSQL, Docker sandbox — brought up with one `docker-compose.yml`
**Status:** For review
**Companion docs:** AGEM Master Documentation (BRD/PRD/TRD/HLD/LLD/ADRs/Roadmap) · LYK Proposal Notes

### Change log

| Version | Changes |
|---|---|
| 1.0 | Initial FRS derived from the AGEM master documentation: 14 functional areas, ~134 testable sub-features, NFRs, business rules, use cases, test strategy, traceability, 12-week phase list |
| 1.1 | Added LYK ("if we're not scoping to the 12-week timeline") proposals as first-class functional requirements: diagnosis reasoning, generated-code viewer, agent connection test, token/cost counter, failed-attempt history, execution report export, MCP adapter, declared capability inventory, human-in-the-loop approval gate, conditional/branching workflow steps, deterministic replay mode, SSE live updates |

---

## 1. Introduction

### 1.1 Purpose
This document defines **what AGEM must do**: every functional requirement, the quality requirements it must meet, the rules it must never break, and the use cases and test approach that prove each requirement works. It is the reference for build, review and the viva defence.

### 1.2 Scope

**In scope (MVP, delivered in the 12-week build):** Bring Your Own Agent (agent registration and registry), the adapter layer for interoperability, workflow definition with DAG validation, the orchestration and execution engine, step-level checkpointing with pause/resume, failure diagnosis via a Master Agent, the Capability Engine (search → build/acquire → sandbox → test → verify → register), sandboxed code safety, the API layer and contracts, static API-key auth and data protection, the persistence layer, structured observability and audit logging, and the deployment/testing/demo tooling.

**Specified here but built only if the schedule allows (LYK, not gated to a specific week):** diagnosis reasoning strings, a generated-code viewer, a connection test at agent registration, a token/cost counter, failed-attempt history on a capability, an execution report export, an MCP adapter, a declared capability inventory per agent, a human-in-the-loop approval gate on granted capabilities, conditional/branching workflow steps, a deterministic replay mode for the demo, and Server-Sent Events in place of polling.

**Out of scope for this project (Future Scope, not specified here):** a marketplace, billing, advanced autonomous planning, large-scale Kubernetes deployment, sophisticated memory systems, enterprise RBAC, very large agent swarms, real tool discovery (replacing the hardcoded search), property-based/formal verification, capability versioning and supersession, multi-tenant users/OAuth, fan-out/map steps, capability signing, and OpenTelemetry-grade tracing.

### 1.3 Definitions

| Term | Meaning |
|---|---|
| Agent | A self-contained worker that does one job (research, calculate, write). Brought into AGEM, not built by AGEM. |
| Adapter | A wrapper translating one agent framework's interface into AGEM's standard contract (`base_adapter.py`: `execute(input) -> output`), so the Orchestrator never needs to know the framework |
| Orchestrator | The engine that runs a workflow: decides which step runs next, passes data between steps, maintains execution state |
| Master Agent | The supervisory/diagnostic role — classifies a failure as `NORMAL_ERROR` or `CAPABILITY_GAP`. Implemented as `diagnose_failure()` inside the Orchestrator service for the MVP (ADR-001), documented as a separate concept |
| Capability | A specific skill or tool an agent needs but might not have (e.g. "calculate compound interest") |
| Tool | The actual implementation of a capability — a Python function or a configured API call |
| Capability Engine | The system that resolves a capability gap: search → build/acquire → sandbox → test → verify → register |
| Sandbox | An isolated Docker container (`sandbox_runner/`) where untrusted generated code runs, with no network access and resource limits |
| Verifier | The check that a generated tool's output is correct/safe for the real task, not just that it ran without crashing |
| Registry | The database table of verified capabilities, so they are reused instead of rebuilt |
| Checkpoint | The stored record of an execution's current step, prior outputs, pending inputs and recovery state, used to resume exactly where a workflow paused |
| Execution / ExecutionStep | One run of a workflow, and one run of one step inside it |
| Workflow / WorkflowAgent | A saved DAG definition, and the row declaring one agent's place, dependency and input mapping inside it |
| DAG | Directed Acyclic Graph — the dependency structure a workflow is validated against at creation time |
| Recovery stage | The sub-state (`NONE → DIAGNOSING → SEARCHING → BUILDING → SANDBOXING → TESTING → VERIFYING → REGISTERED`) shown only while a step is `PAUSED` |
| LLM | A hosted language model (OpenAI or Anthropic) called via API — used only for diagnosis and capability generation, never for deterministic mechanics |

### 1.4 Priority scale (MoSCoW)
**M** = Must (MVP is blocked without it) · **S** = Should (MVP target, not blocking) · **C** = Could (build if the schedule allows — this is where most LYK items sit) · **L** = Later (named here, explicitly deferred to a future iteration) · **W** = Won't (explicitly rejected as over-engineering for this scope)

### 1.5 References
AGEM Master Documentation (BRD §1–12, PRD, TRD, HLD, LLD, ADR-001–007, Roadmap, Traceability Matrix) · LYK Proposal Notes (Tier 1/2/3)

---

## 2. Overall Description

### 2.1 User types

Derived from BRD §5 Personas — all are the same two-person team plus an examiner during the MVP; the roles describe tasks, not separate accounts.

| User | Description | Main goals |
|---|---|---|
| Developer (P1: solo agent developer) | Registers an agent, drops it into a workflow, expects step-level resume on failure | Reuse an agent without rewriting it |
| Team lead (P2: multi-agent team lead) | Assembles several specialist agents, often on different frameworks, into one pipeline | Reliable ordering, data passing and shared state across agents |
| Operator / Platform owner (P3) | Deploys and watches workflows centrally rather than on a laptop | Live status, audit trail of every diagnosis/build/verification |
| Examiner / Viewer | Watches a live demo or viva | A visual, trustworthy walkthrough of a running and recovering workflow |
| Contributor (P4, Future Scope) | Would publish an agent as a reusable component for others | Not built for in the MVP; the adapter contract is designed so it stays possible |

v1 (MVP) is effectively single-tenant: one seeded `User` row owns everything (see §3.11 AUTH).

### 2.2 Operating environment
- Frontend: React + TypeScript + Tailwind CSS + React Flow, latest browsers
- Backend: Python + FastAPI, one process, running in Docker
- Database: PostgreSQL + SQLAlchemy + Alembic migrations
- Sandbox: a separate minimal Docker image (`sandbox_runner/`), no internet access
- LLM: a hosted provider (OpenAI or Anthropic) — never run locally
- Orchestration of the stack: Docker Compose, single machine
- Optional (only if needed from Week 8): Redis + Celery for the async job queue

### 2.3 Assumptions
- The developer supplies their own LLM provider API key and any credentials a registered agent's endpoint needs.
- The machine running AGEM has Docker installed and network access to the LLM provider and to any registered REST agent endpoints.
- Agents are already built and reachable over HTTP (or via one supported framework adapter) before they are registered — AGEM never builds an agent.

### 2.4 Constraints
- A 2-person team delivering a demonstrable MVP within a 12-week academic timeline, without dedicated infrastructure beyond Docker, PostgreSQL and a hosted LLM API.
- The UI holds no execution logic and no source of truth; the backend and the database do.
- Untrusted code (agent-generated or acquired) never executes directly on the main backend process — the sandbox is the only place it runs.
- All backend code is Python; all frontend code is TypeScript.
- Every enum/status is defined once in `backend/app/models/` and mirrored in `frontend/src/types/` — never hardcoded in a component or handler.

---

## 3. Functional Requirements

Each requirement has an ID, priority and acceptance criteria (AC). Sections are grouped by functional area, matching the `backend/` folder structure, with the week each area is first delivered in the 12-week Roadmap.

### 3.1 Agent Registration & Registry (AGT) — Week 3

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-AGT-001 | Register an agent | M | `POST /api/agents` with name, framework, endpoint/config returns the created Agent with status `ACTIVE` |
| FR-AGT-002 | Framework enum validation | M | `framework` must be one of `python`, `rest`, `langchain`, `crewai`; anything else is rejected at registration with a clear error, not later at execution time |
| FR-AGT-003 | Agent status lifecycle | M | `ACTIVE` / `INACTIVE`; an `INACTIVE` agent cannot be added to a new workflow, but executions already referencing it still resolve |
| FR-AGT-004 | List agents | M | `GET /api/agents` backs the Agents page table |
| FR-AGT-005 | Agent detail | M | `GET /api/agents/{id}`; `404 AGENT_NOT_FOUND` if missing |
| FR-AGT-006 | Delete with referential guard | M | `DELETE /api/agents/{id}` returns `409` if the agent is referenced by an active workflow (`ON DELETE RESTRICT`) |
| FR-AGT-007 | Encrypted credential storage | M | If a registered agent's endpoint needs credentials, they are Fernet-encrypted at rest in `encrypted_credentials` and never echoed in any API response |
| FR-AGT-008 | No code upload | M (scope boundary) | Registration accepts endpoint/connection info only; no agent source-code upload in the MVP |
| FR-AGT-009 | Agent↔capability grants | M | The `AgentCapability` join table records `agent_id`, `capability_id`, `granted_at`, `granted_by` |
| FR-AGT-010 | Demo seeding | M | `scripts/seed_agents.py` populates 4–5 test agents before a demo run |
| FR-AGT-011 (LYK) | Connection test on registration | C | The engine pings the agent's endpoint once at registration; the agent is only set `ACTIVE` if it responds, so a mistyped endpoint doesn't register cleanly and fail mid-demo |
| FR-AGT-012 (LYK) | Declared capability inventory | C | At registration an agent can optionally declare the capabilities it already has, stored on the Agent row for use during diagnosis (FR-DIAG-013) |

### 3.2 Adapter Layer / Interoperability (ADP) — Weeks 3, 7

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-ADP-001 | `base_adapter.py` contract | M | An abstract class defines exactly one method, `execute(input: dict) -> dict`; every adapter implements it |
| FR-ADP-002 | REST/API adapter | M | `rest_adapter.py` calls an external agent over HTTP through the shared contract |
| FR-ADP-003 | One framework adapter | M | `langchain_adapter.py` **or** `crewai_adapter.py` (one of the two), built by Week 7 |
| FR-ADP-004 | Common I/O contract | M | Every adapter exposes the same shape: task input, context, requested capability/tool info, result, status, error |
| FR-ADP-005 | Error normalisation at the boundary | M | Framework exceptions and structured agent errors are normalised inside `step_executor.py` to `{"status":"FAILED","error_type":"...","raw_error":"..."}` before diagnosis ever sees them |
| FR-ADP-006 | Framework isolation | M | No framework-specific import exists outside `adapters/`; `orchestrator/` never changes when a new adapter is added |
| FR-ADP-007 | Agents stay external and opaque | M | Agents are reached only over HTTP through an adapter; AGEM never imports agent code into its own process |
| FR-ADP-008 | Adapter test suite | M | `test_adapters.py` covers a valid REST response, a timeout, and malformed JSON normalised into the standard error shape |
| FR-ADP-009 (LYK) | MCP adapter | C | `mcp_adapter.py` implements the `base_adapter.py` contract against a Model Context Protocol server, added as one file with no change to `orchestrator/` |

### 3.3 Workflow Definition & DAG Validation (WFL) — Weeks 2, 4

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-WFL-001 | Create workflow | M | `POST /api/workflows` with an ordered list of steps plus dependencies |
| FR-WFL-002 | Cycle detection | M | A topological sort at creation rejects the request with `400 WORKFLOW_CYCLE_DETECTED` if dependencies aren't a valid DAG |
| FR-WFL-003 | Unregistered agent rejection | M | A step referencing an unknown `agent_id` is rejected at creation, not at run time |
| FR-WFL-004 | Sort once, store the order | M | The topological sort runs once, at creation; the result is stored on `WorkflowAgent.step_order`; the Orchestrator never re-sorts at run time |
| FR-WFL-005 | Declarative input mapping | M | `WorkflowAgent.input_mapping` declares which upstream output field feeds a step's input |
| FR-WFL-006 | Workflow status lifecycle | M | `DRAFT → ACTIVE → ARCHIVED`; a workflow only becomes `ACTIVE` after DAG validation passes |
| FR-WFL-007 | List / detail endpoints | M | `GET /api/workflows`, `GET /api/workflows/{id}`; detail returns the DAG structure consumed directly by React Flow |
| FR-WFL-008 | Form-based creation | M (deliberately simplified) | Agents are picked from a dropdown and dependencies declared from a select list; React Flow renders the result read-only for confirmation before saving — no drag-and-drop authoring in the MVP |
| FR-WFL-009 | Workflow size | M | 1–5 agents per workflow is supported end-to-end |
| FR-WFL-010 (LYK) | Conditional / branching steps | C | A step can declare a condition on an upstream output field; the Orchestrator skips the step (a distinct `SKIPPED` outcome, not `FAILED`) when the condition isn't met |

### 3.4 Orchestration & Execution Engine (ORC) — Weeks 4–6

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-ORC-001 | Non-blocking execution start | M | `POST /api/workflows/{id}/executions` returns `execution_id` immediately; the run proceeds via FastAPI `BackgroundTasks` |
| FR-ORC-002 | Execution entry point | M | `Orchestrator.run_execution(execution_id: UUID) -> None` |
| FR-ORC-003 | Sequential DAG execution | M | Steps run in the stored `step_order`, respecting declared dependencies |
| FR-ORC-004 | Parallel sibling steps | M | Steps with no dependency between them run under `asyncio.gather` |
| FR-ORC-005 | Automatic output→input passing | M | Step N's input includes the declared upstream step's output field(s), read from the Checkpoint, with no manual wiring |
| FR-ORC-006 | Shared execution state | M | Task, agent outputs, current step, errors, capabilities and execution status are held as one shared state object |
| FR-ORC-007 | Retry logic | M | A step retries automatically before being marked failed, per the routing in §3.6 (DIAG) |
| FR-ORC-008 | Step state machine | M | Exactly 5 states: `PENDING → RUNNING → SUCCEEDED / FAILED / PAUSED`; `PAUSED` returns to `RUNNING` on resume or to `FAILED` after 3 attempts; `SUCCEEDED` and `FAILED` are terminal |
| FR-ORC-009 | Transition guard | M | A single `can_transition(old, new)` function enforces legal transitions — explicitly not a State-pattern class hierarchy |
| FR-ORC-010 | Execution status rollup | M | `Execution.status` derives from its steps: `PAUSED` if any step is paused, `FAILED` if any failed, `SUCCEEDED` only when every step succeeded |
| FR-ORC-011 | Recovery sub-state | M | A separate `ExecutionStep.recovery_stage` (`NONE → DIAGNOSING → SEARCHING → BUILDING → SANDBOXING → TESTING → VERIFYING → REGISTERED`) is meaningful only while status is `PAUSED` |
| FR-ORC-012 | Single-step executor | M | `StepExecutor.run_step(step_id: UUID) -> dict` runs exactly one step and returns the normalised result shape |
| FR-ORC-013 | One transaction per transition | M | Status and checkpoint are written in the same SQLAlchemy transaction, so a crash never leaves a step `SUCCEEDED` with no stored output |
| FR-ORC-014 | Orchestration overhead budget | M | Orchestration bookkeeping adds < 300 ms per step, excluding agent and LLM call time |
| FR-ORC-015 | Scalability fallback | S | If CPU-bound work blocks the event loop by Week 8, `step_executor.py` and the Capability Engine move into a `ThreadPoolExecutor`/`ProcessPoolExecutor` before Celery/Redis is considered (ADR-007) |
| FR-ORC-016 (LYK) | Branch-aware execution | C | The Orchestrator honours FR-WFL-010 conditions and reflects skipped steps distinctly from failed ones in the execution rollup |

### 3.5 Checkpointing, Pause & Resume (CKP) — Week 5

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-CKP-001 | Save/load API | M | `CheckpointManager.save(step_id, state)` and `CheckpointManager.load(execution_id) -> dict` |
| FR-CKP-002 | Written twice per step | M | A checkpoint is written when a step succeeds and again the moment it pauses — never only at the end of a run |
| FR-CKP-003 | Checkpoint contents | M | Current step, prior successful outputs, pending inputs, workflow state and recovery information, stored as JSONB |
| FR-CKP-004 | Step-level pause | M | Only the failed `ExecutionStep` enters `PAUSED`; upstream steps are untouched |
| FR-CKP-005 | Exact-step resume | M | Resume reads its input from the checkpoint; upstream steps are never recomputed |
| FR-CKP-006 | Idempotent resume | M | Resuming a step already `SUCCEEDED` is a no-op, so a retried background task or a double-click can't duplicate work |
| FR-CKP-007 | Resume is internal-only | M | No public resume endpoint exists; resume only ever follows a successful diagnosis or a verified capability |
| FR-CKP-008 | Bounded pause | M | After 3 failed attempts a `PAUSED` step moves to `FAILED` rather than pausing forever |
| FR-CKP-009 | Crash recoverability | M | Every transition is written before the next step starts, so a backend restart mid-run leaves a recoverable, inspectable state instead of a lost one |

### 3.6 Failure Diagnosis / Master Agent (DIAG) — Weeks 3, 6

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-DIAG-001 | Structured failure signal | M | The agent reports failure as a structured object, e.g. `{"error":"MISSING_CAPABILITY","capability":"..."}` |
| FR-DIAG-002 | Single normalised error shape | M | Every exception or structured agent error becomes `{"status":"FAILED","error_type":"...","raw_error":"..."}` before diagnosis reads it |
| FR-DIAG-003 | Binary classification | M | `Orchestrator.diagnose_failure(step_id, error) -> Literal["NORMAL_ERROR","CAPABILITY_GAP"]`; exactly two outcomes |
| FR-DIAG-004 | One diagnosis call per failure | M | A hard budget cap of 1 LLM diagnosis call per failure |
| FR-DIAG-005 | Schema-constrained LLM output | M | Every diagnosis call requests JSON validated against a fixed Pydantic schema |
| FR-DIAG-006 | Validation retry loop | M | On parse/validation failure, retry the same call up to 2 times with the validation error appended to the prompt |
| FR-DIAG-007 | Fail-safe default | M | After retries are exhausted, or past a 20 s timeout, fail safe to `NORMAL_ERROR` rather than hang the workflow |
| FR-DIAG-008 | Cheap model tier | M | Diagnosis uses a cheaper model tier; the stronger model is reserved for code generation in `builder.py` |
| FR-DIAG-009 | Single LLM wrapper | M | No module calls the LLM API directly; every call goes through one wrapper handling the JSON request, validation, retry and fail-safe |
| FR-DIAG-010 | Diagnosis visible in UI | M | The classification result appears in the step detail view, not only in logs |
| FR-DIAG-011 | Accuracy evaluation | S | 20 hand-labelled error examples (10 genuine gaps, 10 normal errors) in `backend/tests/fixtures/`, scored as classification accuracy, **18/20 pass bar**, re-run whenever the prompt changes |
| FR-DIAG-012 (LYK) | Diagnosis reason string | S | `diagnose_failure()` additionally returns a schema-validated `reason` and, when applicable, `missing_capability` field in the same call; `reason` is rendered in the step detail alongside the classification, at zero extra LLM cost |
| FR-DIAG-013 (LYK) | Pre-check against declared inventory | C | Before spending an LLM call, diagnosis checks the agent's declared capability inventory (FR-AGT-012); a clear match short-circuits to `NORMAL_ERROR` without a diagnosis call |

### 3.7 Capability Engine (CAP) — Weeks 4–8

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-CAP-001 | Pipeline orchestration | M | `engine.py` runs the stages (normal-error check → free-tool search → build → sandbox → test → verify) as a simple ordered list of functions — not a class-per-handler hierarchy |
| FR-CAP-002 | Free-tool search first | M | `Searcher.find_free_tool(capability_name) -> Tool \| None` always runs before the build path (ADR-003) |
| FR-CAP-003 | Search is a hardcoded check | M (simplified, disclosed) | The MVP search is a hardcoded web-search check, not a general tool-discovery system — the weakest link in the pipeline, documented as such rather than hidden |
| FR-CAP-004 | Found → plug in and resume | M | If a free tool covers the gap, it is added to the workflow where needed and the paused step resumes — no build step at all |
| FR-CAP-005 | Code generation | M | `Builder.build(capability_name, spec) -> str` returns Python source and never executes it |
| FR-CAP-006 | Static import check | M | Generated code is scanned for disallowed imports (`os.system`, `subprocess`, `socket`, `open` outside a scratch dir) before it is ever sent to the sandbox |
| FR-CAP-007 | Sandboxed execution | M | `Sandbox.run(code, inputs) -> SandboxResult` (see §3.8 SBX) |
| FR-CAP-008 | Testing | M | `Tester.test(code, samples) -> TestResult` runs the tool against **3 sample inputs** |
| FR-CAP-009 | Verification | M | `Verifier.verify(result, expected_type, expected_range) -> float` returns the `verification_score` stored on the Capability row |
| FR-CAP-010 | Verification scope | M (simplified, disclosed) | Verification checks output type and range for the kind of input the failed step needs — not full formal verification against all future inputs |
| FR-CAP-011 | Repair loop | M | On verification failure the engine improves/rebuilds and re-tests rather than discarding the attempt outright |
| FR-CAP-012 | Hard attempt cap | M | `resolve_gap()` returns `None` after **3 build/repair attempts**; the step is marked `FAILED` and `CAPABILITY_BUILD_FAILED` (500) is surfaced |
| FR-CAP-013 | Registration | M | `Registry.register(name, version, code, score) -> Capability` writes to the PostgreSQL capability table |
| FR-CAP-014 | Capability status lifecycle | M | `BUILDING / VERIFIED / FAILED`; only `VERIFIED` rows are ever handed to an agent or reused; `FAILED` rows are kept for the audit trail, never deleted |
| FR-CAP-015 | Grant and resume | M | The verified capability is handed to the agent (`AgentCapability` row) and the exact paused step resumes |
| FR-CAP-016 | Reuse / dedup | M | A second agent hitting the same gap finds the capability in the registry instead of rebuilding it (ADR-006) |
| FR-CAP-017 | Timing budget | M | Build → sandbox → test → verify completes in **< 60 s end-to-end**, including at most one repair loop |
| FR-CAP-018 | Generation evaluation | S | 5 canned gaps (the demo calculator plus four others) scored as the proportion reaching `VERIFIED` within the 3-attempt cap |
| FR-CAP-019 | Reliable demo trigger | M | `scripts/trigger_capability_gap.py` reproducibly forces a capability gap so the search → build → sandbox → test → resume sequence can be demonstrated on demand |
| FR-CAP-020 (LYK) | Generated code viewer | S | The Capabilities page can open a detail view showing the stored source code, the verification score, the 3 test inputs and the pass/fail result per input |
| FR-CAP-021 (LYK) | Failed-attempt history | S | All 3 build attempts for a gap (code + failure reason) are stored, not only the final state, and are viewable in the capability detail view |
| FR-CAP-022 (LYK) | Human-in-the-loop approval gate | C | A capability can optionally require user approval before being granted to an agent; the step enters an `APPROVAL_PENDING` sub-state and a narrowly-scoped approval endpoint (distinct from the general resume endpoint forbidden by FR-CKP-007) accepts approve/reject |

### 3.8 Sandbox & Code Safety (SBX) — Week 6

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-SBX-001 | Separate sandbox image | M | `sandbox_runner/` is its own minimal Dockerfile with `runner.py` and a requirements file limited to stdlib plus very few packages — not a module inside the backend |
| FR-SBX-002 | Fresh container per run | M | A new container per execution, destroyed afterwards, so no state carries between capability tests |
| FR-SBX-003 | No network | M | The container runs with `--network none` |
| FR-SBX-004 | Execution timeout | M | A hard 10 s timeout per test run |
| FR-SBX-005 | Resource caps | M | `--memory=256m --cpus=0.5` |
| FR-SBX-006 | File-mount I/O | M | Code and inputs are passed in by file mount; results are read back out |
| FR-SBX-007 | Import allowlist | M | No `os.system`, `subprocess`, `socket`, or `open()` outside a scratch temp directory |
| FR-SBX-008 | Restricted filesystem | M | Sandbox filesystem access is limited to the scratch directory |
| FR-SBX-009 | Two-layer defence | M | The static import check (FR-CAP-006) is the first layer; container restrictions are the second — neither replaces the other |
| FR-SBX-010 | Single trust boundary | M | Exactly one trust boundary exists in the whole system: main backend process ↔ sandbox container; generated code never crosses it inward |
| FR-SBX-011 | Degraded fallback | S (simplified fallback only) | If Docker is unavailable, a Python subprocess with a timeout and restricted imports is used instead; the setup may be lighter but isolation is never skipped |
| FR-SBX-012 | Sandbox test suite | M | `test_sandbox.py`: a network attempt fails; a timeout is killed; a memory-cap breach is killed; a benign function returns correctly |

### 3.9 Dashboard / Frontend (UI) — Weeks 9–10

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-UI-001 | Five-page shell | S | A persistent left sidebar with exactly five items — Dashboard, Agents, Workflows, Executions, Capabilities — no deeper navigation hierarchy |
| FR-UI-002 | Dashboard summary | S | Cards for active agents, saved workflows, currently running executions and recent capability builds |
| FR-UI-003 | Agents page | S | Table (name, framework, status) plus a Register Agent form (name, framework dropdown, endpoint/connection info) |
| FR-UI-004 | Workflows page | S | List of definitions; form-based Create Workflow; React Flow renders the resulting DAG read-only for confirmation before saving |
| FR-UI-005 | Executions list | S | All runs with rolled-up status, newest first |
| FR-UI-006 | Execution detail view | M | `WorkflowGraph` (React Flow, read-only) with each node colour-coded via `StepStatusCard`, plus a live timeline/log underneath |
| FR-UI-007 | 3-second polling | S | The Executions page refreshes by polling `GET /api/executions/{id}` every 3 s |
| FR-UI-008 | Recovery-in-progress node state | M | A distinct node state renders `recovery_stage` (search/build/sandbox/test/verify) so a viewer sees recovery happening, not just failure→success |
| FR-UI-009 | Capabilities page | S | Read-only registry: name, version, `verification_score`, source (built vs acquired); capabilities are created only by the engine, no manual creation UI |
| FR-UI-010 | Loading / empty / error states | S | Per page: loading skeleton, empty state with a call-to-action, error toast on failed registration, inline validation error on a cyclic workflow |
| FR-UI-011 | Accessibility baseline | M (deliberate baseline, not an audit) | Semantic HTML over generic divs, visible keyboard focus states, labelled form inputs, status badges pairing colour with a text label or icon; a full WCAG audit is explicitly out of scope |
| FR-UI-012 | Shared enum types | M | Every enum is defined once in `backend/app/models/` and mirrored in `frontend/src/types/`; no status string is hardcoded in a component |
| FR-UI-013 (LYK) | Capability detail panel | S | Renders FR-CAP-020 (generated code, score, test results) as a page/panel on the Capabilities page |
| FR-UI-014 (LYK) | Approval card | C | Renders FR-CAP-022; shown on the execution detail view when a step is `APPROVAL_PENDING`, with approve/reject actions |
| FR-UI-015 (LYK) | SSE live updates | C | The execution detail view subscribes to a Server-Sent Events stream instead of polling, using the browser's native `EventSource`; falls back to 3 s polling if the stream drops |

### 3.10 API Layer & Contracts (API) — Week 3 onward

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-API-001 | Endpoint set | M | 4 routers — `agents.py`, `workflows.py`, `executions.py`, `capabilities.py` — covering the full endpoint list, documented verbatim in `docs/api-spec.md` |
| FR-API-002 | Strict request validation | M | Pydantic schemas in `app/schemas/` use `extra = "forbid"` so unknown fields are rejected, not silently ignored |
| FR-API-003 | Single error envelope | M | Every 4xx/5xx response uses `{"error_code": "...", "message": "...", "details": {...}}` |
| FR-API-004 | Error code catalogue | M | `VALIDATION_ERROR` (422), `*_NOT_FOUND` (404), `WORKFLOW_CYCLE_DETECTED` (400), `UNAUTHORIZED` (401), `CAPABILITY_BUILD_FAILED` (500) |
| FR-API-005 | Stateless, non-blocking | M | The API layer holds no execution state, validates the request, starts work and returns immediately |
| FR-API-006 | Step detail endpoint | M | `GET /api/executions/{id}/steps/{step_id}` includes error, diagnosis result and checkpoint reference |
| FR-API-007 (LYK) | Execution report export | S | `GET /api/executions/{id}/report` returns a JSON or Markdown summary of steps, statuses, diagnosis, capability built, verification score and timings for a completed run |

### 3.11 Auth & Data Protection (AUTH) — Weeks 2–3, 11

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-AUTH-001 | Static API key auth | M (deliberate) | One `API_KEY` value in `.env`, one `get_api_key` FastAPI dependency applied globally, checked as `X-API-Key` on every request |
| FR-AUTH-002 | Single seeded user | M (deliberate) | No login; one seeded `User` row owns everything in the MVP |
| FR-AUTH-003 | No OAuth / JWT / RBAC | L | Not built in the MVP; Enterprise RBAC is explicit Future Scope |
| FR-AUTH-004 | Single secrets entry point | M | LLM keys and agent credentials are read through `core/config.py` only; never stored in PostgreSQL; never echoed in a response |
| FR-AUTH-005 | Encryption at rest | M | Agent credentials are Fernet-encrypted with a single symmetric key held in `.env` |
| FR-AUTH-006 | PII-free audit logging | M | Logs record execution/step/capability metadata (IDs, timestamps, status, diagnosis outcome) by default — not raw agent input/output payloads; full payload logging exists only as an explicit debug flag |
| FR-AUTH-007 | Week 11 security review | S | A dedicated pass over sandbox limits, input validation and auth, owned by Person 2 |

### 3.12 Persistence & Data Layer (DAT) — Week 2

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-DAT-001 | UUID primary keys | M | Application-generated UUIDs, not auto-increment integers, so an ID can be returned by a POST before commit and is safe to expose in a URL |
| FR-DAT-002 | UTC timestamps everywhere | M | Every table carries `created_at` and `updated_at` |
| FR-DAT-003 | Enum status columns | M | Status columns are Python Enums, never free strings |
| FR-DAT-004 | JSONB for free-form structures | M | Workflow definition, step input/output and checkpoint state are JSONB columns, not separate tables |
| FR-DAT-005 | Deliberate FK policies | M | `ON DELETE RESTRICT` from Agent to WorkflowAgent (produces the 409 in FR-AGT-006); `ON DELETE CASCADE` down Execution → ExecutionStep → Checkpoint |
| FR-DAT-006 | Alembic migrations | M | After the first migration exists, all schema changes go through Alembic — no manual edits to `database/init.sql` |
| FR-DAT-007 | Thin repository CRUD | M | A small set of plain CRUD functions per model, not a generic repository abstraction layer |
| FR-DAT-008 | DB as single source of truth | M | No execution state lives only in memory |

### 3.13 Observability & Audit Trail (OBS) — ongoing, Week 11

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-OBS-001 | Structured JSON logs | M | Standard `logging` module, JSON to stdout, captured by Docker |
| FR-OBS-002 | Correlated log lines | M | Every log line carries `execution_id`, `step_id` and `agent_id` where applicable |
| FR-OBS-003 | LLM call audit | M | Every LLM call logs prompt, response and verdict — treated as a safety requirement, not only debugging |
| FR-OBS-004 | Sandbox execution audit | M | Every sandbox run is logged |
| FR-OBS-005 | Output artefacts | M | Final task result, execution history, agent outputs, errors and recovery events, capability build history, verification results |
| FR-OBS-006 | No external observability stack | L (deliberate) | No ELK, Datadog, Grafana or OpenTelemetry; the Executions page is the intended observability tool |
| FR-OBS-007 (LYK) | Token + cost counter | S | Prompt/completion tokens are logged on every LLM call and rolled up onto the Execution row; one cost figure is surfaced on the execution detail page |

### 3.14 Deployment, Testing & Demo Tooling (DEP) — Weeks 1, 9–12

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-DEP-001 | Single-node compose | M | `docker-compose.yml` brings up frontend, backend, PostgreSQL and the sandbox image on one machine |
| FR-DEP-002 | Exactly two environments | M | Local development (each member's machine, own LLM key) and demo (one machine, seeded before the viva); no staging or production |
| FR-DEP-003 | CI pipeline | S | One GitHub Actions workflow on push/PR to `main` with two jobs: backend (install, ruff/flake8, pytest) and frontend (`npm ci`, `tsc --noEmit`, `npm run build`); no deploy job |
| FR-DEP-004 | Test suite | M | `test_adapters.py`, `test_orchestrator.py`, `test_capability_engine.py`, `test_sandbox.py` |
| FR-DEP-005 | Coverage target | S | 70% line coverage across `backend/orchestrator/`, `backend/adapters/` and `backend/capability_engine/`, measured with pytest-cov; API layer and models excluded from the target |
| FR-DEP-006 | LLM mocking in unit tests | M | LLM calls are mocked in unit tests; real API calls happen only in the two evaluation runs (FR-DIAG-011, FR-CAP-018) |
| FR-DEP-007 | Demo scripts | M | `scripts/trigger_capability_gap.py`, `seed_agents.py`, `run_dev.sh` |
| FR-DEP-008 | Rollback procedure | S | `git revert` to the last known-good commit plus `docker-compose up --build`; schema rollback via `alembic downgrade` |
| FR-DEP-009 (LYK) | Deterministic replay mode | C | One good run's agent responses and LLM responses can be recorded and replayed, so the demo's capability-gap moment is immune to a flaky network or a slow API call |

---

## 4. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-PERF-01 | Performance | Orchestrator overhead adds < 300 ms per step, excluding agent/LLM call time |
| NFR-PERF-02 | Performance | Dashboard/Executions refresh interval is a 3-second poll |
| NFR-PERF-03 | Performance | Master Agent diagnosis LLM call: < 8 s typical, treated as a timeout at 20 s |
| NFR-PERF-04 | Performance | Capability build → sandbox → test → verify loop: < 60 s end-to-end, including at most one repair loop |
| NFR-PERF-05 | Performance | Supports 1–5 simultaneous workflow runs |
| NFR-SEC-01 | Security | Untrusted (imported or generated) code never executes directly on the main AGEM server — isolation is the non-negotiable foundation of the safety claim |
| NFR-SEC-02 | Security | Sandbox isolation is enforced by literal Docker flags: `--network none`, an execution timeout, `--memory=256m --cpus=0.5` |
| NFR-SEC-03 | Security | Generated code is restricted to a safe import allowlist, checked statically before it ever reaches the sandbox |
| NFR-SEC-04 | Security | Secrets (LLM keys, agent credentials) never appear in the database, logs or API responses |
| NFR-SEC-05 | Security | Every request requires a valid `X-API-Key` header |
| NFR-REL-01 | Reliability | Retry/recovery logic and step checkpoints let execution resume without restarting the whole workflow |
| NFR-REL-02 | Reliability | Every step transition and checkpoint is written before the next step starts, so a backend restart mid-run leaves a recoverable, inspectable state instead of a lost one |
| NFR-AUDIT-01 | Auditability | Execution history, errors, recovery events, capability acquisition/build history and verification results are all recorded as outputs |
| NFR-ACC-01 | Accessibility | Semantic HTML, visible keyboard focus states, labelled form inputs, status badges pairing colour with text/icon — a deliberate baseline, not a full WCAG audit |
| NFR-COST-01 | Cost | At most 1 diagnosis call per failure and at most 3 build/repair attempts per capability gap |
| NFR-COST-02 | Cost | A cheaper model tier is used for diagnosis; the stronger model is reserved for code generation |
| NFR-COST-03 | Cost | No GPU or local model hosting; no cloud infrastructure bill — PostgreSQL, Docker and the sandbox all run locally via docker-compose |
| NFR-AVAIL-01 | Availability | Not applicable — a local/self-hosted demo deployment, not a hosted service with an SLA |
| NFR-MAIN-01 | Maintainability | One responsibility per file: `orchestrator.py` decides order, `step_executor.py` performs one step, `checkpoint_manager.py` persists state, `master_agent.py` classifies failures |
| NFR-MAIN-02 | Maintainability | Framework knowledge lives only in `adapters/`; nothing in `orchestrator/` changes when a new adapter is added |

---

## 5. Business Rules

| ID | Rule |
|---|---|
| BR-01 | The workflow is never restarted from zero — only the failed step is ever re-run (ADR-002) |
| BR-02 | Every newly built or acquired capability runs in the sandbox before being trusted, regardless of any other MVP simplification (ADR-004, non-negotiable) |
| BR-03 | A free/accessible tool is always checked for before the Capability Engine's build path is invoked (ADR-003) |
| BR-04 | The Master Agent (supervision/diagnosis) and the Orchestrator (execution) are documented and diagrammed as separate concepts, even though implemented as one Python service for the MVP (ADR-001) |
| BR-05 | LLM judgement is confined to exactly two functions — diagnosis (`master_agent.py`) and generation (`builder.py`); every other orchestration decision is deterministic code |
| BR-06 | The DAG is validated at creation, not at execution — an invalid workflow can never reach the Orchestrator |
| BR-07 | Resume is always an internal consequence of a successful diagnosis or a verified capability — never a user-triggered action (no public resume endpoint) |
| BR-08 | Every verified capability is registered in the shared capability registry so future gaps of the same kind are reused, not rebuilt (ADR-006) |
| BR-09 | Every loop and every retry in the system is bounded — one diagnosis call per failure, three build attempts per gap, a sandbox timeout, CPU/memory caps |
| BR-10 | A `FAILED` capability row is kept, never deleted, for the audit trail |
| BR-11 | An `INACTIVE` agent cannot be added to a new workflow, but executions that already reference it still resolve |
| BR-12 | Framework-specific knowledge lives only inside `adapters/`; supporting a new framework never requires changing `orchestrator/` |
| BR-13 | All errors are normalised to one shape before diagnosis ever sees them |
| BR-14 | Secrets never appear in the database, logs or exports |
| BR-15 | Adapters use REST/API plus one framework (LangChain or CrewAI) rather than mandating a single agent framework (ADR-005) |
| BR-16 | A custom, lightweight Python orchestrator is used for the MVP rather than adopting LangGraph, LangChain, RAG, vector databases or Celery/Redis up front (ADR-007) |

---

## 6. External Interfaces

- **User interface:** React + TypeScript + Tailwind + React Flow; a five-page sidebar (Dashboard, Agents, Workflows, Executions, Capabilities)
- **Engine API:** FastAPI REST at `/api` — `agents.py`, `workflows.py`, `executions.py`, `capabilities.py`; no WebSocket in the MVP, the frontend polls every 3 s (SSE only if FR-UI-015 is built)
- **Agent interfaces:** registered agents are reached over HTTP through an adapter (REST/API, LangChain or CrewAI, optionally MCP per FR-ADP-009)
- **LLM interface:** a hosted provider (OpenAI or Anthropic) used only for diagnosis (`master_agent.py`) and capability generation (`builder.py`)
- **Sandbox interface:** `sandbox_runner/`, a separate Docker image invoked by `capability_engine/sandbox.py` with file-mount I/O
- **Storage:** PostgreSQL + SQLAlchemy (agents, workflows, executions, checkpoints, capability registry); Alembic migrations; `.env` for secrets
- **CI:** GitHub Actions (`.github/workflows/ci.yml`)
- **Deployment:** `docker-compose.yml`, single machine, two environments (local dev, demo)

---

## 7. Use Cases

| ID | Name | Actor | Precondition | Main flow | Alternate flows |
|---|---|---|---|---|---|
| UC-01 | Register an agent | Developer | AGEM running | Open Agents page → fill form → submit → agent appears `ACTIVE` | Unreachable endpoint → rejected with a reason (FR-AGT-011) |
| UC-02 | Build a workflow | Developer | ≥1 agent registered | New workflow → pick agents in order → declare dependencies → save | Cyclic dependency → `WORKFLOW_CYCLE_DETECTED` shown inline |
| UC-03 | Run a single-agent workflow | Developer | Valid workflow | Start execution → agent executes → step succeeds → final result shown | Step fails → diagnosis begins |
| UC-04 | Run a multi-agent workflow | Developer | Valid multi-step workflow | Research → parallel analysis → Quality Check → Writer → final report | One branch fails → only that step pauses |
| UC-05 | Recover from a normal error | Developer | Step failed | Diagnosis classifies `NORMAL_ERROR` → step retries or stops → workflow continues | Retries exhausted → step `FAILED` |
| UC-06 | Recover from a capability gap (end-to-end) | Developer | Step failed with `MISSING_CAPABILITY` | Diagnosis classifies `CAPABILITY_GAP` → free-tool search (not found) → Capability Engine builds → sandbox → test → verify → register → resume exact step → final result | Verification fails → repair loop, bounded at 3 attempts |
| UC-07 | Reuse a verified capability | Developer | Capability already registered | A second agent hits the same gap → registry match found → capability granted without rebuilding | — |
| UC-08 | Monitor a live execution | Operator / Examiner | Execution running | Open Executions page → watch step states and recovery sub-stage update every 3 s | Capability gap in progress → distinct node state shown |
| UC-09 | Inspect a capability | Developer / Examiner | ≥1 capability registered | Open Capabilities page → view name, version, `verification_score`, source | Open the code viewer to read the generated source and test results (FR-CAP-020) |
| UC-10 | Trigger the demo moment reliably | Presenter | Demo environment seeded | Run `scripts/trigger_capability_gap.py` → watch the full recovery sequence live | — |
| UC-11 (LYK) | Approve a risky capability | Operator | Approval gate enabled (FR-CAP-022) | Capability verified → step `APPROVAL_PENDING` → approval card → Approve → step resumes | Reject → step `FAILED` with the rejection recorded |

---

## 8. Testing

### 8.1 Test levels

| Level | What it covers | Tool | When |
|---|---|---|---|
| Unit | Adapters, orchestrator transitions, checkpoint save/load, capability engine stages, sandbox invocation | Pytest | Every commit |
| Integration | API endpoints against a test database with a mock LLM provider | Pytest | Every commit |
| LLM evaluation | Diagnosis accuracy and capability generation success rate | Pytest (tagged, real API calls) | On prompt change; nightly |
| End-to-end demo | Full register → connect → deploy → run → capability-gap → recover → complete flow | `scripts/trigger_capability_gap.py` + manual walkthrough | Before Week 12 demo |

### 8.2 Test suite (from `backend/tests/`)

- **`test_adapters.py`** — a REST agent returns a valid response; a REST agent times out; an agent returns malformed JSON and is normalised into the standard error shape.
- **`test_orchestrator.py`** — a 2-step workflow runs in declared order; a cyclic definition is rejected at creation with `WORKFLOW_CYCLE_DETECTED`; a failed step pauses without re-running upstream steps; a resumed step reads its input from the checkpoint rather than recomputing it.
- **`test_capability_engine.py`** — a gap where a free tool exists skips the build path entirely; a gap with no free tool builds, tests, verifies and registers; a capability that fails verification three times marks the step `FAILED` instead of looping.
- **`test_sandbox.py`** — code attempting network access fails; code exceeding the timeout is killed; code exceeding the memory cap is killed; a benign function returns its result correctly.

### 8.3 LLM output evaluation
- **Diagnosis:** a fixed, hand-labelled set of 20 error examples (10 genuine capability gaps, 10 normal errors) in `backend/tests/fixtures/`; scored as classification accuracy against those labels, **18/20 pass bar**, re-run whenever the diagnosis prompt changes.
- **Capability generation:** scored against 5 canned gaps — the calculator gap used in the demo plus four others — as the proportion reaching `VERIFIED` within the 3-attempt cap.
- LLM calls are mocked in unit tests; real API calls happen only in these two evaluation runs, so CI stays free, fast and deterministic.

### 8.4 Entry and exit criteria
- **Entry:** feature complete for the week's Roadmap deliverable; the DB schema for the feature exists; a mock LLM provider is available.
- **Exit:** all Must-priority test cases pass; 70% line coverage across `orchestrator/`, `adapters/`, `capability_engine/`; the diagnosis accuracy bar (18/20) and the capability generation evaluation are both met; the demo trigger script reliably reproduces the capability-gap recovery sequence.

### 8.5 CI/CD
One GitHub Actions workflow, `.github/workflows/ci.yml`, on every push and pull request to `main`, running two jobs: (1) backend — install requirements, `ruff`/`flake8` lint, `pytest` across `backend/tests/`; (2) frontend — `npm ci`, `tsc --noEmit`, `npm run build`. No automatic deployment step — deployment is `docker-compose up`, run manually for the demo.

### 8.6 User acceptance checklist
1. Register an agent (REST) and one framework agent (LangChain or CrewAI).
2. Build and run a 2–5 agent workflow end-to-end.
3. Watch a normal error retry and resolve.
4. Trigger a capability gap and watch it recover: search → build → sandbox → test → verify → resume.
5. Confirm a second agent reuses the registered capability instead of rebuilding it.
6. Watch the Executions page reflect the recovery sub-stage live.
7. Inspect a capability's verification score and (if built) its generated source.
8. Confirm a cyclic workflow is rejected before it can run.
9. Confirm an in-use agent cannot be deleted.
10. Run the CI pipeline and confirm both jobs pass.

Each item is rated pass/fail; all Must-priority items must pass before the Week 12 demo.

---

## 9. Traceability Matrix

| Module | Requirements | Source (Master Doc) | Phase (Week) | Test cases |
|---|---|---|---|---|
| AGT | FR-AGT-001–012 | BRD §6/§7, PRD US-01, LLD Agent entity | 3 | `test_adapters.py` (indirect), seed script |
| ADP | FR-ADP-001–009 | BRD §6/§7, PRD US-03, ADR-005, LLD Key Interfaces | 3, 7 | `test_adapters.py` |
| WFL | FR-WFL-001–010 | BRD §6/§7, PRD US-02, LLD WorkflowAgent entity, Validation section | 2, 4 | `test_orchestrator.py` (cycle detection) |
| ORC | FR-ORC-001–016 | BRD §6/§7, PRD US-02/US-04, HLD Design Points, LLD Design Points | 4–6 | `test_orchestrator.py` |
| CKP | FR-CKP-001–009 | BRD §6/§7, PRD US-06, ADR-002, LLD Design Points | 5 | `test_orchestrator.py` (resume from checkpoint) |
| DIAG | FR-DIAG-001–013 | BRD §6/§7, PRD US-05/US-07, ADR-001, AI Safety/Guardrails | 3, 6 | LLM evaluation set (§8.3) |
| CAP | FR-CAP-001–022 | BRD §6/§7, PRD US-08, ADR-003/ADR-006, Failure Handling section | 4–8 | `test_capability_engine.py`, generation evaluation |
| SBX | FR-SBX-001–012 | BRD §8 NFR Security, ADR-004, Security Design | 6 | `test_sandbox.py` |
| UI | FR-UI-001–015 | PRD UX Requirements, BRD §6/§7 | 9–10 | Manual UAT checklist |
| API | FR-API-001–007 | LLD API Specification | 3+ | Integration tests |
| AUTH | FR-AUTH-001–007 | TRD Authentication/Authorization, Security Design | 2–3, 11 | Integration tests, security review |
| DAT | FR-DAT-001–008 | LLD Core Entities, LLD Design Points | 2 | Migration checks |
| OBS | FR-OBS-001–007 | TRD/HLD Observability, AI Safety/Guardrails | ongoing, 11 | Log inspection |
| DEP | FR-DEP-001–009 | Testing Strategy, CI/CD, Deployment Architecture | 1, 9–12 | Full test suite, demo script |

---

## 10. Phase List (12-week Roadmap)

| Wk | Person 1 (Ananya) | Person 2 (Guthal) | Deliverable | Main FRS modules |
|---|---|---|---|---|
| 1 | Repo, Docker Compose skeleton | Research diagnosis approaches, design capability flow | Shared repo running | DEP |
| 2 | DB schema, base FastAPI app | DB schema, capability engine flow on paper | Database running, API starts | DAT, API |
| 3 | `base_adapter.py` + `rest_adapter.py`, agent registration | `master_agent.py` — basic LLM classification call | One agent registered and callable | AGT, ADP, DIAG |
| 4 | `orchestrator.py` — sequential DAG (no failure yet) | `searcher.py` — free-tool check | 2-agent workflow runs end-to-end | WFL, ORC, CAP |
| 5 | `checkpoint_manager.py` | `builder.py` — LLM generates a tool | Workflow pauses and resumes from checkpoint | CKP, CAP |
| 6 | Wire orchestrator → master_agent on failure | `sandbox_runner/` isolated Docker execution | Failure detected, classified, sandbox runs | ORC, DIAG, SBX |
| 7 | `langchain_adapter.py` or `crewai_adapter.py` | `tester.py` + `verifier.py` | Full capability gap loop end-to-end | ADP, CAP |
| 8 | `registry.py` integration | Fix capability engine loops, add retry limit | Second agent reuses a verified capability | CAP |
| 9 | React frontend — Dashboard, Workflow Builder | All backend tests written and passing | Frontend shows workflow graph | UI, DEP |
| 10 | Execution monitor page | `trigger_capability_gap.py`, polish capability engine | Full demo flow works reliably | UI, CAP, DEP |
| 11 | Frontend polish, connect all pages to real API | Security review — sandbox limits, input validation, auth | Everything integrated, running in Docker | UI, AUTH |
| 12 | Demo rehearsal, README, architecture diagrams | Viva Q&A prep, final documentation | Submittable, demonstrable, defensible | DEP |

LYK items (FR-*-0xx marked "LYK") are not pinned to a specific week — they are picked up opportunistically wherever the owning module has slack, per §1.2.

---

## 11. Open Issues

| ID | Issue | Recommended | Needed by |
|---|---|---|---|
| OI-01 | Which framework adapter — LangChain or CrewAI | Either is acceptable per ADR-005; pick based on which the team is more comfortable with by Week 7 | Week 7 |
| OI-02 | Redis/Celery addition | Only if the async job queue becomes a real pain point; not before Week 8 (ADR-007) | Week 8 |
| OI-03 | MCP adapter (FR-ADP-009) | Build only if Week 8 lands on schedule — it is the single highest-upside LYK item | Week 8 checkpoint |
| OI-04 | Approval gate (FR-CAP-022) vs. BR-07 (no public resume endpoint) | If built, the approval endpoint must be narrowly scoped to capability approval only, explicitly distinct from a general resume endpoint | Before FR-CAP-022 is built |
| OI-05 | Real tool discovery replacing the hardcoded search (FR-CAP-003) | Out of scope for this project; note as Future Scope in the README | — |
| OI-06 | Formal/property-based verification replacing the type/range check (FR-CAP-010) | Out of scope for this project; note as Future Scope in the README | — |
| OI-07 | Capability versioning and supersession | Out of scope for this project; note as Future Scope in the README | — |