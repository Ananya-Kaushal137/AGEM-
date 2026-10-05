The short version
Prompt 1 built the empty house. Prompt 2 built the filing cabinet. Prompt 3 built the front door and the lock. Prompt 4 built the telephone AGEM uses to call agents.

AGEM never runs an agent's code itself. Every agent lives outside AGEM and is called over the network (HTTP). Prompt 4 wrote the two sides of that phone call:
- AGEM's side: the adapter — one standard way for AGEM to call any agent and hear back.
- The agent's side: the Python wrapper — a small file a developer puts around their existing Python agent so it can answer AGEM's calls, without changing the agent's code.

Still no features you can click. Nothing calls the adapter yet. Prompt 5 (agent registration) uses health(), and the step executor (a later prompt) uses execute().

Think of the hotel again: the rooms, the register and the receptionist exist. Prompt 4 installed the phone at reception, and handed guests a phone they can plug into their own room. Both follow the same script, so any guest can be called the same way.

What the task actually asked for
Prompt 4 in prompts/CLAUDEBUILDPROMPTS said:

- backend/adapters/base_adapter.py — an abstract class with exactly one method, async execute(input: dict) -> dict (FR-ADP-001)
- backend/adapters/rest_adapter.py — calls an agent over HTTP using the frozen contract in docs/api-spec.md (FR-ADP-002, FR-ADP-010), plus a health() call for GET /health, used by registration in Prompt 5
- agent_wrappers/python_wrapper.py — a small FastAPI template that exposes a developer's existing Python agent over /health + /execute, with no change to the agent's code (FR-ADP-011)

"Done when": a REST agent — including a Python agent behind python_wrapper.py — can be called through execute() and returns the contract shape; no framework-specific import exists outside adapters/ (FR-ADP-006).

What it said NOT to do: never import an agent's code into AGEM (FR-ADP-007, P5). No LangChain or CrewAI adapter yet — that's Prompt 15, Week 7.

Docs read first: Architecture §3 (P5, P6, P11, P13), §16.1, §23.1, §25, §31 (ADR-005, ADR-009) · FRS §3.2 (FR-ADP-001…011), §5 (BR-12, BR-13) · api-spec.md · final_flow.md §1 and §3.3.

Part 1 — The agent contract (the script both sides follow)
This was already frozen in docs/api-spec.md. Every agent must answer two calls:

    GET  {endpoint}/health     → 200 OK          "are you alive?"

    POST {endpoint}/execute                      "do this work"
      request:  { "task": "...", "input": {...}, "context": {...} }
      success:  { "status": "SUCCEEDED", "output": {...} }
      failure:  { "status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest" }

context is where AGEM sends extra information. The important one is context.tool_results: when an agent was missing a tool, AGEM builds the tool, runs it, and calls the agent again with the answer in there.

Prompt 4 didn't change this contract. It wrote code on both sides that speaks it.

Part 2 — base_adapter.py: the one shape every agent has inside AGEM
File: backend/adapters/base_adapter.py

It's an abstract class called BaseAdapter with exactly one method:

    async def execute(self, input: dict) -> dict

"Abstract" means you can't use BaseAdapter directly — it's a rule, not a tool. Every real adapter (REST now, LangChain or CrewAI in Week 7, maybe MCP in Week 8) must provide execute().

Why this matters: the Orchestrator never needs to know what kind of agent it's calling. It only ever calls execute(). Adding a new framework later means adding one file in adapters/ — nothing in orchestrator/ changes (P6, BR-12).

The input dict is the whole request body: {task, input, context}.

Part 3 — rest_adapter.py: AGEM's phone
File: backend/adapters/rest_adapter.py

    adapter = RestAdapter("http://localhost:9002")
    reply = await adapter.execute({"task": "...", "input": {...}, "context": {}})
    ok = await adapter.health()

execute() sends POST {endpoint}/execute and gives back the agent's reply exactly as the contract says — either the SUCCEEDED shape or the FAILED shape.

health() sends GET {endpoint}/health and returns True only if the agent answers 200. It never crashes: if the agent is down, it just returns False. Prompt 5 uses this so a mistyped endpoint is rejected at registration, instead of failing in the middle of the demo.

Small detail: a trailing slash on the endpoint ("http://host:9002/") is fine — it's removed.

What happens when the call goes wrong?
The adapter does NOT turn problems into the standard error shape. It raises an error instead, and a later prompt's step_executor.py does the translating. That's on purpose: P13 says all errors are normalised at exactly one point, and that point is step_executor.py.

What goes wrong                          What the adapter raises       step_executor will call it
The agent takes longer than 30 s         httpx.TimeoutException        TIMEOUT
Nothing is listening at the address      httpx.ConnectError            CONNECTION_ERROR
The agent answers 500, 503, …            httpx.HTTPStatusError         HTTP_5XX
The agent answers 400, 404, …            httpx.HTTPStatusError         AGENT_ERROR
The reply isn't JSON, or isn't the       ValueError                    INVALID_JSON
contract shape (no "status" field, etc.)

A reply that IS the contract shape — including {"status": "FAILED", "error": "MISSING_CAPABILITY", ...} — is returned normally, not raised. It's a proper answer from the agent, just not a happy one.

Part 4 — python_wrapper.py: the phone a developer plugs into their agent
File: agent_wrappers/python_wrapper.py (at the repo root, NOT inside backend/)

Some agents are just Python code with no web address. The wrapper gives them one. It runs in the developer's own process, on their machine or container — never inside AGEM.

The developer's agent only needs to be a function like this (their existing code, unchanged):

    # my_agent.py
    def run(task, input, context):
        return {"summary": f"{task}: {input['company']}"}

Then they start the wrapper in one command:

    python python_wrapper.py my_agent:run --port 9001

and register it with AGEM as framework "rest", endpoint http://<host>:9001. That's it — AGEM can now call it.

There's also a second way, for people writing their own small file (Prompt 5's demo agents will use this):

    from python_wrapper import create_app
    from my_agent import run
    app = create_app(run)

What the wrapper does with the agent's answer:
- Agent returns something normally → {"status": "SUCCEEDED", "output": <what it returned>}
- Agent raises MissingToolError("calculate_compound_interest") → {"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest"}
- Agent crashes with any other error → the wrapper answers 500, which AGEM treats as "the agent's server crashed" (HTTP_5XX, a normal error that gets retried)

Async agents (async def run(...)) work too.

/health just answers {"ok": true}.

Part 5 — How it was checked
Docker still isn't running on this machine, so tests ran in the same separate Python environment as Prompt 3 (a temp folder, not your project).

Tests: backend/tests/test_adapters.py — 21 new tests. All 92 tests pass (21 new + 71 from Prompts 2–3).

The tests start real little web servers on this computer and call them over real HTTP. So a timeout in the tests is a real timeout, and a broken reply is really broken — nothing is faked.

What they prove:
- BaseAdapter has exactly one abstract method, execute, and it's async. It can't be used directly. RestAdapter follows it.
- A Python agent behind the wrapper, called through execute():
  - with the tool result in context → SUCCEEDED with the right output
  - without it → FAILED / MISSING_CAPABILITY / calculate_compound_interest
  (This is exactly the Finance agent's behaviour in the demo.)
- Async agents work. A crashing agent gives a 500. input and context can be left out of the request.
- health(): True for a running agent, False when nothing is listening, False for a non-200 answer.
- Every failure in the Part 3 table raises the right error: slow agent → timeout, nothing listening → connection error, 503 → status error, HTML / a JSON list / a reply with no "status" → ValueError.
- execute() always has a time limit (P11: nothing waits forever).
- No file in backend/ outside adapters/ imports LangChain, CrewAI or MCP (FR-ADP-006).
- No backend code imports the wrapper or any agent code (FR-ADP-007).

Live check — I also ran the wrapper from the command line with a tiny test agent, the way a developer would, and called it through the adapter:

    GET  /health            → {"ok": true}
    adapter.health()        → True
    adapter.execute(...)    → {'status': 'SUCCEEDED', 'output': {'summary': 'Report: Acme'}}

Judgment calls — please check these
1. Errors are raised, not converted. The adapter raises errors and step_executor.py (later prompt) converts them into {"status": "FAILED", "error_type": ..., "raw_error": ...}. That follows P13 and the build prompts, which put normalisation in step_executor.py. It means FR-ADP-008's "malformed JSON normalised into the standard error shape" test can only be fully written when step_executor.py exists. For now the tests check each failure raises its own distinct error.

2. 30-second timeout for agent calls. The docs say everything must be bounded (P11) but never give a number for a normal agent call. I used 30 s, the same as the only other call limit the docs do give (web research — 30 s, docs/websearch.md). It can be changed per adapter: RestAdapter(endpoint, timeout=60). health() uses 5 s. If you want an official number, it's a docs change.

3. Agent credentials are not sent yet. The docs say credentials are stored encrypted (Prompt 5), but never say how they're sent to the agent — which header, what format. So the adapter doesn't send them. This needs a decision before or during Prompt 5.

4. An agent crash becomes a 500. The contract only defines one FAILED error (MISSING_CAPABILITY). Rather than invent a new field in a frozen contract, any other crash inside the agent makes the wrapper answer 500. AGEM already knows what to do with that (HTTP_5XX → normal error → retry).

5. The agent function gets task, input and context. The docs' sample wrapper only passed input, but the Finance demo agent needs context.tool_results, so the wrapper passes all three by name: agent(task=..., input=..., context=...). If a developer's function looks different, they write a two-line function around it — their agent's code still doesn't change.

6. The wrapper doesn't reject unknown fields. AGEM's own API rejects unknown fields (extra = "forbid"), but the wrapper is on the agent's side, and a strict wrapper would only turn harmless extras into failures. So it ignores them.

Heads-up for Prompt 5
- Use RestAdapter(endpoint).health() for FR-AGT-011. It already returns a clean True/False.
- Decide how credentials are sent (judgment call 3).
- The four demo agents can each be about 10 lines: a run(task, input, context) function plus create_app(run).

What I did NOT add, on purpose
- No LangChain, CrewAI or MCP adapter (Prompt 15 / stretch)
- No step_executor.py, no error normalisation (later prompt)
- No agent registration endpoint (Prompt 5)
- No demo agents (Prompt 5)
- No credentials handling (see judgment call 3)

Files changed
New:
- agent_wrappers/python_wrapper.py — the wrapper template

Rewritten (were one-line stubs):
- backend/adapters/base_adapter.py — BaseAdapter with execute()
- backend/adapters/rest_adapter.py — RestAdapter with execute() and health()
- backend/tests/test_adapters.py — 21 tests

Where you are now

✅ Prompt 1  — skeleton + docker setup
✅ Prompt 2  — database: 9 tables, enums, can_transition, first migration
✅ Prompt 3  — FastAPI app + X-API-Key check + error envelope + owner seed
✅ Prompt 4  — adapter contract + REST adapter + Python wrapper   ← just finished
⬜ Prompt 5  — agent registration API (and the AgentFramework fix from Prompt 2)
⬜ ... and the rest

AGEM can now call any agent that speaks the contract, and any Python agent can be made to speak it with one command. Prompt 5 uses this to let people register their agents.
