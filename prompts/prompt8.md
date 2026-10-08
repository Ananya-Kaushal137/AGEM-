# Prompt 8 — The Orchestrator: Running a Workflow (explained)

## 1. The short version

Prompt 7 let you **save** a workflow. Prompt 8 lets you **run** it.

You send `POST /api/workflows/{id}/executions` with a task. AGEM answers **at once** with an `execution_id`, then, in the background:

1. finds the steps that are **READY** (waiting, and everything they depend on has finished),
2. builds each step's input from earlier steps' outputs (the `input_mapping` from Prompt 7),
3. calls each agent through its adapter,
4. saves the result, the new status and a checkpoint **together**,
5. repeats until nothing is READY.

Steps that don't depend on each other run **at the same time**.

Whatever goes wrong when an agent is called, it is turned into **one standard error shape** with one of six fixed codes. That is what Prompt 11's diagnosis will read.

**Hotel picture:** Prompt 7 booked the tour plan. Prompt 8 is the **driver on the day**. The driver follows the numbered list. They never re-plan. At each stop they write in the logbook before driving on. If two stops are independent, two minibuses go at once. If a guide doesn't turn up, the driver writes it down in the standard form ("guide absent: CONNECTION_ERROR") and, for now, the tour stops. Prompt 11 adds the "call the manager and fix it" part.

---

## 2. How Prompt 8 connects to earlier prompts

| From | What Prompt 8 uses |
|---|---|
| **Prompt 2** (database) | `executions`, `execution_steps`, `checkpoints`. The 5-state `StepStatus` and the `can_transition` guard. `recovery_stage` (stays `NONE` for now) |
| **Prompt 3** (API skeleton) | Error envelope, `X-API-Key`, `StrictModel` |
| **Prompt 4** (adapters) | `RestAdapter.execute()`, which **raises** on transport problems. Prompt 8 catches them in one place |
| **Prompt 5** (agents) | Agent endpoint and encrypted credentials. The step executor decrypts them and passes them to the adapter |
| **Prompt 6** (diagnosis) | The `ErrorType` constants. Prompt 8 emits **exactly** those strings, so the diagnosis rules will match them |
| **Prompt 7** (workflows) | The saved `step_order`, `depends_on` and `input_mapping`. Prompt 8 follows them and never sorts again |

**And forward:**
- **Prompt 9** moves the checkpoint writing into `CheckpointManager.save/load` and adds the **pause** checkpoint.
- **Prompt 11** changes "a failed step ends FAILED" into "a failed step PAUSES, then `diagnose_failure` decides".
- **Prompt 17** adds `GET /api/executions/{id}` for the Executions page.

---

## 3. What the task asked for

From `prompts/CLAUDEBUILDPROMPTS`, Prompt 8:

- **FR-ORC-001**: `POST /api/workflows/{id}/executions` returns `execution_id` immediately; the run goes through `BackgroundTasks`
- **FR-ORC-002**: `Orchestrator.run_execution(execution_id) -> None`
- **FR-ORC-003 / 004**: follow the stored `step_order`. READY = `PENDING` + all `depends_on` `SUCCEEDED`. Sequential first, then siblings under `asyncio.gather`
- **FR-ORC-005**: step N's input includes the declared upstream output fields
- **FR-ORC-012**: `StepExecutor.run_step(step_id) -> dict`
- **FR-ORC-008 / 009**: the 5-state machine, every change through `can_transition`
- **FR-ORC-010**: `Execution.status` is rolled up from the steps
- **FR-ORC-011**: the `recovery_stage` field (exists, stays `NONE`)
- **FR-ORC-013**: status and checkpoint in **one** transaction
- **FR-ADP-005 / FR-DIAG-002 / BR-13**: every error normalised in `step_executor.py`, with one of the six codes and an extra `capability` field when the agent names the missing tool

**Done when:** a 2-agent workflow runs end to end in declared order, and bookkeeping stays under **300 ms per step** (FR-ORC-014).

**Do NOT:** add failure handling, diagnosis or recovery (that is Prompt 11).

---

## 4. Key words

| Word | Meaning |
|---|---|
| **Execution** | One run of one workflow. Has its own status |
| **ExecutionStep** | One step inside one run. Starts `PENDING` |
| **READY** | A step that is `PENDING` and whose dependencies have all `SUCCEEDED` |
| **Roll-up** | Working out the execution's status from its steps: any `PAUSED` → `PAUSED`; any `FAILED` → `FAILED`; all `SUCCEEDED` → `SUCCEEDED` |
| **Normalise** | Turn every kind of failure (crash, timeout, bad JSON, the agent's own `FAILED`) into one shape |
| **`asyncio.gather`** | Python's way to wait for several things at the same time |
| **Background task** | Work FastAPI does **after** it has already answered the request |

---

## 4b. Judgment calls — please check

The docs did not decide these. Each is easy to change.

| Question | Decision | Why |
|---|---|---|
| Where are the run's **task** and **starting input** stored? | **New migration** `8e2f4a6b1c3d`: `executions.task` (text) and `executions.input` (JSONB). ⚠ Architecture §14.1 does not list them | The agent contract needs `task`, and `"from": "input"` needs the starting values. No existing column fits. Server defaults keep old rows valid |
| What does the start request look like? | `{"task": "...", "input": {...}}` → **202** `{execution_id, status: "PENDING"}` | 202 = "accepted, still working". Written in `docs/api-spec.md` |
| What does a step receive as `input`? | **Only** the fields in its `input_mapping`. Empty mapping → `{}` (it still gets `task`) | `input_mapping` is the declared wiring (FR-WFL-005). No hidden "pass everything" rule |
| Where do upstream values come from? | The upstream step's **checkpoint** (`state.outputs`) | US-04 / FR-ORC-005 say "read from the Checkpoint" |
| A mapped field is **missing** from the upstream output | The step fails with `AGENT_ERROR` ("input_mapping cannot be satisfied: …") **without** calling the agent | Calling it with half an input would only give a confusing error later |
| Agent replies `FAILED` with an error other than `MISSING_CAPABILITY` (even `"TIMEOUT"`) | `AGENT_ERROR` | In the frozen contract, only `MISSING_CAPABILITY` has a meaning. `TIMEOUT` etc. are things **AGEM** observes, not words an agent can claim. The LLM diagnosis handles `AGENT_ERROR` |
| HTTP 4xx from the agent | `AGENT_ERROR` (5xx is `HTTP_5XX`) | Matches what `rest_adapter.py` already documents |
| `httpx.ConnectTimeout` | `CONNECTION_ERROR`, not `TIMEOUT` | The agent was never reached. Both go to `NORMAL_ERROR` anyway |
| Success reply where `output` is not an object | `INVALID_JSON` | The contract says `output: {...}` |
| A step fails (no recovery yet) | Step → `FAILED`, its normalised error stored, the run stops, execution → `FAILED` with `error_summary`. Already-running siblings are allowed to finish | Prompt 8 must not add pause/diagnosis. Prompt 11 replaces this |
| Checkpoint writing | Done **inside `orchestrator.py`** for now (success checkpoint only) | P14 says `checkpoint_manager.py` owns this, but that file **is** Prompt 9. Prompt 9 moves it there |
| `final_output` | Output of the last step. If several steps have nothing after them: `{"<key>": output, ...}` | Docs say "save the final output" without a shape |
| `run_execution` called twice | The second call does nothing (execution not `PENDING` any more) | Never call agents twice |
| A step whose framework has no adapter (`langchain` before Prompt 15) | `AGENT_ERROR` "No adapter is built yet…" | Still the one error shape |

---

## 5. Everything that was built, part by part

### Part 1 — The step executor (`backend/orchestrator/step_executor.py`)
`StepExecutor.run_step(step_id)` does **one** step:
1. reads the step, its agent and the run's `task` from the database,
2. picks the adapter by `framework` (only `rest` exists today) and decrypts the credentials,
3. sends `{task, input, context: {}}`,
4. returns the standard result. It **never raises** for anything the agent does.

The mapping, all in one place:

| What happened | `error_type` |
|---|---|
| Agent said `{"status":"FAILED","error":"MISSING_CAPABILITY","capability":"x"}` | `MISSING_CAPABILITY` + `"capability": "x"` |
| Read/write timeout (`httpx.TimeoutException`, `asyncio.TimeoutError`) | `TIMEOUT` |
| Could not connect, connection dropped, connect timeout | `CONNECTION_ERROR` |
| HTTP 500–599 | `HTTP_5XX` |
| Body is not JSON / not the contract shape / `output` not an object | `INVALID_JSON` |
| Anything else (4xx, the agent's own error text, a Python crash) | `AGENT_ERROR` |

`raw_error` is cut to 4000 characters (the same limit `master_agent.py` sends to the LLM). Nothing is logged except status and `error_type` (no inputs, outputs or error text, FR-AUTH-006).

### Part 2 — The Orchestrator (`backend/orchestrator/orchestrator.py`)
`Orchestrator.run_execution(execution_id)`:

```
plan = this run's steps, matched to the workflow's steps by step_order
while READY steps exist:
    run them all together with asyncio.gather
```

For **one** step:
1. **Transaction 1:** build the input from the mapping, move `PENDING → RUNNING`, `attempts + 1`, re-roll the execution. Commit.
2. Call `StepExecutor.run_step` (no database open while the agent works).
3. **Transaction 2:** success → save `output`, move `RUNNING → SUCCEEDED`, add the **checkpoint**, re-roll. Failure → save the error, move `RUNNING → FAILED`, re-roll. Commit. If anything in this transaction fails, **nothing** is written, so a step can never be `SUCCEEDED` without its output and checkpoint (FR-ORC-013).

Every status change goes through `_move()`. It calls `can_transition` and raises `IllegalTransition` if the move isn't allowed. It then re-computes the execution status with `rollup()`. The execution status is **never** set any other way (FR-ORC-010). The roll-up also sets `started_at`, `finished_at`, `final_output` and `error_summary`.

The success checkpoint holds the five things Architecture §11.3 lists: `current_step`, `completed_steps` + `outputs` (prior successful outputs), `pending_inputs`, `workflow_state`, `recovery` (`recovery_stage`, `attempts`).

Each finished step logs `overhead_ms`: the time spent on bookkeeping, not counting the agent call. That is the FR-ORC-014 number.

### Part 3 — The endpoint (`backend/app/api/executions.py`)
`POST /api/workflows/{id}/executions`:
- workflow must exist (404) and be `ACTIVE` (422),
- creates the `Execution` (`PENDING`) and one `ExecutionStep` per workflow step (`PENDING`, `step_order` copied),
- hands `run_execution` to `BackgroundTasks` and answers **202** straight away.

The background run opens its **own** database sessions, because the request's session is closed by then.

### Part 4 — Schema + migration
- `backend/app/schemas/execution.py`: `ExecutionCreate {task, input}`, `ExecutionStarted {execution_id, status}`.
- `Execution` model: `task`, `input` added.
- `backend/alembic/versions/8e2f4a6b1c3d_execution_task_and_input.py`: the new migration (Prompts 1–2's migrations untouched).

---

## 6. How it was checked

**Tests: 207 pass (55 new).**

In `test_orchestrator.py` (fake agents at the adapter, SQLite):
- **Done-when:** Research → Writer runs in order. Writer's input is `{company, revenue}` taken from Research's output. Execution `SUCCEEDED`, `final_output` = Writer's output
- Every agent gets `{task, input, context: {}}`, and only the mapped fields
- READY steps start in stored `step_order` (C, A, B stays C, A, B)
- **Siblings overlap**: Finance and Market (0.3 s each) both start before either ends, and the whole run takes < 0.55 s (one after the other would be ≥ 0.6 s)
- Several end steps → keyed `final_output`
- A `MISSING_CAPABILITY` reply → step `FAILED` with exactly `{status, error_type, raw_error, capability}`, downstream stays `PENDING`, execution `FAILED` with an `error_summary`
- A failed sibling lets the other sibling finish; nothing is left `RUNNING`
- Missing mapped field → `AGENT_ERROR`, agent not called
- One checkpoint per successful step, with the §11.3 keys
- If the checkpoint write fails, the step is **not** marked `SUCCEEDED` (one transaction)
- Downstream input really comes from the **checkpoint**
- Running the same execution twice calls agents once; unknown execution is ignored
- An illegal transition (`SUCCEEDED → RUNNING`) raises `IllegalTransition`
- `langchain` agent (no adapter yet) → normalised `AGENT_ERROR`
- **11 exception types** → the right `error_type`; **8 agent replies** → the right shape
- The codes `step_executor` emits are **exactly** the strings `master_agent`'s rules match
- Roll-up table (7 cases)
- **FR-ORC-014:** a 5-step chain logs `overhead_ms` < 300 for every step
- `orchestrator.py` imports no `httpx` / `adapters` (P6)

In `test_api.py` (**real HTTP agents** behind `python_wrapper.py`):
- Research → Writer through the API: 202 + `PENDING`, then both steps `SUCCEEDED`, `final_output` = `"Investment report — Tesla: revenue 96.77"`
- Finance raising `MissingToolError` → stored error `MISSING_CAPABILITY` + `capability: calculate_compound_interest`
- Agent endpoint unreachable → `CONNECTION_ERROR`
- Unknown workflow → 404; `ARCHIVED` workflow → 422 and nothing created; bad bodies → 422

**Migration:** `alembic heads` = `8e2f4a6b1c3d`. The offline SQL is two `ALTER TABLE executions ADD COLUMN …` lines.

⚠ **Not yet checked on real PostgreSQL/Docker** (Docker isn't running on this machine). Please run, as for Prompt 7:
```
docker compose up -d
docker compose exec backend alembic upgrade head
```
then start the demo workflow from Prompt 7 with `POST /api/workflows/{id}/executions`. With the real demo agents, **Finance will fail** with `MISSING_CAPABILITY`. That is expected until Prompts 11–14.

---

## 7. What Prompt 8 does NOT do (comes later)

- **Pause, diagnosis, retry, Capability Engine, resume** → Prompts 9, 11, 14. Today a failed step just ends `FAILED`
- `CheckpointManager.save/load` and the **pause** checkpoint → **Prompt 9**
- `GET /api/executions/{id}` and step detail → with the Executions page (**Prompt 17**)
- Restarting runs that were `RUNNING` when the backend crashed (FR-CKP-009) → Prompt 9
- LangChain adapter → **Prompt 15**
- Shared state object (FR-ORC-006), branching (FR-ORC-016) → not asked for here

---

## 8. Files changed in Prompt 8

| File | New / changed |
|---|---|
| `backend/orchestrator/step_executor.py` | written: one step + all error normalisation |
| `backend/orchestrator/orchestrator.py` | written: scheduler, transitions, roll-up, success checkpoint |
| `backend/app/api/executions.py` | changed: `POST /api/workflows/{id}/executions` |
| `backend/app/schemas/execution.py` | new |
| `backend/app/models/execution.py` | changed: `task`, `input` on `Execution` |
| `backend/alembic/versions/8e2f4a6b1c3d_execution_task_and_input.py` | new migration |
| `backend/tests/test_orchestrator.py` | changed: 46 new tests |
| `backend/tests/test_api.py` | changed: 9 new tests |
| `docs/api-spec.md` | changed: "Starting an execution" section |
| `prompts/prompt8.md` | new: this file |

---

## 9. Where you are now

- [x] Prompts 1–7
- [x] Prompt 8: workflows **run**, in order, siblings in parallel, errors normalised
- [ ] Check Prompt 8 on Docker/PostgreSQL (`alembic upgrade head`, then run the demo workflow)
- [ ] Commit + push to `staging`
- [ ] Prompt 9: CheckpointManager, pause and resume

---

## 10. Check questions

1. Why does the endpoint answer **before** the workflow has run? What would go wrong if it waited?
2. Why are status, output and checkpoint written in **one** transaction?
3. An agent's server crashes with HTTP 503. Which `error_type` is stored, and what will Prompt 11's diagnosis decide?
4. Why must `step_executor.py` use **exactly** the strings in `ErrorType`, and not, say, `"timeout"`?
5. Finance and Market both depend only on Research. When does Writer (which needs both) start?
6. Why doesn't the Orchestrator ever sort the steps itself?
