# Failure Diagnosis in AGEM

This document explains, in simple words, what AGEM does when something goes wrong in a workflow.
It is the detailed version of Sections 4–6 of `docs/final_flow.md` (the full project flow). The decisions here are recorded in ADR-008.

---

## 1. The big idea

A workflow is a chain of steps. Each step is done by an agent.

Sometimes a step **fails**. When that happens, AGEM does **not** start the whole workflow again. Instead it:

1. **Stops only the broken step** (the other steps keep their results).
2. **Finds out why** the step failed.
3. **Fixes the problem** — either by trying again, or by getting the missing tool.
4. **Continues from the same step** where it stopped.

Finding out *why* a step failed is called **failure diagnosis**.

---

## 2. Who does the diagnosis?

The **Master Agent**.

The name sounds big, but the Master Agent is **not** a separate program running all the time. It is just **one Python function** that runs **only when a step fails**.

| Item | Value |
|---|---|
| File | `backend/orchestrator/master_agent.py` |
| Function | `diagnose_failure(step_id, error)` |
| Answer it gives | `"NORMAL_ERROR"` or `"CAPABILITY_GAP"` |
| Who calls it | The Orchestrator (`orchestrator.py`), only when a step fails |
| Who owns it | Person 2 (Guthal) |

### Master Agent vs Orchestrator

- **Orchestrator** = the worker. It runs the steps, passes data between agents and does the fixing. It is plain code with fixed rules.
- **Master Agent** = the doctor. It looks at the error and says what went wrong.

They live in the same service, but they have different jobs.

---

## 3. The only two answers

The Master Agent must choose **one** of these two answers:

### NORMAL_ERROR
The agent **can** do the job, but something went wrong this time.

Examples:
- The agent took too long (timeout).
- The agent's server was down.
- The agent sent back broken data.

**Fix:** try again.

### CAPABILITY_GAP
The agent **cannot** do the job, because it is missing a tool or skill.

Example:
- The Finance Agent needs to calculate compound interest, but has no calculator tool for it.

**Fix:** get or build the missing tool.

---

## 4. What happens before diagnosis

Two things happen first:

### Step 1 — The error is put into one standard shape
Agents are built with different frameworks, so their errors look different. `step_executor.py` turns **every** error into the same shape:

```json
{
  "status": "FAILED",
  "error_type": "MISSING_CAPABILITY",
  "raw_error": "I cannot calculate compound interest",
  "capability": "calculate_compound_interest"
}
```

- `error_type` = a short code for the kind of error
- `raw_error` = the original error message
- `capability` = the name of the missing tool (only when the agent tells us; the Capability Engine needs it)

`error_type` is **always** one of these fixed codes, defined once as constants:

| Code | Meaning |
|---|---|
| `MISSING_CAPABILITY` | The agent says it is missing a tool |
| `TIMEOUT` | The agent took too long |
| `CONNECTION_ERROR` | The agent could not be reached |
| `HTTP_5XX` | The agent's server crashed |
| `INVALID_JSON` | The agent sent back broken data |
| `AGENT_ERROR` | Anything else (the fallback) |

The rules in Section 5 match these **exact** spellings. A different spelling (for example `TOOL_ERROR`) would silently skip the rules.

Because every error looks the same, the Master Agent only needs to understand one format.

### Step 2 — Only the failed step is paused
- The failed step's status becomes **PAUSED**.
- A **checkpoint** is saved. A checkpoint is like a "save game": it stores the inputs, the outputs of earlier steps and where the workflow stopped.
- Earlier steps are **not** run again. Their results are safe in the checkpoint.

---

## 5. How the diagnosis works (two stages)

The Master Agent checks in **two stages**: simple rules first, and the LLM only if the rules cannot decide.

### Stage 1 — Simple rules (no LLM)

Some errors are obvious. For these, AGEM does not need to ask an AI.

| If `error_type` is... | Answer |
|---|---|
| `MISSING_CAPABILITY` | `CAPABILITY_GAP` (the agent told us itself) |
| `TIMEOUT` | `NORMAL_ERROR` |
| `CONNECTION_ERROR` | `NORMAL_ERROR` |
| `HTTP_5XX` (server error) | `NORMAL_ERROR` |
| `INVALID_JSON` (broken reply) | `NORMAL_ERROR` |

### Stage 2 — Ask the LLM (only if no rule matched)

Some errors are messy text, for example:
`"ValueError: cannot compute IRR for this cash flow"`

A simple rule cannot understand every sentence like this, but an LLM (like Claude or GPT) can read language. So AGEM sends the error to the LLM with a strict question.

**What AGEM sends to the LLM:**

```
SYSTEM: You diagnose failures in an AI agent workflow.
Classify the failure as exactly one of:
- NORMAL_ERROR: the agent has the ability, but something went wrong
- CAPABILITY_GAP: the agent is missing a tool or skill it needs
Reply ONLY with JSON: {"diagnosis": "...", "capability": "...", "reason": "..."}

USER: Agent: Finance Agent
Error: "ValueError: cannot compute IRR for this cash flow"
```

**What the LLM sends back:**

```json
{
  "diagnosis": "CAPABILITY_GAP",
  "capability": "calculate_irr",
  "reason": "The agent has no tool to compute IRR"
}
```

### Safety rules for the LLM call

- The LLM **must** reply in JSON. AGEM checks the reply with **Pydantic** (a Python library that checks data shape).
- If the reply is wrong, AGEM asks again (**up to 2 more times**) and tells the LLM what was wrong.
- If it is **still** wrong, or the LLM takes **more than 20 seconds**, AGEM picks **NORMAL_ERROR**. This is the safe choice, because a retry is harmless, while building a tool by mistake is not.
- **At most 1** LLM call per failure, and **0** when a rule matched.
- A **cheaper model** is used here, because this is a simple yes/no-style question.

### Why rules first, LLM second?

- **Faster** — most failures skip the LLM.
- **Cheaper** — fewer LLM calls cost less money.
- **More reliable for the demo** — the demo agent sends `MISSING_CAPABILITY` directly, so the key demo moment does not depend on the LLM.
- **Still smart** — messy errors still go to the LLM.

### The code shape

```python
RULE_GAP    = {"MISSING_CAPABILITY"}
RULE_NORMAL = {"TIMEOUT", "CONNECTION_ERROR", "HTTP_5XX", "INVALID_JSON"}

def diagnose_failure(step_id, error) -> str:
    # Stage 1: simple rules, no LLM
    if error["error_type"] in RULE_GAP:
        return "CAPABILITY_GAP"
    if error["error_type"] in RULE_NORMAL:
        return "NORMAL_ERROR"

    # Stage 2: only unclear errors go to the LLM
    return llm_classify(error)   # JSON + Pydantic, 2 retries, safe default NORMAL_ERROR
```

---

## 6. What happens after diagnosis

### Path A — NORMAL_ERROR

1. AGEM **tries the same step again**.
2. If it works → the workflow **continues**.
3. It tries **at most 3 times**.
4. If all 3 tries fail → the step becomes **FAILED**, and the error is shown on the dashboard.

Nothing new is built on this path.

### Path B — CAPABILITY_GAP

The step goes to the **Capability Engine** (`backend/capability_engine/`). It tries to get the missing tool in the cheapest and safest order:

#### Stage 0 — Check the registry
The **registry** is a list of tools AGEM has already built and checked.
- If the tool is already there → **reuse it** and jump straight to **Resume** (Stage 8).
- File: `registry.py`

#### Stage 1 — Web research
The LLM cannot search the web by itself, so AGEM asks its own **Web Research Agent** (a system agent in its own container, called over HTTP like any other agent). It searches the web and returns **research notes**:
- a **free or open-source tool**, if one already does the job,
- the **formula or definition** (for example, `A = P × (1 + r/n)^(n×t)`),
- **worked examples** with correct answers,
- the **sources** it used.

What happens next:
- A free tool is found → use it, **but it still goes through the safety checks** (Stages 3 to 6). A tool from the internet is not trusted just because it is free.
- No free tool → go to Build, and give the LLM the formula from the notes.
- The worked examples become **reference test cases** for Verify.
- **One call, 30 seconds.** If research fails or times out, AGEM simply builds without notes. Research can never block recovery.
- Text from the web is **data, never instructions**. The research agent never runs anything it finds.
- Files: `searcher.py` (asks the agent) and the `research_agent/` folder

#### Stage 2 — Build the tool
The LLM **writes a small Python function** for the missing tool (for example, a compound-interest calculator), using the formula from the research notes.
- A **stronger model** is used here, because the code must be correct.
- The code is **only written here, never run here**.
- File: `builder.py`

#### Stage 3 — Static safety check
Before running anything, AGEM **reads the code** and rejects it if it tries to use dangerous things:
- `os.system` (running system commands)
- `subprocess` (starting other programs)
- `socket` (network access)
- opening files outside a temporary scratch folder

This is the first safety wall.

#### Stage 4 — Run it in the Sandbox
The **Sandbox** is a separate, locked Docker container. It is the **only place** where new, untrusted code is ever run.

| Sandbox rule | Value |
|---|---|
| Internet | **None** (`--network none`) |
| Time limit | **10 seconds** |
| Memory | **256 MB** (`--memory=256m`) |
| CPU | **Half a CPU** (`--cpus=0.5`) |
| After the run | The container is **deleted** |

A fresh container is used every time, so nothing is left behind between runs. This is the second safety wall.

- Files: `sandbox.py` and the `sandbox_runner/` folder

#### Stage 5 — Test
Run the tool on **about 10 test cases**, each **3 times**, in one sandbox run:
- reference cases with known answers (from the research notes, or written by hand),
- edge cases (zero, negative, very large values, bad input that must give an error),
- property checks (right type, sensible range, no crash).
- File: `tester.py`

#### Stage 6 — Verify
A stricter check: is the answer **correct for the real task**? Three measurements:

| Check | Question | Pass rule |
|---|---|---|
| **Accuracy** | How many cases give the right answer? | at least **90%** |
| **Regression** | Did a repair break a case that passed before? | **0** broken |
| **Consistency** | Does each case give the same answer all 3 times? | all consistent |

- Accuracy is the **verification score**.
- File: `verifier.py`

**Test vs Verify:**
- *Test* = "Does it run?"
- *Verify* = "Is the answer right?"

#### If it fails — refine until verified
If Stage 3, 4, 5 or 6 fails, AGEM **rebuilds** the tool. The LLM is shown **exactly which cases failed**: the input, what the tool returned and what the right answer was ("fix these; do not change cases that already pass").
- **At most 3 rounds.**
- After 3 failed rounds → the step becomes **FAILED** and the message `CAPABILITY_BUILD_FAILED` is shown on the dashboard.

We do **not** fine-tune (retrain) the LLM. What is refined is the **tool the LLM writes**. More in `docs/final_flow.md` §5.3.

#### Stage 7 — Register
Save the working tool in the registry as **VERIFIED**, with its name, version and verification score.
- Next time any agent needs the same tool, it is **reused**, not rebuilt.
- File: `registry.py`

#### Stage 8 — Resume
1. AGEM runs the verified tool **in the sandbox** with the step's real input.
2. AGEM calls the agent again and sends the tool's **result** inside `context.tool_results`, for example:
   ```json
   { "tool_results": { "calculate_compound_interest": 1628.89 } }
   ```
3. The agent uses the result and **finishes its step**.
4. **Output drift check:** AGEM checks the agent's output has every field the next step needs (its `input_mapping`), with the right type. If not, the step becomes **FAILED** with `OUTPUT_DRIFT`, and the next agent **never receives the bad data**.
5. The workflow **continues** with the next steps.
6. An `AgentCapability` row records which agent was given which tool.

Only the paused step is run again. The whole workflow is **never** restarted.

**Why send the result and not the tool?** Agents live outside AGEM and are called over HTTP. AGEM cannot install code inside them, and must not change their code. So AGEM runs the tool safely in the sandbox and hands over just the answer.

**No resume button.** There is no public "resume" endpoint. Resume only happens inside the Orchestrator, after a successful diagnosis or a verified tool. Resuming a step that already succeeded does nothing, so work can never run twice.

### How the Orchestrator calls all of this

The Orchestrator stays small: it only calls the Master Agent and the Capability Engine, and never contains recovery logic itself.

```python
async def run_one(step):
    step_input = build_input_from_checkpoint(step)
    result = await step_executor.run_step(step.id)

    if result["status"] == "SUCCEEDED":
        save_success_and_checkpoint(step, result)       # one transaction
        return

    pause_step_and_checkpoint(step, result)             # one transaction
    diagnosis = master_agent.diagnose_failure(step.id, result)   # rules first, then LLM

    if diagnosis == "NORMAL_ERROR":
        await retry_step(step, max_tries=3)             # still failing -> step FAILED
        return

    capability_name = result.get("capability") or llm_capability_name(result)
    capability = await capability_engine.resolve_gap(capability_name, context)
    if capability:
        tool_result = run_tool_in_sandbox(capability, step_input)
        await resume_same_step(step, tool_results=tool_result)   # drift check before SUCCEEDED
    else:
        mark_step_failed(step, "CAPABILITY_BUILD_FAILED")
```

The full Orchestrator loop is in `docs/final_flow.md` Section 8.

---

## 7. The full picture

```
Step fails
   │
   ▼
Error put into one standard shape (step_executor.py)
   │
   ▼
Only this step PAUSED + checkpoint saved
   │
   ▼
MASTER AGENT — diagnose_failure()
   │
   ├─ 1. Simple rules (no LLM)
   └─ 2. LLM only if no rule matched
   │
   ├──────── NORMAL_ERROR ────────┐
   │                              ▼
   │                   Try the step again (max 3)
   │                   ├─ works → continue workflow
   │                   └─ still fails → step FAILED, shown on dashboard
   │
   └──────── CAPABILITY_GAP ──────┐
                                  ▼
                 0. Registry: already have it? → yes → go to 8
                                  │ no
                                  ▼
                 1. Web Research Agent: free tool? formula? examples?
                        free tool found → go to 3
                                  │ not found
                                  ▼
                 2. Build: LLM writes the code from the notes
                                  ▼
                 3. Static safety check
                                  ▼
                 4. Run in Sandbox (no internet, 10 s, 256 MB)
                                  ▼
                 5. Test (~10 cases, each 3 times)
                                  ▼
                 6. Verify (accuracy ≥ 90%, 0 regression, consistent?)
                     fail → feed failing cases back, rebuild (max 3 rounds, then FAILED)
                                  │ pass
                                  ▼
                 7. Register in the registry
                                  ▼
                 8. Resume ONLY the paused step
                                  ▼
                 9. Output drift check → fits next step? → continue workflow
                                           no → step FAILED (OUTPUT_DRIFT)
```

---

## 8. Example: the Tesla report demo

**Workflow:** Research Agent → Finance Agent → Fact Checker → Writer Agent

1. The Research Agent finishes. Its result is saved.
2. The Finance Agent fails and says:
   `{"error_type": "MISSING_CAPABILITY", "raw_error": "no tool for compound interest", "capability": "calculate_compound_interest"}`
3. Only the Finance step is **paused**. The Research result stays safe.
4. Master Agent, Stage 1 rule: `MISSING_CAPABILITY` → **CAPABILITY_GAP** (no LLM needed).
5. Registry check: no calculator yet.
6. Web research: no free tool, but the Web Research Agent brings back the compound-interest formula and worked examples (saved notes are used if the web is down).
7. Build: the LLM writes `calculate_compound_interest()` from the formula.
8. Static check: passes.
9. Sandbox + Test + Verify: refined until accuracy ≥ 90%, with no regression and consistent answers.
10. Register: the calculator is saved as VERIFIED, with its score.
11. Resume: the result is sent to the Finance Agent, and it finishes its step. Its output passes the drift check.
12. Fact Checker and Writer run. The final report is ready.

The Research step was **never run twice**, and the Finance Agent's code was **never changed**.

The script `scripts/trigger_capability_gap.py` makes this failure happen on purpose, so the demo works every time. Because the agent sends `MISSING_CAPABILITY` directly, diagnosis takes the **rules path**, so the demo never depends on the LLM guessing correctly. Running the demo a second time reuses the calculator from the registry, with no rebuild.

---

## 9. What the dashboard shows

### Step status (5 values)
```
PENDING → RUNNING → SUCCEEDED / FAILED / PAUSED
```
- PAUSED can go back to RUNNING (when resumed) or to FAILED (when out of tries).
- SUCCEEDED and FAILED are final.
- "Resumed" is not its own status. A resumed step simply becomes RUNNING again.

### Recovery stage (shown while a step is paused)
```
NONE → DIAGNOSING → SEARCHING → BUILDING → SANDBOXING → TESTING → VERIFYING → REGISTERED
```
`SEARCHING` is the web research stage. This lets a viewer **watch the fixing happen live** on the Executions page.

---

## 10. All the limits

Nothing in AGEM can loop forever. Every part has a limit.

| What | Limit |
|---|---|
| LLM diagnosis calls per failure | at most 1, and 0 when a rule matches (plus 2 retries if the reply format is wrong) |
| Time allowed for diagnosis | 20 seconds (then NORMAL_ERROR) |
| Retries for a normal error | 3 |
| Web research per missing tool | 1 call, 30 seconds (then build without notes) |
| Build/repair rounds per missing tool | 3 |
| Verification pass rule | accuracy ≥ 90%, 0 regressions, each case consistent over 3 runs |
| One sandbox run | 10 seconds, 256 MB memory, half a CPU, no internet |
| Whole build → sandbox → test → verify loop | under 90 seconds |

---

## 11. Logging (keeping a record)

AGEM writes down everything, so we can always explain later what happened:

- every diagnosis and its answer
- every LLM call (the question, the reply and the decision)
- every sandbox run and its result
- every web research call, its notes and its sources

Each log line includes `execution_id`, `step_id` and `agent_id`, so it is easy to follow one run.

Tools that **failed** verification are **kept** in the registry with status `FAILED`, not deleted. This way we can answer "why was this tool rejected?"

---

## 12. How we test it

| What we test | How |
|---|---|
| The simple rules | Unit tests check that each `error_type` gives the right answer, and that the LLM is **not called** when a rule matches |
| The LLM diagnosis | 20 hand-labelled errors (10 capability gaps, 10 normal errors) in `backend/tests/fixtures/`. It must get **at least 18 out of 20** right. Re-run every time the prompt changes. Use only errors the rules do **not** match, otherwise we would be testing the rules, not the LLM. |
| The Capability Engine | Unit tests: a tool already in the registry skips search and build; a free tool skips the build but is still checked; 3 failed rounds end in FAILED; a repair round is given the failing cases; a tool under 90%, with a regression or inconsistent results is not registered; a research timeout still reaches Build; a resumed output missing a field the next step needs ends in `OUTPUT_DRIFT`. Plus 5 prepared missing-tool cases (including the compound-interest demo), counting how many reach VERIFIED within 3 attempts. |
| The Sandbox | Tests check that network access fails, too-long code is stopped, too-much-memory code is stopped, and normal code works. |

In normal automatic tests (CI), LLM calls are **faked (mocked)**, so tests are free, fast and give the same result every time.

---

## 13. Files involved

| File | Job |
|---|---|
| `backend/orchestrator/step_executor.py` | Runs one step and puts errors into the standard shape with a fixed `error_type` code |
| `backend/orchestrator/checkpoint_manager.py` | Saves and loads checkpoints |
| `backend/orchestrator/master_agent.py` | Diagnosis: rules first, then LLM |
| `backend/orchestrator/orchestrator.py` | Decides what to do next and resumes the step |
| `backend/capability_engine/engine.py` | Runs the capability stages in order |
| `backend/capability_engine/searcher.py` | Asks the Web Research Agent for a free tool, formula and examples |
| `research_agent/` | The Web Research Agent: searches the web, returns research notes |
| `backend/capability_engine/builder.py` | LLM writes the tool code; rewrites it using the failing cases |
| `backend/capability_engine/sandbox.py` | Sends code to the sandbox |
| `backend/capability_engine/tester.py` | Runs about 10 cases, each 3 times |
| `backend/capability_engine/verifier.py` | Accuracy, regression and consistency; gives the score |
| `backend/capability_engine/registry.py` | Saves and reuses verified tools |
| `sandbox_runner/` | The locked Docker container that runs untrusted code |

---

## 14. Short answer for the viva

> "When a step fails, only that step pauses and a checkpoint is saved. The Master Agent, a function inside the Orchestrator, decides why it failed: obvious errors are handled by simple rules, and only unclear errors go to one LLM call that must reply in checked JSON. A normal error is retried up to 3 times. A capability gap goes to the Capability Engine: reuse the tool from the registry if we have it, otherwise a Web Research Agent searches the web for a free tool and the correct formula, otherwise the LLM builds one from those notes. Every new tool is safety-checked, run in a locked sandbox, tested and refined until it scores at least 90% with no regression and consistent answers, then saved in the registry. Finally, only the paused step resumes with the tool's result, and its output is checked so the next agent never gets bad data. Every loop has a limit, so nothing runs forever."
