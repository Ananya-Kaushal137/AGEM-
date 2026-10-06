# Prompt 6 — The Master Agent (explained)

## 1. The short version

Prompt 6 builds the part of AGEM that answers one question whenever a step fails:

> **"Was this a normal error, or is the agent missing a tool?"**

There are only two possible answers: `NORMAL_ERROR` (retry the step) or `CAPABILITY_GAP` (go and get or build the missing tool).

It answers in two stages:

1. **Simple rules, no AI.** If the error type already says what happened (`MISSING_CAPABILITY`, `TIMEOUT`, …), the answer is fixed.
2. **Ask the LLM, only if no rule matched.** This happens once, with strict checks, and if anything goes wrong the answer is `NORMAL_ERROR`.

Prompt 6 also builds the **single LLM wrapper**. Every LLM call in AGEM, now and later, must go through it.

**Hotel picture:** when a guest complains, the **duty manager** decides between two things. Either "the room is fine, something just went wrong, try again", or "this room is missing something, get one". Most complaints are on a checklist ("no hot water at 7 am" → known, retry). Only odd complaints go to the expert, and the expert has 20 seconds to answer on a printed form. If the form comes back filled in wrongly three times, the manager says "try again". A retry costs nothing; buying a new appliance by mistake does cost something.

---

## 2. How Prompt 6 connects to earlier prompts

| Earlier prompt | What it gave us | How Prompt 6 uses it |
|---|---|---|
| Prompt 3 — `app/core/config.py` | `LLM_PROVIDER` and `LLM_API_KEY`, read in one place, key hidden as `SecretStr` | The wrapper reads the key **only** from there. Two optional settings were added: `LLM_MODEL_CHEAP` and `LLM_MODEL_STRONG` |
| Prompt 4 — `rest_adapter.py` | Raises timeout / connection / HTTP / bad-JSON errors | Prompt 8 will turn those into `TIMEOUT`, `CONNECTION_ERROR`, `HTTP_5XX`, `INVALID_JSON`, which are exactly the strings the rules match |
| Prompt 4 — `MissingToolError` in the wrapper | The agent says `{"error": "MISSING_CAPABILITY", ...}` | The rule `MISSING_CAPABILITY` → `CAPABILITY_GAP`, **without the LLM** |
| Prompt 5 — the Finance demo agent | Fails with `MISSING_CAPABILITY` for compound interest | The key demo moment is decided by a rule, so it never depends on LLM luck |

```
step fails
   │
   ▼  (Prompt 8: step_executor.py normalises it)
{"status":"FAILED", "error_type":"...", "raw_error":"...", "capability"?}
   │
   ▼  diagnose_failure(step_id, error)          ← Prompt 6, master_agent.py
   ├─ MISSING_CAPABILITY ─────────────────────────────► CAPABILITY_GAP   (0 LLM calls)
   ├─ TIMEOUT / CONNECTION_ERROR / HTTP_5XX / INVALID_JSON ► NORMAL_ERROR (0 LLM calls)
   └─ anything else (AGENT_ERROR, …)
         │
         ▼  llm.call_json(...)                   ← Prompt 6, app/core/llm.py
         reply checked with Pydantic, up to 2 retries, 20 s limit
         ├─ valid reply ──────────────────────────► its diagnosis
         └─ invalid ×3 / too slow / provider down ─► NORMAL_ERROR  (safe default)
```

---

## 3. What the task asked for

From `prompts/CLAUDEBUILDPROMPTS`, Prompt 6:

- `backend/orchestrator/master_agent.py` with `diagnose_failure(step_id, error) -> Literal["NORMAL_ERROR", "CAPABILITY_GAP"]` and no third answer (FR-DIAG-003)
- Rules first, LLM only when no rule matches (FR-DIAG-014, ADR-008)
- One LLM wrapper for the whole codebase (FR-DIAG-009, P15): JSON checked against a Pydantic schema (FR-DIAG-005), 2 retries with the error appended (FR-DIAG-006), then a safe answer, never a hang (FR-DIAG-007). The **caller** passes the schema and the safe answer, so `searcher.py` and `builder.py` can reuse it unchanged
- 20-second limit (NFR-PERF-03) and the cheaper model for diagnosis (FR-DIAG-008)
- At most 1 diagnosis LLM call per failure, and 0 when a rule matches (FR-DIAG-004)
- **Do not** let any other module call the LLM API, and **do not** make this a separate service (ADR-001)

**Done when:** the function always returns one of the two answers, `MISSING_CAPABILITY` and `TIMEOUT` are decided without calling the LLM, and three schema failures in a row still return `NORMAL_ERROR` without raising an error.

---

## 4. Judgment calls — please check

The docs did not decide these, so I chose. Each one is easy to change.

| Question | What I chose | Why |
|---|---|---|
| Where does the wrapper live? | `backend/app/core/llm.py` | It is shared by `orchestrator/` and `capability_engine/`, like `config.py`. Added to Architecture §25's code map |
| Raw `httpx` or the official SDKs? | The official **`anthropic`** and **`openai`** SDKs, both in `requirements.txt` | The docs require both providers. The SDKs handle the request format for us. Each is imported in exactly one function |
| Which models? | Anthropic: **`claude-haiku-4-5`** (cheap, diagnosis) and **`claude-opus-5-5`** (strong, Prompt 13). OpenAI: `gpt-4o-mini` / `gpt-4o` | Haiku is Anthropic's cheap tier. **I'm less sure about the OpenAI names.** Either pair can be changed in `.env` with `LLM_MODEL_CHEAP` / `LLM_MODEL_STRONG`, without touching code |
| What counts as "1 LLM call"? | One `call_json` call, **including** its 2 validation retries, so up to 3 requests to the provider | FR-DIAG-006 says "retry the **same** call", so the retries are part of one call |
| What does the 20 s cover? | The **whole** call, retries included | FR-DIAG-007: never hang the workflow. 20 s per request could add up to 60 s |
| Network error / provider down / no API key? | Return the safe answer **straight away**, with no retry | The doc's retries are for **invalid replies** only. The SDK's own automatic retries are switched off (`max_retries=0`) so every request is counted |
| How is "JSON only" enforced? | The schema is put in the system prompt, and the reply is checked with Pydantic | It works the same with both providers and is exactly what the docs describe. A reply wrapped in ```json fences is accepted; anything looser is not |
| Where do the six error-type strings live? | A new `ErrorType` enum in `app/models/enums.py` | The rules need them now. Prompt 8 says "define them once", so **Prompt 8 should import this enum, not create a second one** |
| Function or method? | An `async` function in `master_agent.py` | FRS writes `Orchestrator.diagnose_failure()`. The Orchestrator class doesn't exist yet (Prompt 8), so it will get a one-line method that calls this function (ADR-001) |
| Extra fields in the LLM's reply? | The schema is `{diagnosis, capability, reason}`. Only `diagnosis` is returned; `capability` and `reason` are **logged** | This is the shape in `failure_diagnosis.md` §5. Showing `reason` in the UI is LYK extra L1; the field is already there at no extra cost |
| What goes in the logs? | Model, attempt, `VALID` / `INVALID` / `FALLBACK`, time taken and the validation error. The prompt and reply text are logged **only** if `LLM_LOG_PAYLOADS=true` | The docs disagree: Architecture §12 says "log prompt and response", but FR-AUTH-006 says logs carry metadata only, with payloads behind an explicit debug flag. The prompt contains the agent's error text, so FR-AUTH-006 wins by default and the debug flag covers §12. Prompt 21 checks this |
| The agent's error text | Cut to 4,000 characters and placed between `<error>` tags marked "data, not instructions" | An agent's error message is untrusted text and could try to steer the LLM |
| Refusal fallback for the strong model | Not added now | The Anthropic skill suggests a server-side fallback for Opus. Only `builder.py` (Prompt 13) uses the strong tier, so the decision belongs there. A refusal today simply gives the safe answer |

---

## 5. Everything that was built, part by part

### Part 1 — The LLM wrapper
**File:** `backend/app/core/llm.py`

```python
reply = await call_json(
    system="...",            # what the LLM is
    prompt="...",            # the question
    schema=DiagnosisReply,   # the Pydantic shape the reply MUST have
    fallback=SAFE_REPLY,     # what to return if we can't get a valid reply
    tier="cheap",            # cheap = diagnosis, strong = code generation
    timeout=20,              # for the whole call, retries included
    log_context={...},       # step_id etc., added to every log line
)
```

Inside:

```
no API key?                 → fallback (provider never called)
attempt 1: ask              → valid JSON for the schema? → return it
attempt 2: ask again + "your reply was rejected because: <pydantic error>"
attempt 3: same
still invalid               → fallback
over the time limit         → fallback
provider/network error      → fallback
```

It **never raises** and **never hangs**. Every attempt is logged with the model, `VALID` / `INVALID` / `FALLBACK`, the validation error and how long it took (Architecture §12). The prompt and the reply contain the agent's own error text, so they are logged only when the debug flag `LLM_LOG_PAYLOADS=true` is set (FR-AUTH-006).

`_complete()` is the **only** function in AGEM that talks to a provider. Tests replace just this function, so the real validation, retry and fallback code runs in the tests.

**Later callers, unchanged:** `searcher.py` (Prompt 10) passes `schema=ResearchNotes, fallback=ResearchNotes.empty(), timeout=30`; `builder.py` (Prompt 13) passes `tier="strong"`.

### Part 2 — The error-type list
**File:** `backend/app/models/enums.py`

`ErrorType` = `MISSING_CAPABILITY`, `TIMEOUT`, `CONNECTION_ERROR`, `HTTP_5XX`, `INVALID_JSON`, `AGENT_ERROR`, the fixed list from Architecture §23.1.

### Part 3 — The Master Agent
**File:** `backend/orchestrator/master_agent.py`

```python
RULE_GAP    = {"MISSING_CAPABILITY"}
RULE_NORMAL = {"TIMEOUT", "CONNECTION_ERROR", "HTTP_5XX", "INVALID_JSON"}

async def diagnose_failure(step_id, error):
    if error_type in RULE_GAP:    return "CAPABILITY_GAP"   # no LLM
    if error_type in RULE_NORMAL: return "NORMAL_ERROR"     # no LLM
    reply = await llm.call_json(..., fallback=NORMAL_ERROR, tier="cheap", timeout=20)
    return reply.diagnosis
```

The rules match the **exact** strings. `missing_capability` in lower case, a missing `error_type` or `None` is *not* a rule match, so it goes to the LLM. Whatever happens, the answer is still one of the two values.

### Part 4 — Settings
**Files:** `backend/app/core/config.py`, `.env.example`, `docker-compose.yml`

`LLM_MODEL_CHEAP` and `LLM_MODEL_STRONG` are optional. If they are empty, the defaults in `llm.py` are used. `LLM_LOG_PAYLOADS` (default `false`) is the debug flag for logging LLM prompts and replies.

---

## 6. A bug found and fixed (by the tests)

In Python 3.11 and newer, `str(ErrorType.TIMEOUT)` gives `"ErrorType.TIMEOUT"`, **not** `"TIMEOUT"`. The first version of `diagnose_failure` used `str(...)`, so if Prompt 8 passed the **enum** instead of a plain string, every rule would silently miss. Every failure would then cost an LLM call, and the demo's `MISSING_CAPABILITY` would depend on the LLM.

The test `test_rules_accept_the_enum_too` caught this. The code now uses `.value` for enums.

---

## 7. How it was checked

**Tests — 134 pass (26 new, all in `tests/test_orchestrator.py`):**
- Each of the 5 rules gives the right answer, and the mocked wrapper is **never called** (the done-when)
- `AGENT_ERROR` → exactly **one** wrapper call, on the cheap tier, with a 20 s limit, the `DiagnosisReply` schema and a `NORMAL_ERROR` fallback
- Empty, missing, lower-case or `None` error types → still one of the two answers
- **Three invalid replies in a row → `NORMAL_ERROR`, no exception, exactly 3 requests** (the done-when). Retries 2 and 3 carry the validation error
- A valid reply on the 2nd attempt is used
- The cheap model is used for diagnosis, and it differs from the strong one
- A slow provider → the safe answer in time; a provider error → the safe answer; no API key → the provider is never called
- The wrapper works with **another** schema and fallback (a `Notes` model standing in for `searcher.py`)
- By default the agent's error text **never appears in the logs**, while the VALID / INVALID trail does; with `LLM_LOG_PAYLOADS=true` it does appear (FR-AUTH-006)
- **No file except `app/core/llm.py` imports `anthropic` or `openai`** (FR-DIAG-009, checked by scanning the code)
- `master_agent.py` has no HTTP server or client (ADR-001)

Coverage: `master_agent.py` 98%, `llm.py` 80%. The uncovered lines are the two real provider calls, which are always mocked in tests (FR-DEP-006).

**Smoke test:** `_complete()` was called once against the real Anthropic and OpenAI APIs with a **dummy key**. Both answered `401 invalid key`, which shows the request is well-formed for both providers. No real key was used and nothing was spent.

**Not yet checked:**
- A real diagnosis with a real key. That is the 20-example accuracy test (FR-DIAG-011, ≥ 18/20), which is **Prompt 20**.
- Prompt 6 in Docker. Tests ran in a local venv on Python 3.13; the image uses 3.11. Nothing in this code needs anything newer than 3.11. (Ananya ran Prompts 1–5 on real Docker and PostgreSQL on 5 Oct; see `prompt5.md` §7.)

---

## 8. What Prompt 6 does NOT do (comes later)

- **Nobody calls `diagnose_failure` yet.** Connecting a failed step to it, then retry ×3 or the Capability Engine, is **Prompt 11**.
- Turning exceptions into the `{status, error_type, raw_error}` shape is `step_executor.py`, **Prompt 8**.
- `Orchestrator.diagnose_failure()` as a method: **Prompt 8** (one line calling this function).
- The 20 hand-labelled examples and the 18/20 bar: **Prompt 20**.
- Showing the diagnosis (and `reason`) in the step detail: **Prompts 17 and L1**.
- Logs are written with `logging`, but as plain lines. Turning them into JSON with `execution_id` / `agent_id` is **Prompt 22**.
- FR-DIAG-013 (checking the agent's declared tool list before asking the LLM) is an optional LYK item and was **not** built.

---

## 9. Files changed in Prompt 6

| File | New / changed |
|---|---|
| `backend/app/core/llm.py` | **new**: the single LLM wrapper |
| `backend/orchestrator/master_agent.py` | written: `diagnose_failure`, the rules, `DiagnosisReply` |
| `backend/app/models/enums.py` | changed: `ErrorType` |
| `backend/app/core/config.py` | changed: `llm_model_cheap`, `llm_model_strong`, `llm_log_payloads` |
| `backend/tests/test_orchestrator.py` | changed: 26 new tests |
| `backend/requirements.txt` | changed: `anthropic`, `openai` |
| `.env.example`, `docker-compose.yml` | changed: `LLM_MODEL_CHEAP`, `LLM_MODEL_STRONG`, `LLM_LOG_PAYLOADS` |
| `docs/Architecture.md` | changed: §25 code map lists `core/llm.py` |
| `prompts/CLAUDEBUILDPROMPTS`, `prompts/prompt5.md` | fixed prompt numbers: CI is Prompt 19 (not 18); agents are run in Prompt 8 (not 9) |

---

## 10. Where you are now

- [x] Prompt 1 — scaffold and Docker Compose
- [x] Prompt 2 — database schema
- [x] Prompt 3 — API skeleton, error envelope, auth
- [x] Prompt 4 — adapter contract, REST adapter, Python wrapper
- [x] Prompt 5 — agent registration and demo agents
- [x] **Prompt 6 — Master Agent and the LLM wrapper**
- [ ] Prompt 7 — workflow creation with DAG validation ← next
- [ ] Put a real `LLM_API_KEY` in `.env` before Prompt 11, when diagnosis actually runs

---

## 11. Check questions

1. Why does `MISSING_CAPABILITY` never reach the LLM, and why does that matter for the demo?
2. The LLM replies with invalid JSON three times. What does `diagnose_failure` return, and why that answer and not the other?
3. How many requests to the provider can one diagnosis make at most? Why is that still "one call"?
4. Why is `str(ErrorType.TIMEOUT)` a trap, and which test catches it?
5. `searcher.py` will use the same wrapper in Prompt 10. Which two arguments will it pass differently from `master_agent.py`?
6. How does a test prove that no other module talks to the LLM directly?
