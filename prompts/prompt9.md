# Prompt 9 — Checkpoint Manager: Pause and Resume (explained)

## 1. The short version

Before Prompt 9, when a step failed, the whole run just ended as `FAILED`. Now:

1. Only the failed step **pauses** (`PAUSED`). Everything finished before it is kept.
2. AGEM saves a **checkpoint** at that moment: what was done, and exactly what the paused step was given.
3. Later, AGEM can **resume that one step** from its checkpoint. Earlier steps are **never run again**.
4. If a step fails **3 times**, it stops pausing and becomes `FAILED`, so nothing waits forever.

This is the heart of AGEM: **stop at the problem, fix it, continue from the same spot**, never restart from zero (BR-01).

**Hotel picture:** a guest's tour (the workflow) stops because the boat (Finance) has no fuel. The tour is not cancelled and the museum is not visited again. Reception writes down "museum done, waiting at the boat, guests: 12" (the checkpoint). When fuel arrives, the tour continues **from the boat**.

---

## 2. How Prompt 9 connects to earlier and later prompts

| From | What Prompt 9 uses / changes |
|---|---|
| **Prompt 2** (database) | The `checkpoints` table, the `PAUSED` status, and the `can_transition` rules (`RUNNING → PAUSED`, `PAUSED → RUNNING`, `PAUSED → FAILED` were already allowed) |
| **Prompt 4** (adapter/wrapper) | `context` in the agent request. Resume can now put `tool_results` there, which the Finance demo agent already reads |
| **Prompt 8** (orchestrator, Guthal) | The scheduler stays the same. Changes: (1) a failure now **pauses** instead of failing; (2) checkpoint writing moved into `CheckpointManager`; (3) new `resume_step()`; (4) `run_step()` can now pass a `context` |

**And forward:**
- **Prompt 11** decides *what to do* with a paused step: diagnose it, then call `resume_step()` to retry (`NORMAL_ERROR`) or hand it to the Capability Engine (`CAPABILITY_GAP`).
- **Prompt 14** calls `resume_step(step_id, tool_results={...})` with the verified tool's result. That is exactly what we did by hand in the live check below.

---

## 3. What the task asked for

From `prompts/CLAUDEBUILDPROMPTS`, Prompt 9:

- `CheckpointManager.save(step_id, state)` and `CheckpointManager.load(execution_id) -> dict` (FR-CKP-001)
- **FR-CKP-002** — write a checkpoint **twice per step**: when it succeeds, and the moment it pauses
- **FR-CKP-003** — contents: current step, prior successful outputs, pending inputs, workflow state, recovery information
- **FR-CKP-004** — only the failed step becomes `PAUSED`; upstream steps untouched
- **FR-CKP-005** — resume reads its input from the checkpoint; upstream steps never recomputed
- **FR-CKP-006** — resuming an already-`SUCCEEDED` step does nothing
- **FR-CKP-008** — after 3 failed attempts, `PAUSED` → `FAILED`
- **Do NOT** add a public resume endpoint (FR-CKP-007, BR-07)

**Done when:** a workflow pauses at a failed step and resumes from the checkpoint without re-running any upstream step.

---

## 4. Key words

| Word | Meaning |
|---|---|
| **Checkpoint** | A saved snapshot (one JSON object in the `checkpoints` table) of where the run is |
| **Pause checkpoint** | The snapshot written the moment a step fails. It holds the step's input and the error |
| **Resume** | Run the paused step again, using the input from its pause checkpoint |
| **Idempotent** | Doing it twice has the same effect as doing it once. Resuming twice must not call the agent twice |
| **Attempt** | One call to the agent for a step. `attempts` counts them |

---

## 5. Decisions we made (the docs did not say)

| Question | Decision | Why |
|---|---|---|
| Should a failure pause the step **now**, or only in Prompt 11? | **Now** | Prompt 9's "done when" needs a paused step. Prompt 11 adds the diagnosis *after* the pause |
| What does "3 failed attempts" count? | **Total** calls: the first run plus 2 resumes. The 3rd failure → `FAILED` | FR-CKP-008 says "3 failed attempts". ⚠️ Prompt 11 talks about "retry at most 3 times", which could mean 4 calls in total. If Prompt 11 needs that, change `MAX_ATTEMPTS` in one place |
| What happens on the 3rd failure? | Pause checkpoint written first, **then** `PAUSED → FAILED` | Matches "a PAUSED step moves to FAILED", and keeps the failed input visible for inspection |
| Do other branches keep running when one step pauses? | **Yes**: independent siblings still run; only the paused step's dependants wait | Architecture §11.2: the run stops only when a step is `FAILED` |
| How does a resume get the tool's result to the agent? | `resume_step(step_id, tool_results={...})` → the agent receives `context.tool_results` | Matches the frozen agent contract (`docs/api-spec.md`) and the Finance demo agent |
| Where is `save` committed? | `save` only **adds** the checkpoint to the open database session; the orchestrator commits it **together with** the status change | So a step can never be `PAUSED` or `SUCCEEDED` without its checkpoint (FR-ORC-013) |

---

## 6. Everything that was built, part by part

### Part 1 — The checkpoint manager
**File:** `backend/orchestrator/checkpoint_manager.py`

```python
CheckpointManager(db).save(step_id, state)      # adds one checkpoint (checks the 6 keys are there)
CheckpointManager(db).load(execution_id)        # newest checkpoint of the run, or {}
CheckpointManager(db).output_of(step_id)        # a finished step's output, from its checkpoint
CheckpointManager(db).paused_input(step_id)     # the input saved when the step paused
build_state(step, step_keys, error=None)        # builds the checkpoint contents
```

What a **pause checkpoint** looks like (real example, Finance):

```json
{
  "current_step":    "<finance step id>",
  "completed_steps": ["<research step id>"],
  "outputs":         { "<research step id>": {"company": "Tesla", "revenue": 96.77, "profit": 14.97, ...} },
  "pending_inputs":  { "<finance step id>":  {"company": "Tesla", "revenue": 96.77, "profit": 14.97} },
  "workflow_state":  { "execution_status": "PAUSED", "task": "...",
                       "step_status": {"<research>": "SUCCEEDED", "<finance>": "PAUSED", ...}, ... },
  "recovery":        { "recovery_stage": "NONE", "attempts": 1,
                       "error": {"error_type": "MISSING_CAPABILITY", "capability": "calculate_compound_interest", ...} }
}
```

These are the five things Architecture §11.3 asks for: current step, prior outputs, pending inputs, workflow state, recovery information.

### Part 2 — A failure now pauses
**File:** `backend/orchestrator/orchestrator.py` → `_finish()`

```
agent replied SUCCEEDED → step SUCCEEDED + success checkpoint                  (one commit)
agent replied FAILED    → step PAUSED   + pause checkpoint (input + error)     (one commit)
                          if this was attempt 3 → PAUSED → FAILED              (same commit)
```

The execution's status rolls up as before: `PAUSED` if any step is paused.

### Part 3 — Resume
**File:** `backend/orchestrator/orchestrator.py` → `resume_step(step_id, tool_results=None)`

```
1. load the step
   not PAUSED (SUCCEEDED, already RUNNING, FAILED)?  → do nothing      (FR-CKP-006)
   already 3 attempts?                              → FAILED, stop    (FR-CKP-008)
2. take the input from the PAUSE CHECKPOINT                            (FR-CKP-005)
3. PAUSED → RUNNING, attempts + 1                                      (commit)
4. call the agent once, with context.tool_results if given
5. success → SUCCEEDED + checkpoint; failure → PAUSED again (or FAILED on attempt 3)
6. carry on with the rest of the workflow (the normal scheduler loop)
```

Step 3 is committed **before** the agent is called. So if two resumes start at the same moment, only the first finds the step `PAUSED`; the second sees `RUNNING` and does nothing.

### Part 4 — No public resume
There is **no** URL to resume a step. `resume_step` is a Python method that only AGEM itself will call: after a diagnosis (Prompt 11) or a verified tool (Prompt 14). A test checks that no API route contains "resume".

### Part 5 — Small change in the step executor
**File:** `backend/orchestrator/step_executor.py`
`run_step(step_id, context=None)`: the agent request's `context` is now whatever the caller passes (empty by default, so Prompt 8's behaviour is unchanged).

---

## 7. How it was checked

**Tests — 218 pass:**
- **10 new** for Prompt 9:
  - `save`/`load` work, and `save` refuses a checkpoint missing any of the 6 parts
  - checkpoint on success and on pause, with the right contents
  - only the failed step pauses
  - **resume without re-running upstream** (Research called once)
  - resume uses the **checkpoint** input even if the step row was changed
  - `tool_results` reach only the resumed agent
  - resuming a finished step does nothing
  - two resumes at once call the agent once
  - 3 failures → `FAILED`, and a 4th resume does nothing
  - no public resume route
- **7 Prompt 8 tests updated**: they expected `FAILED` after one failure and now expect `PAUSED`. Nothing else in them changed.

**Checked on real PostgreSQL (Docker):**

The demo workflow **Investment report** (Research → Finance → Fact Checker → Writer):

| After the run | status | attempts | checkpoints |
|---|---|---|---|
| Research | SUCCEEDED | 1 | 1 |
| Finance | **PAUSED** | 1 | 1 (pause) |
| Fact Checker | PENDING | 0 | 0 |
| Writer | PENDING | 0 | 0 |
| **execution** | **PAUSED** | | |

Then, inside the backend container, Finance was resumed **by hand** with the tool result, the way Prompt 14 will do it automatically:

```python
Orchestrator().resume_step(finance_step_id, {"calculate_compound_interest": 1628.89})
```

| After the resume | status | attempts | checkpoints |
|---|---|---|---|
| Research | SUCCEEDED | **1** (not re-run) | 1 |
| Finance | SUCCEEDED | 2 | 2 (pause + success) |
| Fact Checker | SUCCEEDED | 1 | 1 |
| Writer | SUCCEEDED | 1 | 1 |
| **execution** | **SUCCEEDED** | | |

The container logs confirm it: the Research agent received **1** call, Finance **2**. Final report:

```
Investment report: Tesla
Revenue: $96.77B, profit: $14.97B (margin 15.5%).
Projected value of the investment: 1628.89.
Figures verified by the Fact Checker.
```

---

## 8. What Prompt 9 does NOT do (comes later)

- **Deciding** what to do with a paused step (diagnose, retry, or build a tool) → **Prompt 11**. Until then, a paused run simply stays `PAUSED`.
- Building the missing tool and resuming automatically → **Prompts 10–14**.
- Restarting a run that was `RUNNING` when the backend crashed. Every status change is already committed before the next step starts, so a crash leaves a readable state (FR-CKP-009), but nothing picks the run up again yet.

---

## 9. Files changed in Prompt 9

| File | New / changed |
|---|---|
| `backend/orchestrator/checkpoint_manager.py` | written: `CheckpointManager`, `build_state` |
| `backend/orchestrator/orchestrator.py` | changed: failure → pause, `resume_step`, `MAX_ATTEMPTS = 3`, uses `CheckpointManager` |
| `backend/orchestrator/step_executor.py` | changed: `run_step(..., context=None)` |
| `backend/tests/test_orchestrator.py` | changed: 10 new tests, 6 updated for `PAUSED` |
| `backend/tests/test_api.py` | changed: 1 test updated for `PAUSED` |
| `prompts/prompt9.md` | new: this file |

---

## 10. Check questions

1. Why is the step's input saved in the **pause checkpoint** instead of being rebuilt from Research's output at resume time?
2. Why is `PAUSED → RUNNING` committed **before** the agent is called during a resume?
3. Why is there no "Resume" button or URL?
4. After the live resume, how do we **know** Research was not run again? Name two pieces of evidence.
5. A step has failed twice. What happens on the third failure, step by step?
