The short version
Prompt 1 built the empty house. Prompt 2 built the filing cabinet. Prompt 3 built the front door, the lock on it, and the one standard way of saying "no".

The backend is now a real web server. It starts, it answers, and it refuses anyone who doesn't show the password (the X-API-Key header). When something goes wrong, every error comes back in the same shape, so the frontend only ever has to understand one kind of error.

Still no features. There are no agent, workflow or execution endpoints yet. The four doors (routers) exist and are locked, but the rooms behind them are empty. Prompt 5 opens the first one (agents).

Think of the hotel again: Prompt 1 built the rooms, Prompt 2 built the register. Prompt 3 put a receptionist at the front door who checks everyone's key card and, when she has to turn someone away, always uses the same polite, printed form.

What the task actually asked for
Prompt 3 in prompts/CLAUDEBUILDPROMPTS said:

Build the FastAPI app in backend/main.py and the four empty routers in backend/app/api/ (agents, workflows, executions, capabilities)
- FR-API-003 / FR-API-004 — one error envelope for every error, plus the full error code list from Architecture §10.2
- FR-AUTH-001 — one get_api_key check on the X-API-Key header, applied to every route
- FR-AUTH-004 — backend/app/core/config.py as the single place secrets are read
- FR-AUTH-002 — a seed function that creates the one User row that owns everything
- FR-API-002 — Pydantic schemas in backend/app/schemas/ with extra = "forbid"

"Done when": every route returns 401 in the standard envelope when X-API-Key is missing, and a bad request body returns 422 in the same envelope.

What it said NOT to do: no OAuth, no JWT, no login, no roles (FR-AUTH-003 defers all of these).

Docs read first: Architecture §3, §10, §16, §18.4, §24, §25 · FRS §3.10, §3.11 · api-spec.md.

Part 1 — What is FastAPI, and what is a "router"?
FastAPI is the Python library that turns functions into web addresses. You write a function, tell FastAPI "this answers GET /health", and FastAPI handles the rest: reading the request, checking it, and sending back JSON.

A router is a group of related addresses. AGEM has four, one per topic:

File                        Address prefix        Filled by
app/api/agents.py           /api/agents           Prompt 5
app/api/workflows.py        /api/workflows        Prompt 7
app/api/executions.py       /api/executions       a later prompt
app/api/capabilities.py     /api/capabilities     a later prompt

Right now each file is just the empty router — five lines. main.py plugs all four into the app.

Part 2 — main.py: the app itself
backend/main.py now does three things, in a function called create_app():

1. Creates the FastAPI app with the API-key check attached to the app itself (not to each route). That means every route — including every route any future prompt adds — is locked automatically. Nobody can forget to lock a door.
2. Registers the error handlers (Part 4), so every error comes out in the envelope.
3. Plugs in the four routers, and keeps the /health route that just says {"status": "ok"}.

Why a create_app() function instead of just "app = FastAPI()"? So the tests can build a fresh app, add some throwaway test routes to it, and check them — without touching the real app.

Part 3 — The lock: X-API-Key (FR-AUTH-001)
New file: backend/app/core/auth.py

Every request must carry a header:
    X-API-Key: <the API_KEY value from your .env>

The get_api_key function checks it. Three things worth knowing:

- Missing key, wrong key → 401 UNAUTHORIZED, in the envelope.
- If API_KEY isn't set at all in .env, every request is refused. This is called "failing closed": a misconfigured server locks everyone out instead of letting everyone in.
- The comparison uses secrets.compare_digest, not ==. A normal == stops at the first wrong letter, so an attacker could time the answers and guess the key one letter at a time. compare_digest always takes the same time.

The check runs before the request body is even looked at. So someone without a key gets 401, never a 422 that hints at what the body should look like.

Bonus: because it's written with FastAPI's APIKeyHeader, the Swagger page at http://localhost:8000/docs gets an "Authorize" button. Paste your key there once and you can try every endpoint from the browser.

Part 4 — The error envelope (FR-API-003 / FR-API-004)
New file: backend/app/core/errors.py

Every error AGEM sends, no matter where it came from, looks exactly like this:

    {
      "error_code": "AGENT_NOT_FOUND",
      "message": "Agent 3f2a... does not exist.",
      "details": { ... }
    }

Why this matters: the frontend only needs one piece of code to show errors. It never has to guess whether an error is a string, a list, or FastAPI's own format.

The error code list — exactly Architecture §10.2, nothing added:

Code                        HTTP   When
VALIDATION_ERROR            422    The request body is wrong
AGENT_NOT_FOUND             404    No agent with that id
WORKFLOW_NOT_FOUND          404    No workflow with that id
EXECUTION_NOT_FOUND         404    No execution with that id
CAPABILITY_NOT_FOUND        404    No capability with that id
WORKFLOW_CYCLE_DETECTED     400    The workflow's steps go round in a circle
UNAUTHORIZED                401    Missing or wrong X-API-Key
CAPABILITY_BUILD_FAILED     500    The Capability Engine gave up after 3 rounds
OUTPUT_DRIFT                500    A resumed step's output is missing what the next step needs

How later prompts use it: they never build error replies by hand. They just write

    raise AppError(ErrorCode.AGENT_NOT_FOUND, "Agent ... does not exist.")

and the handler looks up the right HTTP status from the table and builds the envelope.

Four handlers catch everything:
1. AppError — AGEM's own errors (the table above)
2. Validation errors — bad request body → 422 VALIDATION_ERROR, with details.errors listing each problem (which field, what's wrong)
3. FastAPI/Starlette's own errors — unknown address, wrong method (see the judgment call below)
4. Anything unexpected (a crash) → 500 INTERNAL_ERROR with a generic message. The real error is never sent to the user — only the server log sees it.

A safety detail: normally a validation error echoes back what you sent. If someone registers an agent with a typo in the body, that echo could include their agent credentials. The handler strips the echoed values and keeps only "which field" and "what's wrong", so a secret can never bounce back in an error (FR-AUTH-004).

Part 5 — Strict schemas (FR-API-002)
New files: backend/app/schemas/base.py and schemas/__init__.py

There's one base class, StrictModel, with extra = "forbid". Every request and response schema in later prompts inherits from it.

What that does: if someone sends a field AGEM doesn't know — say "framwork" instead of "framework" — the request is rejected with 422, instead of the typo being silently ignored and the default value used. Silent ignores are how bugs hide.

No real schemas exist yet (there are no endpoints to use them). Prompt 5 writes the first ones (agent registration).

Part 6 — config.py: the one place secrets are read (FR-AUTH-004)
backend/app/core/config.py was rewritten with pydantic-settings. It now reads:

Setting         From .env        Used by
database_url    DATABASE_URL     the database connection
api_key         API_KEY          the X-API-Key check, the seed
llm_provider    LLM_PROVIDER     the LLM wrapper (Prompt 6) — must be anthropic or openai
llm_api_key     LLM_API_KEY      the LLM wrapper (Prompt 6)
fernet_key      FERNET_KEY       encrypting agent credentials (Prompt 5)

The three secrets are stored as SecretStr. If anyone ever prints the settings or they show up in an error trace, you see ********** instead of the key. To read the real value, code has to deliberately call .get_secret_value().

Rule from now on: nothing else in the codebase reads environment variables. Everything asks get_settings().

Part 7 — The seed: one user owns everything (FR-AUTH-002)
backend/app/db/seed.py now has seed_owner(db).

Remember from Prompt 2: every agent, workflow and execution must belong to a user, but the MVP has no login. So there's exactly one user — owner@agem.local — who owns everything.

seed_owner:
- creates that user if it doesn't exist
- if it already exists, leaves it alone (running it twice never makes two users)
- stores only a hash of API_KEY, never the key itself
- if you change API_KEY in .env, running it again updates the stored hash
- refuses to run if API_KEY is empty

Why SHA-256 and not bcrypt? bcrypt is deliberately slow to protect short human passwords like "fluffy123". API_KEY is a 43-character random token — nobody can guess it, slow or fast. So a fast hash is enough, and it avoids adding another package.

To run it (after the tables exist):

    docker compose exec backend alembic upgrade head
    docker compose exec backend python -m app.db.seed

Part 8 — How it was checked
Docker still isn't installed/running on this machine, so I made a separate Python environment (in a temp folder, not in your project) with requirements.txt and ran everything there.

Tests: backend/tests/test_api.py — 29 new tests. All 71 tests pass (29 new + the 42 from Prompt 2).

What they prove:
- Every real route in the app returns 401 in the envelope with no key. This test finds routes automatically, so when Prompt 5 adds /api/agents, those routes are checked too without anyone editing the test.
- A wrong key → 401. The right key → 200. An unset API_KEY → everything refused.
- No key + bad body → 401, not 422 (the lock is checked first).
- Unknown field → 422 VALIDATION_ERROR. Missing field, wrong type, broken JSON → 422 too.
- A secret sent in a bad body never appears in the error reply.
- The error code list is exactly the 9 codes from §10.2, each with the right HTTP status. (Typed out by hand in the test, like Prompt 2's transitions, so an accidental change gets caught.)
- Unknown address → 404 in the envelope; wrong method → 405 in the envelope; a crash → 500 in the envelope, with no internal details leaked.
- The seed creates one owner, never two; stores a hash not the key; follows a changed key; refuses an empty key.
- Printing the settings never shows any of the three secrets.

Live check — I also started the real server and called it:

    no key     → 401 {"error_code":"UNAUTHORIZED","message":"Missing or invalid X-API-Key header.","details":{}}
    wrong key  → 401 (same)
    right key  → 200 {"status":"ok"}
    unknown    → 404 {"error_code":"NOT_FOUND","message":"Not Found","details":{}}

The seed was tested against an in-memory SQLite database with just the users table, not real PostgreSQL. It's plain SQLAlchemy, so it will behave the same — but run the two commands in Part 7 once Docker is up to confirm.

Judgment calls — please check these
1. Two new files in app/core/. Architecture §25 only lists config.py in core/. The error envelope and the API-key check had to live somewhere, and P14 says one job per file, so I added app/core/errors.py and app/core/auth.py. Same for tests/test_api.py (§25 lists only four test files) and app/schemas/base.py (§25 names the folder but no files).

2. Error codes outside the catalogue. §10.2 only covers AGEM's own errors. It doesn't say what to send for "that address doesn't exist" (404), "wrong method" (405), or an unexpected crash (500). I used the standard HTTP names: NOT_FOUND, METHOD_NOT_ALLOWED, INTERNAL_ERROR. They're still in the envelope. If you'd rather add them to §10.2 officially, that's a docs change, not a code change.

3. /api vs /api/v1. docs/api-spec.md says "REST over /api/v1", but Architecture §10.3 lists every endpoint as /api/agents, /api/workflows, etc. The prompt pointed me at Architecture, so I used /api. One of the two docs should be corrected so they agree.

4. /health is locked too. Its old comment said docker-compose and run_dev.sh use it — they don't. Since "every route" must require the key, /health now needs it as well. If you later add a Docker healthcheck, it'll need to send the header.

5. The Swagger page (/docs) is open. FastAPI's built-in docs page and /openapi.json aren't "routes" in the app's sense, so the lock doesn't cover them. They only describe the API — no data, no secrets — and they're very handy for testing. Easy to turn off if you want.

6. The seed doesn't run automatically. It's a function plus a command. I didn't make the backend run it on startup, because on a fresh machine the tables don't exist yet and the server would crash before you could run the migration. Wiring migration + seed into startup is a small Dockerfile change if you want it.

Heads-up for Prompt 5
The prompt says DELETE /api/agents/{id} returns 409 when the agent is in use. There's no 409 code in §10.2. Prompt 5 will need a name for it (e.g. AGENT_IN_USE) — that's a docs decision to make before building it.

What I did NOT add, on purpose
- No OAuth, JWT, login, user accounts or roles (FR-AUTH-003)
- No real endpoints — the four routers are empty
- No real request/response schemas — only the strict base class
- No auto-run of migrations or the seed on startup (see judgment call 6)

Files changed
New:
- backend/app/core/errors.py — error codes + envelope + handlers
- backend/app/core/auth.py — get_api_key
- backend/app/schemas/base.py — StrictModel
- backend/tests/test_api.py — 29 tests

Rewritten:
- backend/main.py — create_app(), global auth, handlers, routers
- backend/app/core/config.py — all settings, secrets as SecretStr
- backend/app/db/seed.py — seed_owner()
- backend/app/api/agents.py, workflows.py, executions.py, capabilities.py — empty routers
- backend/app/schemas/__init__.py — exports StrictModel

Where you are now

✅ Prompt 1  — skeleton + docker setup
✅ Prompt 2  — database: 9 tables, enums, can_transition, first migration
✅ Prompt 3  — FastAPI app + X-API-Key check + error envelope + owner seed   ← just finished
⬜ Prompt 4  — adapter contract + REST adapter + Python wrapper
⬜ Prompt 5  — agent registration API (and the AgentFramework fix from Prompt 2)
⬜ ... and the rest

AGEM now has a front door that works: it answers, it checks the key, and it says "no" the same way every time. Every endpoint from Prompt 5 onwards drops straight into this — locked by default, errors already handled.
