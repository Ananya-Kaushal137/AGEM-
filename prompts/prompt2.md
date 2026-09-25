The short version
Prompt 1 built the empty house. Prompt 2 built the filing cabinet: the database.

It decided exactly what AGEM will remember — every agent, every workflow, every run, every step, every saved checkpoint and every tool the Capability Engine builds — and created the tables in PostgreSQL to hold it. It also wrote down, in one place, every "status" a thing can have, and the one rule that says which status changes are allowed.

Still no working features. Nothing calls an agent yet and there's no API yet. But from now on, everything AGEM does has a place to be saved.

Think of the hotel again: Prompt 1 built the rooms. Prompt 2 built the front desk's register — the book where every guest, every booking and every room status is written down.

Commit: cf38f22 "feat(db): database schema and SQLAlchemy models for nine core entities" (on the staging branch).

What the task actually asked for
Prompt 2 in prompts/CLAUDEBUILDPROMPTS said four things:

Write SQLAlchemy models for the nine entities in Architecture §14.1
Follow the table conventions in Architecture §14.2 (UUID ids, UTC timestamps, enums, JSONB, foreign key rules)
Define every status enum from Architecture §15, plus the can_transition(old, new) function
Set up Alembic and generate the first migration
"Done when": alembic upgrade head creates all nine tables, and can_transition rejects every illegal move.

Two things it said NOT to do:
- No generic "repository" layer — just plain per-table functions later (Architecture §14.4)
- No State-pattern class hierarchy for step status — one enum plus one function is enough (Architecture §11.5)

Part 1 — What is a "model"?
A model is a Python class that describes one database table. Each attribute in the class is one column.

SQLAlchemy is the library that turns these Python classes into real PostgreSQL tables, and lets the rest of the code read and write rows as Python objects instead of writing SQL by hand.

All models live in backend/app/models/.

Part 2 — The nine tables
Here is every table, what it stores, and why it exists.

File: user.py
users — who owns things.
Columns: user_id, email, api_key_hash.
The MVP has no login (one static API key), so there will be exactly one user row that owns everything. The table exists so "owned by" is a real link, and adding real accounts later is easy. The API key is never stored as plain text — only its hash.

File: agent.py
agents — every agent registered with AGEM.
Columns: agent_id, user_id, name, framework, endpoint, description, status, encrypted_credentials.
Only the endpoint (the URL) is stored — never the agent's code. Credentials are stored encrypted (Fernet) and must never be sent back in any API reply.

agent_capabilities — which agent was given which tool.
Columns: agent_capability_id, agent_id, capability_id, granted_at, granted_by.
A record of every grant. The same tool can't be granted twice to the same agent (unique rule).

File: capability.py
capabilities — the registry of tools the Capability Engine found or built.
Columns: capability_id, name, version, source (BUILT or ACQUIRED), status (BUILDING, VERIFIED, FAILED), verification_score, implementation (the code, or a reference to the found tool).
Name + version must be unique, so two runs can never register the same tool twice. FAILED tools are kept, not deleted, so we can always explain why a tool was rejected.

File: workflow.py
workflows — saved workflow definitions.
Columns: workflow_id, user_id, name, definition (the DAG, as JSON), status (DRAFT, ACTIVE, ARCHIVED).
A workflow stays DRAFT until its DAG passes validation. Only then does it become ACTIVE, so a broken workflow can never be run.

workflow_agents — one row per step in a workflow.
Columns: workflow_agent_id, workflow_id, agent_id, step_order, depends_on, input_mapping.
- step_order = the running order, worked out once when the workflow is created and never re-sorted
- depends_on = which earlier steps this one waits for (a JSON list)
- input_mapping = which output field of an earlier step feeds this step
This is exactly what the Orchestrator reads to find READY steps.

File: execution.py
executions — one row per run of a workflow.
Columns: execution_id, workflow_id, user_id, status, started_at, finished_at, final_output, error_summary.
The status is never set by hand — it is worked out from the steps (PAUSED if any step is paused, FAILED if any failed, SUCCEEDED only when all succeeded).

execution_steps — one row per step inside one run.
Columns: step_id, execution_id, agent_id, step_order, input, output, status, recovery_stage, error, attempts, started_at, finished_at.
- step_order is copied in when the run starts, so editing the workflow later can't reorder a run that's already going
- error holds the standard error shape ({"status": "FAILED", "error_type": ..., "raw_error": ...})
- attempts counts tries, so a paused step goes to FAILED after 3 instead of pausing forever
- recovery_stage shows what's happening while a step is paused (DIAGNOSING, BUILDING, ...)

checkpoints — the "save game" for a step.
Columns: checkpoint_id, step_id, state (one JSON blob).
Saved when a step succeeds and again the moment it pauses. It holds the current step, the earlier outputs, the pending inputs and the recovery info — everything needed to resume without re-running earlier steps.

How they connect:

users ──< agents ──< agent_capabilities >── capabilities
users ──< workflows ──< workflow_agents >── agents
users ──< executions ──< execution_steps ──< checkpoints
           (each execution belongs to one workflow; each step uses one agent)

Part 3 — The rules every table follows
These live in backend/app/models/base.py, so no table can forget them.

1. IDs are UUIDs, created by our code — not 1, 2, 3 from the database.
   Why: the API can return an id before the row is saved, and ids are safe to put in a URL (nobody can guess "the next one").

2. Every table has created_at and updated_at, always in UTC.
   Why: one clock for everything; no time-zone confusion in logs or on the dashboard.

3. Every status column is a Python Enum, never free text.
   Why: a typo like "SUCEEDED" becomes impossible — the database refuses it.

4. Free-form data is stored as JSONB (PostgreSQL's JSON type): the workflow definition, step input/output, errors, and checkpoint state.
   Why: these shapes vary; separate tables would add complexity and buy nothing at this size.

Part 4 — The delete rules (foreign keys)
These decide what happens when you delete something that other rows point to. They are the reason some future API errors happen automatically.

RESTRICT — "you can't delete this while it's in use":
- agent → workflow_agents: an agent used in a workflow can't be deleted. This is exactly what will make DELETE /api/agents/{id} return 409 later.
- agent → execution_steps: past runs must still be able to show which agent they used.
- user → everything, workflow → executions: history is never silently lost.

CASCADE — "delete this, and its children go too":
- execution → execution_steps → checkpoints: delete a run and its steps and checkpoints go with it.
- workflow → workflow_agents: delete a workflow and its step list goes with it.
- agent / capability → agent_capabilities: grant records go with them.

All of these were tested against a real PostgreSQL database with real rows, not just written down.

Part 5 — The statuses (enums)
All in backend/app/models/enums.py — defined once, never typed out anywhere else. The frontend will copy these same values into frontend/src/types/.

Step status (exactly 5): PENDING, RUNNING, SUCCEEDED, FAILED, PAUSED
Recovery stage (exactly 8, only used while PAUSED): NONE, DIAGNOSING, SEARCHING, BUILDING, SANDBOXING, TESTING, VERIFYING, REGISTERED
Execution status: PENDING, RUNNING, PAUSED, SUCCEEDED, FAILED
Agent status: ACTIVE, INACTIVE
Workflow status: DRAFT, ACTIVE, ARCHIVED
Capability status: BUILDING, VERIFIED, FAILED
Agent framework: python, rest, langchain, crewai
Capability source: BUILT, ACQUIRED

Why is "recovery stage" separate from "step status"? So the step status can stay at just 5 values. While a step is PAUSED, the recovery stage tells the dashboard exactly what the Capability Engine is doing — that's the "watch it fix itself" moment in the demo.

Part 6 — can_transition: the one rule for step status
A step can't jump to any status it likes. can_transition(old, new) says yes or no.

Allowed moves (only these 6):
PENDING → RUNNING
RUNNING → SUCCEEDED
RUNNING → FAILED
RUNNING → PAUSED
PAUSED  → RUNNING   (resumed)
PAUSED  → FAILED    (out of tries)

Everything else is refused, including:
- anything out of SUCCEEDED or FAILED (they're final)
- a status "moving" to itself
- PENDING skipping straight to SUCCEEDED
- PAUSED jumping straight to SUCCEEDED (a resumed step must run again first)

Every future status change in the Orchestrator must go through this one function. It's a plain function on purpose — the docs explicitly reject a big class hierarchy for five states.

Part 7 — Alembic: the database's version history
Alembic is to the database what Git is to code. Each "migration" is one saved change to the table structure, and you can move forward or back.

- alembic upgrade head → build or update the tables to the latest version
- alembic downgrade -1 → undo the last change

What was set up:
- backend/alembic.ini and backend/alembic/env.py — Alembic's settings
- backend/alembic/versions/c776e466d326_initial_schema_nine_core_entities.py — the first migration, which creates all nine tables and the eight enum types

Two small decisions worth knowing:
1. env.py reads the database URL from app/core/config.py, not from alembic.ini. The docs require every setting and secret to have one single entry point (rule P16).
2. The auto-generated migration had two bugs, fixed by hand:
   - it used Text() without importing it, which would crash on upgrade
   - downgrade removed the tables but left the enum types behind, so upgrading again failed with "type already exists". It now drops them too, so down-then-up works cleanly.

The rule from now on: after this first migration, every change to the tables goes through a new Alembic migration. Never edit the tables by hand, and never add tables to database/init.sql.

Part 8 — Other small files touched
backend/app/core/config.py — now holds the database URL (read once from the environment). Prompt 3 adds the API key, the LLM key and the Fernet key here.
backend/app/db/database.py — the database connection, the session factory, and get_db() (FastAPI will use this to give each request its own session).
backend/requirements.txt — added tzdata. Alembic is set to UTC, and Windows needs this package for time zones to work.
database/init.sql — now only turns on the pgcrypto extension, with a comment saying the tables belong to Alembic.
backend/tests/test_orchestrator.py — 42 tests for the step status rules (see below).

Part 9 — How it was checked
- alembic upgrade head on a real PostgreSQL: creates all 10 tables (the 9 entities + Alembic's own alembic_version table) and all 8 enum types
- downgrade then upgrade again: works cleanly, no leftovers
- The delete rules (RESTRICT and CASCADE) tried with real rows
- 42 tests for can_transition, covering all 25 possible from→to pairs: the 6 allowed ones pass, the 19 others are refused, final states never move, no self-moves, and it still works when the database hands back plain strings

The allowed moves in the test file are typed out by hand on purpose, not copied from the code — so if someone changes the rules by mistake, the test catches it.

What I did NOT add, on purpose
- No API endpoints — that's Prompt 3 onwards
- No generic "repository" layer — the docs ask for small plain functions per table only (Architecture §14.4)
- No State-pattern classes — one enum + can_transition is the whole state machine
- Nothing in database/init.sql — tables belong to Alembic

⚠ One thing to fix — decided after Prompt 2 was built
Prompt 2 was built before we finalised how agents connect (ADR-009, docs/final_flow.md §1).

enums.py still has:
    AgentFramework: python, rest, langchain, crewai

The final decision is:
    AgentFramework: rest, and whichever ONE of langchain / crewai we build (plus mcp only if we build the MCP adapter)

"python" was dropped: a plain Python agent now runs behind agent_wrappers/python_wrapper.py and registers as "rest".

To fix it: remove PYTHON from AgentFramework and write a new Alembic migration that changes the agent_framework enum type in PostgreSQL. Do NOT edit the first migration — it's already pushed. The natural time to do this is at the start of Prompt 5 (agent registration), since that's where FR-AGT-002 is enforced.

Nothing else in Prompt 2 conflicts with the final flow. The error column already stores the full error JSON, so the extra "capability" field fits without any change, and step_order / depends_on are exactly what the Orchestrator needs for the READY-step rule.

Where you are now

✅ Prompt 1  — skeleton + docker setup
✅ Prompt 2  — database: 9 tables, enums, can_transition, first migration   ← just finished (pushed to staging)
⬜ Prompt 3  — FastAPI app + the X-API-Key check + error envelope
⬜ Prompt 4  — adapter contract + REST adapter + Python wrapper
⬜ ... and the rest

AGEM still can't do anything you can click on. But every piece of information it will ever need — agents, workflows, runs, steps, checkpoints, tools — now has a proper home, with rules that stop bad data from getting in.
