# AGEM — The Final Flow

This is the **final, agreed flow** of the AGEM project, from registering an agent to getting the final result.
It is written in simple words. It combines every decision we made:

- how agents are brought into AGEM,
- how a workflow is built and run,
- what happens when a step fails,
- how a missing tool is found or built,
- how the workflow continues.

For more detail on failures, see `docs/failure_diagnosis.md`.

---

## 0. AGEM in one picture

```
 BRING  →  CONNECT  →  ORCHESTRATE  →  DIAGNOSE  →  EVOLVE  →  RESUME  →  COMPLETE
   │          │             │               │           │          │           │
 register   build the     run the steps   find out    get or     continue    final
 agents     workflow      in order        why a step  build the  the same    result
 by HTTP    (a DAG)                       failed      missing    step
                                                      tool
```

**The one-sentence version:** developers bring agents they already built, AGEM connects them into a workflow and runs it, and when a step fails AGEM finds out why, fixes it (retry, or get/build the missing tool safely), and continues from that same step — never restarting the whole workflow and never changing the agent's code.

---

## 1. BRING — registering agents

### 1.1 The rule: agents are called over HTTP, never imported

AGEM **never imports or runs an agent's code** inside its own server. Every agent stays outside AGEM and is called over the network (HTTP), through an **adapter**.

Why not import code?
- **Safety:** AGEM promises that untrusted code never runs on the main server. Importing an agent would break that promise.
- **No library clashes:** agent A may need one version of a library and agent B another. Separate agents never clash.
- **Less work:** running uploaded code safely would need a whole extra system. Not possible for 2 people in 12 weeks.

### 1.2 The agent contract (every agent must follow this)

Every agent must answer two HTTP calls:

```
GET  {agent_endpoint}/health     →  200 OK          (am I alive?)

POST {agent_endpoint}/execute                        (do the work)
  request:  { "task": "...", "input": {...}, "context": {...} }
  success:  { "status": "SUCCEEDED", "output": {...} }
  failure:  { "status": "FAILED", "error": "MISSING_CAPABILITY",
              "capability": "calculate_compound_interest" }
```

- `context` is where AGEM sends extra information, for example the result of a tool AGEM built (`context.tool_results`, see Section 6).
- When an agent is missing a tool, it should say so clearly with `MISSING_CAPABILITY` and the tool's name.

This contract is written into `docs/api-spec.md` and is **frozen**: nobody changes it without both team members agreeing.

### 1.3 How a developer registers an agent

On the Agents page (or by API):

```json
POST /api/agents
{
  "name": "Finance Agent",
  "framework": "rest",
  "endpoint": "http://localhost:9002",
  "credentials": "optional"
}
```

What AGEM does:
1. Checks `framework` is one of the allowed values (see 1.5). If not → clear error straight away.
2. Calls the agent's `/health`. If it does not answer → error ("invalid endpoint" toast). A typo never reaches the demo.
3. Encrypts any credentials (Fernet) and **never shows them again** in any response.
4. Saves the agent with status **ACTIVE**.

No source-code upload. Only the endpoint and connection details.

### 1.4 Agents that are only code: the wrapper templates

Some agents are just a Python script or a LangChain/CrewAI object, with no HTTP endpoint. For these, AGEM gives a **wrapper template** in the `agent_wrappers/` folder: a small FastAPI file (about 30 lines). The developer imports their agent into it and runs it. The agent's own code does **not** change.

```python
# agent_wrappers/python_wrapper.py — the developer runs this themselves
from fastapi import FastAPI
from my_agent import agent            # their existing code, unchanged

app = FastAPI()

@app.post("/execute")
def execute(req: dict):
    try:
        result = agent.run(req["input"])          # LangChain: .invoke()  CrewAI: .kickoff()
        return {"status": "SUCCEEDED", "output": result}
    except MissingToolError as e:
        return {"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": e.name}

@app.get("/health")
def health():
    return {"ok": True}
```

| Template | When |
|---|---|
| `python_wrapper.py` | Week 3 |
| `langchain_wrapper.py` **or** `crewai_wrapper.py` (one of the two) | Week 7 |

### 1.5 The ways an agent can connect

| # | Way | File on AGEM's side | Status |
|---|---|---|---|
| 1 | **REST/API** — any agent with an HTTP endpoint, including Python agents using the wrapper | `rest_adapter.py` | MUST, Week 3 |
| 2 | **LangChain or CrewAI** (one of the two) — the agent runs behind its wrapper; the adapter understands that framework's reply format | `langchain_adapter.py` **or** `crewai_adapter.py` | MUST, Week 7 |
| 3 | **MCP** (Model Context Protocol) — the agent is an MCP server, AGEM is the MCP client | `mcp_adapter.py` | Stretch, Week 8 only if on schedule |

So `framework` is one of: `rest`, `langchain` **or** `crewai` (whichever is built), and `mcp` only if built.
A plain Python agent registers as `rest` (it runs behind `python_wrapper.py`).

**MCP rule:** only the HTTP transport is allowed. The `stdio` transport is **never** allowed, because it would make AGEM start the agent's code on its own machine — that is code import again.

**MCP bonus (stretch, Weeks 8–10):** the Capability Engine's "search for a free tool" step can look up the missing tool on a small list of known MCP tool servers. This makes the "acquire" path real instead of a hardcoded check.

Other frameworks (JavaScript, OpenAI-based, LangGraph, AutoGen, n8n, custom) are supported **by exposing them as a REST endpoint**. They get no special adapter in the MVP.

---

## 2. CONNECT — building the workflow

### 2.1 The user builds the workflow first

There is **no smart planner** in the MVP. The user creates the workflow on the Workflows page, using a **simple form** (not drag-and-drop):
- pick an agent for each step,
- choose which earlier step(s) it depends on.

React Flow then **shows** the workflow as a picture (read-only), so the user can check it before saving.

### 2.2 Example workflow (first MVP)

```
Research Agent
      ↓
Finance Agent
      ↓
Writer Agent
```

The user asks: **"Create a company investment report."**
Research runs first → its output becomes Finance's input → Finance's output becomes Writer's input.

Each step stores what it depends on:

```
Research: depends_on = []
Finance:  depends_on = [Research]
Writer:   depends_on = [Finance]
```

Each step also stores an `input_mapping`: which output field of the earlier step feeds this step.

### 2.3 Checked once, at creation

When the workflow is saved, AGEM checks it **once**:
- every agent exists and is ACTIVE,
- there is **no loop** (A needs B and B needs A). A loop is rejected with `WORKFLOW_CYCLE_DETECTED`.

Then AGEM works out the running order **once** and saves it (`step_order`). The Orchestrator **never re-sorts** it later. A broken workflow can never reach the Orchestrator.

---

## 3. ORCHESTRATE — running the workflow

### 3.1 Four small files, one job each

```
backend/
└── orchestrator/
    ├── orchestrator.py         # decides what runs next
    ├── step_executor.py        # runs one step
    ├── checkpoint_manager.py   # saves / loads progress
    └── master_agent.py         # diagnoses a failed step
```

| File | Its one job |
|---|---|
| `orchestrator.py` | Loads the workflow, finds READY steps, starts them, handles success or failure, finishes the execution |
| `step_executor.py` | Takes **one** step, picks the correct adapter, calls the agent, returns a standard success or error result |
| `checkpoint_manager.py` | Saves finished outputs and progress to PostgreSQL; loads them when a step resumes |
| `master_agent.py` | Decides why a step failed: rules first, one LLM call only if the rules cannot decide |

The Orchestrator and the Master Agent are **two ideas** (the worker and the doctor) but live in **one service**.

### 3.2 The exact sequence

1. **The user starts the workflow.** React sends `POST /api/workflows/{workflow_id}/executions`.
2. **FastAPI creates an Execution row** with status `PENDING` and returns the `execution_id` **immediately**. The user never waits.
3. **A background task starts the Orchestrator**: `Orchestrator.run_execution(execution_id)` via FastAPI `BackgroundTasks`.
4. **The Orchestrator loads the workflow** — steps, saved order, dependencies — from PostgreSQL.
5. **It finds the READY steps.** A step is READY when it is `PENDING` **and** every step in its `depends_on` is `SUCCEEDED`.
6. **It prepares each step's input** from the earlier steps' outputs, read from the checkpoint.
7. **StepExecutor runs the step** through the correct adapter.
8. **On success** → save the output, mark `SUCCEEDED`, save a checkpoint, find the next READY step.
9. **On failure** → standard error shape, mark `PAUSED`, save a checkpoint, ask the Master Agent (Section 4).
10. **Recovery** → `NORMAL_ERROR`: retry (max 3). `CAPABILITY_GAP`: Capability Engine (Section 5). Then **resume the same step** (Section 6).
11. **Finish** → when every step is `SUCCEEDED`, mark the execution `SUCCEEDED` and save the final output (Section 7).

### 3.3 How one step calls an agent

The Orchestrator has **no framework-specific code**. All of that lives in adapters.

```
Orchestrator
    ↓
StepExecutor
    ↓
BaseAdapter.execute(input)
    ↓
REST Adapter / LangChain or CrewAI Adapter / (MCP Adapter, stretch)
    ↓
External agent, over HTTP
```

```python
class BaseAdapter:
    async def execute(self, input: dict) -> dict:
        ...
```

Adding a new framework = adding **one file** in `adapters/`. Nothing in `orchestrator/` changes.

**One common interface for every agent.** No matter which framework an agent comes from, it ends up behind the same interface. Everything after the adapter (Orchestrator, Master Agent, Capability Engine) sees only that one shape. There are two layers:

| Layer | Where | What it makes common |
|---|---|---|
| Agent contract + wrapper (1.2, 1.4) | Outside AGEM, on the agent's side | How every agent is **called**: `GET /health` and `POST /execute` |
| `BaseAdapter` | Inside AGEM | How the Orchestrator **sees** every agent: one `execute()` method, one standard success or error result |

The adapter is **not** used at registration. Registration only saves the `framework` field and checks `/health`. The adapter is used **at run time, on every step**: the StepExecutor reads the agent's `framework`, picks the matching adapter, and the adapter turns the agent's reply into the standard result.

### 3.4 The success path

The Research Agent replies:

```json
{ "status": "SUCCEEDED", "output": {"revenue": 96.7, "profit": 7.1} }
```

The Orchestrator:
1. saves the result in `ExecutionStep.output`,
2. marks the step `SUCCEEDED`,
3. saves the checkpoint,
4. finds the next READY step.

### 3.5 Checkpoints: the "save game"

A checkpoint is the saved progress of the workflow. It is why AGEM never restarts from zero.

```python
completed_steps = ["research"]
current_step    = "finance"
outputs = { "research": {"revenue": 96.7, "profit": 7.1} }
```

If Finance fails, Research does **not** run again. After recovery, the Orchestrator loads the checkpoint and runs **only** Finance again.

Rules:
- A checkpoint is saved **twice per step**: when it succeeds, and the moment it pauses.
- Step status, step output and checkpoint are saved in **one database transaction**. A step can never say `SUCCEEDED` without its output being saved.
- The database is the only source of truth. If the backend restarts mid-run, nothing is lost.

### 3.6 Parallel steps (after the simple version works)

```
       Research
        /      \
   Finance    Market
        \      /
         Writer
```

Finance and Market do not depend on each other, so they run **at the same time** with `asyncio.gather()`. Writer starts only when **both** have succeeded.

Build the simple one-after-another version first. Parallel steps come second, but they are still a required (MUST) feature.

---

## 4. DIAGNOSE — why did the step fail?

### 4.1 First, one standard error shape

`step_executor.py` turns **every** failure into the same shape:

```json
{
  "status": "FAILED",
  "error_type": "MISSING_CAPABILITY",
  "raw_error": "CAGR calculator not available",
  "capability": "calculate_cagr"
}
```

`error_type` is **always** one of these fixed codes (defined once, as constants):

| Code | Meaning |
|---|---|
| `MISSING_CAPABILITY` | The agent says it is missing a tool |
| `TIMEOUT` | The agent took too long |
| `CONNECTION_ERROR` | The agent could not be reached |
| `HTTP_5XX` | The agent's server crashed |
| `INVALID_JSON` | The agent sent back broken data |
| `AGENT_ERROR` | Anything else (the fallback) |

`capability` is only present when the agent names the missing tool.

Then: the step is marked **PAUSED** and the checkpoint is saved.

### 4.2 The Master Agent: rules first, LLM second

The Master Agent is **one function**, `diagnose_failure(step_id, error)`. It does not run the workflow. It only answers: **"Why did this step fail?"** — and it can only say `NORMAL_ERROR` or `CAPABILITY_GAP`.

**Stage 1 — simple rules (no LLM):**

| `error_type` | Answer |
|---|---|
| `MISSING_CAPABILITY` | `CAPABILITY_GAP` |
| `TIMEOUT`, `CONNECTION_ERROR`, `HTTP_5XX`, `INVALID_JSON` | `NORMAL_ERROR` |

**Stage 2 — one LLM call, only if no rule matched** (for example an `AGENT_ERROR` with messy text like `"ValueError: cannot compute IRR"`):
- The LLM must reply in JSON: `{"diagnosis": "...", "capability": "...", "reason": "..."}`, checked by Pydantic.
- Wrong format → ask again, up to 2 more times.
- Still wrong, or more than 20 seconds → `NORMAL_ERROR` (the safe choice).
- A cheaper model is used here.

Why rules first: faster, cheaper, and the demo never depends on the LLM guessing right.

---

## 5. EVOLVE — fixing the problem

### 5.1 NORMAL_ERROR → retry

- Retry the same step, **at most 3 times**.
- Works → the workflow continues.
- Still failing → the step becomes **FAILED** and the error is shown on the dashboard.

### 5.2 CAPABILITY_GAP → the Capability Engine

The recovery logic lives **outside** `orchestrator.py`. The Orchestrator only calls `CapabilityEngine.resolve_gap(capability_name, context)`.

```
0. REGISTRY   — already have a VERIFIED tool with this name?  yes → skip to RESUME
1. RESEARCH   — the Web Research Agent searches the web (see 5.4):
                a free / open-source tool exists?             yes → skip BUILD, still check it
                also brings back the formula / definition and worked examples with answers
2. BUILD      — the LLM (stronger model) writes a Python function, using the research notes. Never run here.
3. STATIC CHECK — reject code using os.system, subprocess, socket, or files outside a scratch folder
4. SANDBOX    — fresh Docker container: no internet, 10 s, 256 MB, half a CPU, deleted after
5. TEST       — run about 10 test cases, each 3 times ("does it run?")
6. VERIFY     — run the full test set: accuracy ≥ 90%, zero regression, consistent outputs
                ("is the answer right?") → verification score (see 5.3)
      fail → send the failing cases back to the LLM and rebuild, at most 3 rounds
             → then step FAILED (CAPABILITY_BUILD_FAILED)
7. REGISTER   — save as VERIFIED in the registry, so it is reused next time
```

After resume, one more check protects the next step: the **output drift check** (see 5.3).

Safety rules:
- **Every** new tool — built **or** found — goes through the static check, sandbox, test and verify. A free tool is not trusted just because it is free.
- The sandbox is the **only** place untrusted code ever runs.
- Tools that fail are **kept** in the registry as `FAILED` (not deleted), so we can explain why they were rejected.

No marketplace and no autonomous planner are needed.

### 5.3 Refine until verified: accuracy, regression, consistency, drift

**We do not retrain (fine-tune) the LLM.** Claude is used as it is, through the API. What gets "fine-tuned" is the **tool the LLM writes**: the Capability Engine tests it, sends the failures back to the LLM, and rebuilds it until it passes. Only then is it registered and used.

> In one line: the Capability Engine iteratively refines the generated tool — test it, feed the failures back to the LLM, rebuild — until it scores **at least 90% accuracy** with **zero regression**, **consistent outputs**, and **no output drift** at the next agent's input.

**The four checks**

| Check | Question it answers | How it is measured | Pass rule |
|---|---|---|---|
| **Accuracy** | Does the tool give the right answer? | `passed / total` over the test set (about 10 cases) | ≥ 90% |
| **Regression** | Did a repair break a case that passed before? | Re-run **every** saved case after each rebuild | 0 cases broken |
| **Behavioural consistency** | Same input → same output every time? | Run each case 3 times | All 3 results match |
| **Output drift** | After resume, does agent A's output still fit agent B's input? | Check A's output fields and types against B's `input_mapping` | All needed fields present, right types |

Accuracy, regression and consistency run inside VERIFY (stage 6), in **one** sandbox run. The drift check runs **after resume**, before the step is marked `SUCCEEDED`.

**Where the test cases come from**
- Normal inputs with **known correct answers** (for a formula tool, worked out independently of the LLM's code).
- Edge cases: zero, negative, very large values, bad input that must raise an error.
- Property checks: right type, sensible range, no crash.

For the demo, the cases are written by hand. For a tool nobody has seen before, the **worked examples found by the Web Research Agent** (5.4) become reference cases — answers that come from outside the LLM, not from the code being tested. If research finds none, the LLM may propose cases in a **separate** call, and the property checks still apply.

**The refinement loop**

```
round = 1
build tool
loop:
    run test set in sandbox (each case 3 times)
    accuracy ≥ 90%  AND  no regression  AND  consistent?  → REGISTER as VERIFIED, stop
    round == 3?                                            → CAPABILITY_BUILD_FAILED, stop
    feedback = the failing cases: input, what the tool returned, what was expected
    rebuild the tool with the feedback  ("fix these; do not change cases that already pass")
    round += 1
```

**Example (the demo tool)**

Round 1: the LLM forgets to divide the rate by the compounding periods `n`. Cases with `n = 1` still pass; the rest fail. Accuracy = 4/10 = 40% → rejected. The failing cases are sent back.
Round 2: the formula is fixed. Accuracy = 10/10, the 4 cases that passed before still pass, every case gives the same answer 3 times → **VERIFIED, score 100%**.
After resume, Finance returns `{"revenue": 96.7, "profit": 7.1, ...}`. The Fact Checker needs `revenue` and `profit` as numbers → both present → no drift → Finance `SUCCEEDED`.

**Why this protects the whole workflow**
- Steps **before** the failed one are checkpointed and never run again (Section 6), so they cannot change.
- The drift check guards the only output that *can* change — the resumed step's. If it fails, that step becomes `FAILED` (`OUTPUT_DRIFT`) and **the next agent never receives the bad data**.
- A tool that does not pass all checks within 3 rounds is **never used**. A half-working tool cannot enter the workflow.

**Honest limit (say this in the viva):** the score proves the tool is correct **on its test cases**, not on every possible input. Report it as "verified on N cases", never as "100% accurate".

### 5.4 The Web Research Agent

The LLM used by the Master Agent and the Capability Engine answers only from what it already knows — it does not search the web by itself. So AGEM adds one **Web Research Agent** whose only job is to look things up on the web when a capability gap happens.

**Where it sits:** it is called by the **Capability Engine** at stage 1 (RESEARCH), **after** the Master Agent has said `CAPABILITY_GAP` and the registry had nothing. The Master Agent itself is unchanged — it still only diagnoses.

```
Master Agent says CAPABILITY_GAP
      ↓
Capability Engine: registry?  → found → RESUME
      ↓ not found
Web Research Agent  (searches the web)
      ↓ research notes
BUILD (LLM uses the notes) → STATIC CHECK → SANDBOX → TEST → VERIFY → REGISTER
```

**How it is built:** it is a **system agent** that follows the same agent contract as every other agent (`/health` + `/execute`, Section 1.2), runs in its own container, and is called through the REST adapter. Inside, it uses a web search API to search and read pages, and an LLM to summarise what it found. AGEM ships it; developers do not register it.

**What it is asked:**

```json
POST /execute
{ "task": "research_capability",
  "input": { "capability": "calculate_compound_interest",
             "context": "Finance Agent, company investment report" } }
```

**What it returns (research notes):**

```json
{ "status": "SUCCEEDED",
  "output": {
    "free_tool":   null,                                   // or a package / function name, if one exists
    "definition":  "A = P × (1 + r/n)^(n×t)",
    "examples":    [ {"input": {"P": 1000, "r": 0.05, "t": 10, "n": 1}, "expected": 1628.89} ],
    "sources":     ["https://...", "https://..."]
  } }
```

**How AGEM uses the notes:**
| Part of the notes | Used by | For |
|---|---|---|
| `free_tool` | RESEARCH stage | If found, skip BUILD — the tool still goes through static check, sandbox, test and verify |
| `definition` | BUILD | Put in the LLM's prompt, so the function is written from the correct formula |
| `examples` | VERIFY (5.3) | Reference test cases with answers from outside the LLM |
| `sources` | Capabilities page | Shown next to the tool, so a person can see where the knowledge came from |

**Rules:**
- **One research call per gap**, **30-second** limit. No answer, an error, or empty notes → the Capability Engine simply continues to BUILD without notes. Research can help, but it can **never block** recovery.
- **Web content is data, not instructions.** Text from a web page is never followed as a command, and code found on the web is **never run** outside the sandbox — it goes through the same static check, sandbox, test and verify as built code.
- The Web Research Agent has internet; the **sandbox still has none**. The agent only reads and summarises; it never installs or runs anything.
- Research notes are **saved with the tool** in the registry, so the same gap is never researched twice.
- **Demo safety:** for the live demo, the research result for `calculate_compound_interest` is also saved in advance. If the web is slow or down during the demo, AGEM uses the saved notes, so the demo never depends on the internet.

---

## 6. RESUME — continue from the same step

Agents live outside AGEM, so AGEM cannot install a tool inside them — and must not change their code. So AGEM **hands over the answer instead of the tool**:

1. AGEM runs the verified tool **in the sandbox** with the paused step's input.
2. AGEM loads the checkpoint.
3. AGEM calls the **same** agent's `/execute` again, with the tool's result in `context`:
   ```json
   { "context": { "tool_results": { "calculate_compound_interest": 1628.89 } } }
   ```
4. The agent uses the result and finishes its step. AGEM runs the **output drift check** (5.3) on the agent's output. Passes → the step goes `PAUSED → RUNNING → SUCCEEDED`. Fails → the step becomes `FAILED` (`OUTPUT_DRIFT`) and the next step never runs.
5. An `AgentCapability` row records which agent was given which tool.
6. The workflow continues with the next READY steps.

Rules:
- Only the paused step runs again. Earlier steps are **never** run again.
- There is **no public "resume" button or endpoint**. Resume only happens inside the Orchestrator.
- Resuming a step that is already `SUCCEEDED` does nothing, so a double-click can never run work twice.

---

## 7. COMPLETE — the final result

- When every step is `SUCCEEDED`, the execution becomes **SUCCEEDED** and the final output is saved.
- If any step ends `FAILED` (out of retries or out of build attempts), the execution becomes **FAILED** and the reason is shown.
- The **Executions page** asks `GET /api/executions/{id}` every **3 seconds** and shows, live:
  - each step's status: `PENDING → RUNNING → SUCCEEDED / FAILED / PAUSED`
  - while paused, the recovery stage: `DIAGNOSING → SEARCHING (web research) → BUILDING → SANDBOXING → TESTING → VERIFYING → REGISTERED`
  - the diagnosis result, errors, and the final output.
- The **Capabilities page** shows every tool built or found, with its verification score.

---

## 8. The whole thing in pseudocode

```python
MAX_RETRIES = 3

async def run_execution(execution_id):
    execution = load_execution(execution_id)
    workflow  = load_workflow(execution.workflow_id)        # step_order already saved
    mark_execution_running(execution)

    while not workflow_finished(execution):
        ready_steps = find_ready_steps(execution)           # PENDING + all depends_on SUCCEEDED
        await asyncio.gather(*(run_one(step) for step in ready_steps))   # siblings together

        if any_step_failed(execution):
            mark_execution_failed(execution)
            return

    mark_execution_succeeded(execution)                     # saves the final output


async def run_one(step):
    step_input = build_input_from_checkpoint(step)
    result = await step_executor.run_step(step.id)          # adapter call + error normalisation

    if result["status"] == "SUCCEEDED":
        save_success_and_checkpoint(step, result)           # one transaction
        return

    pause_step_and_checkpoint(step, result)                 # one transaction
    diagnosis = master_agent.diagnose_failure(step.id, result)   # rules first, then LLM

    if diagnosis == "NORMAL_ERROR":
        await retry_step(step, max_tries=MAX_RETRIES)       # still failing -> step FAILED
        return

    capability_name = result.get("capability") or llm_capability_name(result)
    capability = await capability_engine.resolve_gap(capability_name, context)   # refine loop: ≥90%, no regression, consistent
    if capability:
        tool_result = run_tool_in_sandbox(capability, step_input)
        await resume_same_step(step, tool_results=tool_result)   # drift check before SUCCEEDED
    else:
        mark_step_failed(step, "CAPABILITY_BUILD_FAILED")
```

---

## 9. The demo, start to finish

**Demo agents:** Research, Finance, Fact Checker and Writer — each wrapped with the template, each its own container in `docker-compose.yml` (ports 9001–9004). `scripts/seed_agents.py` registers all four through `POST /api/agents`. The **Web Research Agent** is AGEM's own system agent (port 9005), started by `docker-compose.yml` with the rest.

**Workflow:** Research → Finance → Fact Checker → Writer. Task: "Create a company investment report."

1. Research succeeds. Its output is saved in the checkpoint.
2. Finance fails on purpose (`scripts/trigger_capability_gap.py`):
   `{"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest"}`
3. Only Finance pauses. Research is safe.
4. Master Agent rule: `MISSING_CAPABILITY` → `CAPABILITY_GAP`. **No LLM needed**, so the demo is reliable.
5. Registry: nothing yet. The Web Research Agent searches the web: no free tool, but it brings back the compound-interest formula and worked examples (saved notes are used if the web is down).
6. Build: the LLM writes `calculate_compound_interest()` from the formula in the notes.
7. Static check → sandbox → test → verify (accuracy, regression, consistency) — refined until it passes. Registered as VERIFIED with its score.
8. AGEM runs the tool in the sandbox and resumes Finance with the result in `context.tool_results`.
9. Finance's output passes the drift check. Finance succeeds. Fact Checker and Writer run. The final report appears.
10. Run it again: the calculator is **reused from the registry**, with no rebuild.

The audience watches every stage live on the Executions page. Research never ran twice, and no agent's code was changed.

---

## 10. All the limits

| What | Limit |
|---|---|
| Agents per workflow | 1–5 |
| Workflows running at once | 1–5 |
| LLM diagnosis calls per failure | at most 1 (0 when a rule matches), plus 2 format retries |
| Diagnosis time | 20 seconds, then `NORMAL_ERROR` |
| Retries for a normal error | 3 |
| Web research per missing tool | 1 call, 30 seconds (then continue without notes) |
| Build/repair rounds per missing tool | 3 |
| Verification pass rule | accuracy ≥ 90%, 0 regressions, each case consistent over 3 runs |
| Test cases per tool | about 10 |
| One sandbox run | 10 s, 256 MB, half a CPU, no internet |
| Whole build → verify loop | under 90 seconds |
| Orchestrator overhead per step | under 300 ms |
| Dashboard refresh | every 3 seconds |

Nothing in AGEM can loop forever.

---

## 11. Who builds what

| Area | Owner |
|---|---|
| `orchestrator.py`, `step_executor.py`, `checkpoint_manager.py` | Person 1 — Ananya |
| Adapters (`adapters/`) and wrapper templates (`agent_wrappers/`) | Person 1 — Ananya |
| API (`backend/app/api/`), DB models, frontend | Person 1 — Ananya |
| `master_agent.py` (rules + LLM) | Person 2 — Guthal |
| Capability Engine (`capability_engine/`) and `sandbox_runner/` | Person 2 — Guthal |
| Web Research Agent (system agent) | Person 2 — Guthal |
| Most tests, LLM prompts | Person 2 — Guthal |
| Agent contract, DB schema, demo agents, demo script | Both |

---

## 12. One line per part

| Part | Its job |
|---|---|
| **React** | Shows agents, workflows and live execution status |
| **FastAPI** | Accepts requests and starts executions (never waits for them) |
| **Adapter** | Translates AGEM's call into the agent's own interface |
| **Wrapper template** | Turns a code-only agent into an HTTP agent, without changing its code |
| **Orchestrator** | Decides what runs next |
| **StepExecutor** | Runs one step and gives back a standard result |
| **CheckpointManager** | Saves and restores progress |
| **Master Agent** | Diagnoses why a step failed |
| **Capability Engine** | Finds or builds a missing tool, safely |
| **Web Research Agent** | Searches the web for a free tool, the formula and worked examples when a tool is missing |
| **Docker Sandbox** | The only place untrusted code runs |
| **Registry** | Keeps verified tools so they are reused, not rebuilt |
| **PostgreSQL** | The single source of truth |

---

## 13. Decisions that differ from the original project PDF

These are the final decisions. The team should agree on them, and the FRS, Architecture and build prompts must say the same thing.

| # | Decision | Original PDF said |
|---|---|---|
| 1 | Agents are **registered by HTTP endpoint**; code-only agents use a wrapper template | "register/import existing agents" |
| 2 | Agent contract: `/health` + `/execute`, frozen in `docs/api-spec.md` | No contract written down |
| 3 | `framework` = `rest`, `langchain` or `crewai` (one of the two), `mcp` only if built | `python`, `rest`, `langchain`, `crewai` (no Python adapter existed) |
| 4 | `/health` is checked at registration (required) | Optional extra |
| 5 | MCP adapter as a Week 8 stretch, **HTTP transport only** | "later" |
| 6 | Diagnosis: **rules first**, LLM only for unclear errors | One LLM call for every failure |
| 7 | Fixed `error_type` codes, plus a `capability` field | Codes not listed |
| 8 | Normal errors: **max 3 retries**, then FAILED | "retry or stop" |
| 9 | Capability Engine checks the **registry first**; found tools are also verified | Search first; found tool "plugged in" |
| 10 | Tool **result** sent to the agent in `context.tool_results` | Tool "given to the agent" |
| 11 | Parallel steps built **after** sequential works (still MUST) | Parallel from the start |
| 12 | Built tools are **refined until verified** (≥ 90% accuracy, 0 regression, consistent, no output drift); the LLM itself is **not** fine-tuned | "test and verify" |
| 13 | A **Web Research Agent** does the web search for the Capability Engine (the LLM cannot search by itself); its notes feed BUILD and VERIFY | "search for a free tool" |

---

## 14. Short answer for the viva

> "Developers register agents they already built by giving AGEM an HTTP endpoint; code-only agents use a small wrapper, so no agent code is ever imported or changed. The user builds a workflow as a DAG, which is checked for loops once, when it is saved. The Orchestrator runs READY steps in order, calling each agent through an adapter, and saves a checkpoint after every step. When a step fails, only that step pauses. The Master Agent classifies the failure — obvious errors by rules, unclear ones by one validated LLM call. A normal error is retried up to 3 times. A capability gap goes to the Capability Engine: reuse from the registry, else a Web Research Agent searches the web for a free tool and the correct formula, else the LLM builds one from those notes — and every new tool is checked, run in a locked sandbox, tested and refined until it scores at least 90% accuracy with zero regression and consistent outputs, before it is registered. AGEM then runs the tool, sends its result to the same agent, resumes only that step, and checks its output has not drifted from what the next agent expects. Everything has a limit, and the whole run is visible live on the dashboard."
