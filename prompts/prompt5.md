# Prompt 5 — Agent Registration (explained)

## 1. The short version

Prompt 5 lets a developer **register** an agent they already built. AGEM:

1. **rings** the agent to check it is really running (`GET /health`),
2. **saves** it as `ACTIVE` in the database (with any password encrypted),
3. can **list** it, **show** it and **delete** it,
4. **refuses to delete** an agent that a workflow still uses.

It also builds the **four demo agents** (Research, Finance, Fact Checker, Writer) and a **script** that registers all four.

**Hotel picture:** Prompt 4 installed the phone. Prompt 5 is the **guest register** at reception. A guest is only written in the register after we ring their room and someone picks up.

---

## 2. How Prompt 5 connects to Prompt 4

Prompt 4 built the "telephone": the two sides of every call between AGEM and an agent.

| Prompt 4 built | What it does | How Prompt 5 uses it |
|---|---|---|
| `adapters/rest_adapter.py` → `health()` | Calls `GET {endpoint}/health`, returns True/False, never crashes | **Registration calls it before saving.** No 200 → the agent is rejected (FR-AGT-011). This is the first real user of `health()`, which is exactly what `prompt4.md` said ("Use `RestAdapter(endpoint).health()` for FR-AGT-011") |
| `adapters/rest_adapter.py` → `execute()` | Calls `POST {endpoint}/execute` | Not used by Prompt 5 itself. The orchestrator uses it later (Prompt 9). The demo agents were tested through it |
| `agent_wrappers/python_wrapper.py` | Turns a plain Python function into an agent with `/health` + `/execute` | **All four demo agents run behind it**, unchanged. That's the proof that an agent's own code never changes |
| `MissingToolError` in the wrapper | Turns "I lack a tool" into `{"status":"FAILED","error":"MISSING_CAPABILITY",...}` | **The Finance demo agent raises it** when `calculate_compound_interest` is missing, which starts the whole capability-gap story later |
| Open question #3 in `prompt4.md`: "Agent credentials are not sent yet… needs a decision before or during Prompt 5" | — | **Answered in Prompt 5:** credentials are sent as `Authorization: Bearer <credentials>`, and `RestAdapter` now does it |

What changed inside Prompt 4's code: **only one thing**. `RestAdapter` got an optional `credentials` argument. Without it, it behaves exactly as before, so all of Prompt 4's tests still pass.

```python
RestAdapter("http://finance-agent:9002")                      # same as Prompt 4
RestAdapter("http://finance-agent:9002", credentials="abc")   # adds: Authorization: Bearer abc
```

**The full picture after Prompt 5:**

```
Developer
   │  POST /api/agents {name, framework, endpoint, credentials?}
   ▼
agents.py (Prompt 5)
   │  1. RestAdapter(endpoint, credentials).health()     ← Prompt 4's phone
   │        │  GET /health  ─────────────►  python_wrapper.py (Prompt 4)  ─►  demo agent (Prompt 5)
   │        │  ◄──────────── 200 {"ok": true}
   │  2. encrypt credentials (crypto.py)
   │  3. save Agent row, status ACTIVE   ← tables from Prompt 2
   ▼
201 {agent_id, name, framework, endpoint, status: "ACTIVE", has_credentials, ...}
```

It also connects backward to **Prompt 2** (the `agents` and `workflow_agents` tables, and the `ON DELETE RESTRICT` rule that gives the 409) and **Prompt 3** (the `X-API-Key` check, the error envelope and `StrictModel`, which all apply to these new endpoints automatically).

---

## 3. What the task asked for

From `prompts/CLAUDEBUILDPROMPTS`, Prompt 5:

- `POST /api/agents`, `GET /api/agents`, `GET /api/agents/{id}`, `DELETE /api/agents/{id}` (FR-AGT-001, 004, 005, 006)
- **First** fix the Prompt 2 leftover: remove `python` from the framework list with a **new** migration
- `framework` checked at registration (FR-AGT-002); status rules (FR-AGT-003); credentials Fernet-encrypted and never shown (FR-AGT-007)
- Call the agent's `GET /health` before saving (FR-AGT-011)
- Four demo agents in `demo_agents/`, each its own container on ports 9001–9004, and `scripts/seed_agents.py` (FR-AGT-010)
- **Do not** accept agent source code (FR-AGT-008)

**Done when:** an agent registers `ACTIVE` without changing its code, a mistyped endpoint is rejected, and deleting an in-use agent returns 409.

---

## 4. Decisions we made (the docs did not say)

| Question | Decision | Why |
|---|---|---|
| Which frameworks? | Only `rest` and `langchain` | `python` agents now run behind `python_wrapper.py` as `rest`; we chose LangChain over CrewAI |
| Error code when the agent is in use? | `AGENT_IN_USE` → 409 | A clear name for the viva |
| Error code when `/health` doesn't answer? | `AGENT_UNREACHABLE` → 400 | The request was well-formed, but the endpoint is wrong or down |
| How are credentials sent to the agent? | Header `Authorization: Bearer <credentials>` | The standard way; only the adapter needs to know |

All four are now written in `docs/Architecture.md` §10.2 and `docs/api-spec.md`.

---

## 5. Everything that was built, part by part

### Part 1 — The framework fix
**Files:** `backend/app/models/enums.py`, `backend/alembic/versions/5d1e2f3a4b6c_agent_framework_rest_langchain.py`

PostgreSQL keeps the allowed framework values as its own type, `agent_framework`. The database itself refuses any other value, even if our Python has a bug.

PostgreSQL can **add** a value to such a type but **cannot remove** one. So the migration rebuilds the type:

```
1. UPDATE agents SET framework='rest' WHERE framework='python'   ← no data lost
2. CREATE TYPE agent_framework_new AS ENUM ('rest', 'langchain')
3. ALTER the column to use the new type
4. DROP the old type, RENAME the new one to agent_framework
```

**Why a new migration and not an edit:** the first migration is already pushed and already run. Alembic remembers which migrations a database has run, so an edited old file would never run again on Guthal's database. Every change gets a new file that runs *after* the old ones.

### Part 2 — Encryption
**File:** `backend/app/core/crypto.py`

**Fernet** is "lock and unlock with one secret key" (symmetric encryption). The key is `FERNET_KEY` in `.env`.

```
encrypt("my-agent-password") → "gAAAAB...long random text..."   ← only this is stored in the database
decrypt("gAAAAB...")         → "my-agent-password"              ← only in memory, just before calling the agent
```

If `FERNET_KEY` is missing, AGEM **refuses** to store credentials instead of saving them unencrypted. This is called "fail closed".

### Part 3 — Request and response shapes
**File:** `backend/app/schemas/agent.py`

| `AgentCreate` (what you send) | `AgentRead` (what you get back) |
|---|---|
| `name` (1–255 characters) | `agent_id`, `name`, `framework`, `endpoint`, `description` |
| `framework` (`rest` or `langchain`) | `status` (`ACTIVE`) |
| `endpoint` (must be a real http/https URL) | `has_credentials` (true/false — never the credentials themselves) |
| `description` (optional) | `created_at`, `updated_at` |
| `credentials` (optional) | |

- An unknown framework (`python`, `crewai`, `REST`) → **422 straight away**. That's what "rejected at registration, not at execution" means.
- There is **no field for source code**, and any extra field is a 422 (Prompt 3's `StrictModel`). That's FR-AGT-008.
- `credentials` is a `SecretStr`, so it shows as `**********` if it's ever printed or logged.

### Part 4 — The endpoints
**File:** `backend/app/api/agents.py`

```
POST   /api/agents        1. ring the agent: RestAdapter(endpoint, credentials).health()
                          2. no 200  → 400 AGENT_UNREACHABLE, nothing saved
                          3. 200     → encrypt credentials, save as ACTIVE, owned by the seeded owner → 201
GET    /api/agents        → all agents, oldest first
GET    /api/agents/{id}   → one agent, or 404 AGENT_NOT_FOUND
DELETE /api/agents/{id}   → 204 deleted / 409 AGENT_IN_USE / 404 AGENT_NOT_FOUND
```

Every one of these also needs the `X-API-Key` header and returns errors in the standard envelope, because Prompt 3 set that up for all routes.

**Why the 409 comes from the database, not from our code:**
`workflow_agents.agent_id` has `ON DELETE RESTRICT` (Prompt 2). PostgreSQL itself refuses the delete; our code only turns that refusal into a 409. If someone forgets a check in Python, the database still protects the data.

One detail: by default SQLAlchemy tries to "tidy up" child rows (the workflow steps) before deleting a parent. We set `passive_deletes="all"` in `backend/app/models/agent.py` so SQLAlchemy leaves them alone and the **database rule** decides.

### Part 5 — The adapter now sends credentials
**File:** `backend/adapters/rest_adapter.py` (from Prompt 4)

`RestAdapter(endpoint, credentials="abc")` adds `Authorization: Bearer abc` to **both** `/health` and `/execute`. Without credentials, nothing changes.

### Part 6 — The demo agents
**Files:** `demo_agents/research.py`, `finance.py`, `fact_checker.py`, `writer.py`, `demo_agents/Dockerfile`, `docker-compose.yml`

These stand in for a developer's existing agents. Each is just a plain function `run(task, input, context)`, served by Prompt 4's `python_wrapper.py`.

| Agent | Gets | Returns |
|---|---|---|
| Research (9001) | `{company}` | `{company, summary, revenue, profit}` |
| Finance (9002) | `{company, revenue, profit}` | **first:** `FAILED MISSING_CAPABILITY` (no `calculate_compound_interest`) |
|  | …resumed with `context.tool_results` | `{company, revenue, profit, projected_value}` |
| Fact Checker (9003) | `{company, revenue, profit, projected_value}` | the same + `{verified, checks}` |
| Writer (9004) | the above | `{report}` |

- Each agent's output contains **exactly the fields the next agent needs**, as numbers where numbers are expected. That's so the output drift check in Prompt 14 passes.
- The Research agent returns **fixed** figures instead of searching the web, so the demo gives the same result every time.
- **One Dockerfile** builds all four, and `docker-compose.yml` starts them as `research-agent`, `finance-agent`, `fact-checker-agent` and `writer-agent`.

### Part 7 — The seed script
**File:** `scripts/seed_agents.py`

- Registers the four agents **through the API** (`POST /api/agents`), not by writing to the database directly, so it goes through the same health check as a real developer.
- Uses endpoints like `http://finance-agent:9002`, not `localhost`, because it's the **backend container** that calls the agents, and inside Docker containers find each other by service name.
- **Safe to run twice:** names already registered are skipped.
- Uses only Python's standard library, so it needs no install. It reads `API_KEY` from the environment or from `.env`.

---

## 6. A bug found and fixed (not part of the task)

**SQLAlchemy 2.1** came out and changed its default PostgreSQL driver to `psycopg` (version 3), but we install `psycopg2`. Our `requirements.txt` didn't pin a version, so a fresh Docker build would have installed 2.1 and **crashed on start**.

**Fix:** `backend/requirements.txt` now says `sqlalchemy>=2.0,<2.1`.

---

## 7. How it was checked

**Tests — 108 pass (16 new for agents):**
- A running agent registers as `ACTIVE` and shows up in the list and the detail view
- An unreachable endpoint → 400 `AGENT_UNREACHABLE`, and **nothing is saved**
- `python`, `crewai`, `autogen`, `REST` → 422; `langchain` is accepted
- A bad URL, an empty name, or a `source_code` field → 422
- Credentials: an agent that needs a password **fails** without it and **registers** with it (proving the Bearer header is sent). The database holds only encrypted text, and the password never appears in any response
- An unknown id → 404 for both get and delete
- Deleting an unused agent → 204; deleting an agent a workflow uses → 409, and the agent is still there

The tests use **SQLite** because Docker wasn't running, with foreign keys switched on so the `RESTRICT` rule is really enforced.

**Live run:** the four demo agents were started for real and called through `RestAdapter`. All are healthy; Finance fails with `MISSING_CAPABILITY`, then succeeds when given the tool's result; the Writer produces the report.

**Migration:** its SQL was generated with Alembic's offline mode and checked by eye.

**Not yet checked on real PostgreSQL.** When Docker Desktop is running:

```
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.db.seed
python scripts/seed_agents.py            → four lines ending in ACTIVE
```

---

## 8. What Prompt 5 does NOT do (comes later)

- "An `INACTIVE` agent can't be added to a new workflow" is checked when workflows are created (**Prompt 7**).
- Actually **running** agents in a workflow with `execute()` is **Prompt 9**.
- The LangChain adapter is **Prompt 15**. `langchain` is already an accepted framework name.

---

## 9. Files changed in Prompt 5

| File | New / changed |
|---|---|
| `backend/app/api/agents.py` | changed: the 4 endpoints |
| `backend/app/schemas/agent.py` | new |
| `backend/app/core/crypto.py` | new |
| `backend/app/core/errors.py` | changed: 2 new codes |
| `backend/app/models/enums.py` | changed: `rest`, `langchain` only |
| `backend/app/models/agent.py` | changed: `passive_deletes="all"` |
| `backend/alembic/versions/5d1e2f3a4b6c_…py` | new migration |
| `backend/adapters/rest_adapter.py` | changed: optional credentials |
| `backend/tests/test_api.py` | changed: 16 new tests |
| `backend/requirements.txt` | changed: SQLAlchemy pinned |
| `demo_agents/` (5 files) | new |
| `docker-compose.yml` | changed: 4 demo agent services |
| `scripts/seed_agents.py` | written |
| `docs/Architecture.md`, `docs/api-spec.md` | changed: new codes, credentials header |

---

## 10. Check questions

1. Why does registration call `/health` **before** saving, instead of saving first and checking later?
2. Where does the 409 really come from: our Python code or PostgreSQL? Why does that matter?
3. If someone reads the `agents` table directly, can they see an agent's password? Why not?
4. Why does `seed_agents.py` use `http://finance-agent:9002` and not `http://localhost:9002`?
5. Which single line of Prompt 4's code changed in Prompt 5, and why didn't any Prompt 4 test break?
