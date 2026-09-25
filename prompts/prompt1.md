The short version
I didn't build any working features. I built the empty house — all the rooms, labelled, with the plumbing connected. Nothing lives in it yet.

Think of it like building a hotel: I put up the walls, numbered every room, and connected the water and electricity. The rooms are empty. Later prompts (2, 3, 4...) move the furniture in.

What the task actually asked for
Your docs/BUILD-PROMPTS.md has 18 prompts. This was Prompt 1. It said three things:

Create every folder and file that Architecture.md §25 lists
Write a docker-compose.yml that starts 4 things at once
Write .env.example and run_dev.sh
So I read those doc sections first (§25, §6, §8, §30 in Architecture, §3.14 and §2.2 in the FRS) and then did exactly those three things.

Part 1 — The folder skeleton
Architecture.md §25 has a big tree diagram showing every folder your project should have. I copied it file for file.

Here's what each part is for:

frontend/ — the website your users see. React.

src/pages/ — the 5 screens: Dashboard, Agents, Workflows, Executions, Capabilities
src/components/ — reusable pieces, like WorkflowGraph.tsx (the pretty DAG picture for your demo)
src/api/ — the code that talks to the backend
src/types/ — TypeScript definitions
backend/ — the Python brain. This is where 90% of your project lives.

app/api/ — 4 files (agents.py, workflows.py, executions.py, capabilities.py). These are the "doors" the frontend knocks on.
app/models/ — database tables (Prompt 2 fills this)
app/core/config.py — the only place secrets get read. Your docs are strict about this (rule P16).
orchestrator/ — the thing that runs your workflow step by step, and master_agent.py which asks the LLM "why did this step fail?"
adapters/ — how you talk to agents built in different frameworks (REST, LangChain, CrewAI). This is your "works with anything" story.
capability_engine/ — your star feature. 7 files, one per step: ask the Web Research Agent (free tool, formula, examples) → build one with the LLM → run it in a sandbox → test it → verify it (≥ 90%, refined with failing cases) → register it.
tests/ — the 4 test files your FRS requires
sandbox_runner/ — a separate, tiny, locked-down box where LLM-written code is allowed to run. Separate on purpose, because you don't let untrusted code run in your main app.

database/init.sql, scripts/, docs/adr/ — the rest of the tree.

Important: the files are basically empty
Every Python file I made contains just a comment saying what it will do and which prompt fills it. For example, backend/capability_engine/sandbox.py is only this:


"""Isolated execution via a fresh sandbox_runner container per run (Architecture §13.4).

Sandbox.run(code: str, inputs: list[dict]) -> SandboxResult
"""
That second line is the function signature your Architecture §13.2 fixed. Why it matters: you're a 2-person team. If both of you know Sandbox.run takes code + inputs and returns a result, you can build the two halves separately without waiting for each other. Your doc says exactly that, so I wrote those signatures into the stubs now.

Part 2 — docker-compose.yml
This is the file that starts your whole project with one command.

Right now, to run your project you'd have to: start Postgres, then start Python, then start the React server, in three terminals, in the right order. Compose does all of it for you.

docker-compose.yml defines 4 services:

Service	Port	What it is
frontend	5173	The React website
backend	8000	Python — API + orchestrator + master agent + capability engine, all one process
postgres	5432	The database
sandbox	—	The locked-down code-running box
A few things I wired in that are worth knowing:

The backend waits for the database. Postgres has a healthcheck — compose literally pings it until it answers, then starts the backend. Without this your backend crashes on startup because the DB isn't ready yet.

The backend can talk to Docker itself. I mounted /var/run/docker.sock into the backend. That sounds weird but it's necessary: your capability engine needs to start new sandbox containers on demand, so it needs to reach the Docker daemon.

The sandbox has no internet. network_mode: "none", plus mem_limit: 256m and cpus: 0.5 — exactly the numbers your Architecture §13.4 specifies.

One judgment call I made — please check this
Your docs say two slightly different things:

§13.4 says the sandbox should be a fresh container per run, destroyed after
FR-DEP-001 says compose must bring up the sandbox as one of four services
Those don't quite fit together. A "service that's always running" is the opposite of "a fresh one each time."

What I did: compose builds and tags the sandbox image, and keeps one idle container sitting there doing nothing (sleep infinity). Its only job is to guarantee the image exists. Then your capability engine starts its own fresh containers from that image whenever it needs one.

That satisfies both readings. If you'd prefer the sandbox to just build-and-exit instead, it's a 2-line change — tell me.

Part 3 — .env.example
.env.example is the template for your secrets. You copy it to .env and fill in real values. .env is in .gitignore so your real keys never get pushed to GitHub.

Architecture §24 named exactly three secrets, so those are the three I put in:

API_KEY — one shared password. Every request to your backend must send it as an X-API-Key header. No login system, no user accounts — your docs deliberately chose this because your only users are you two plus an examiner.
LLM_API_KEY — your OpenAI or Anthropic key. Used by master_agent.py (diagnosing failures) and builder.py (writing tools).
FERNET_KEY — encrypts agent passwords before they go in the database, so they're not sitting there in plain text.
I put the command to generate each one right in the comments, so you don't have to look it up:


python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
I deliberately did not add an LLM_MODEL variable — neither of your docs picks a model, so that decision belongs to whichever prompt first actually calls the API.

Part 4 — run_dev.sh
scripts/run_dev.sh is your one command:


./scripts/run_dev.sh
It does four helpful things:

Finds your project root, so it works no matter which folder you run it from
Figures out whether your machine has docker compose or the older docker-compose
If you have no .env, it makes one for you from the example and warns you the keys are placeholders
Starts everything
Also supports --build (rebuild from scratch) and --down (stop everything).

What I couldn't verify — the honest bit
The task's "done when" was: docker-compose up starts all four services.

I could not confirm that. I ran it, and it failed — Docker Desktop isn't running on your machine. You have the Docker command-line tool installed, but not the engine behind it.

That's why I could check that the compose file is valid (that check doesn't need the engine) but not that it actually runs.

What you need to do: open Docker Desktop, wait for it to say "running", then:


cd AGEM-
./scripts/run_dev.sh --build
First run will take several minutes (downloading Python, Node, Postgres). After that it's fast.

One thing to expect: that first build creates frontend/package-lock.json. Commit that file — Prompt 18's CI uses npm ci, which refuses to run without it.

What I did NOT add, on purpose
The prompt explicitly forbade these, and your Architecture §30 backs it up:

Redis / Celery — marked OPTIONAL, "not before Week 8 at the earliest"
Kubernetes — explicitly out of scope
GitHub Actions CI — that's Prompt 18, not now
So none of those are anywhere in the project.

Where you are now

✅ Prompt 1  — skeleton + docker setup        ← just finished
⬜ Prompt 2  — database tables (9 of them)
⬜ Prompt 3  — FastAPI app + the X-API-Key check
⬜ ... 15 more
Nothing in your project does anything yet. But every file has a home, the four containers know how to find each other, and the secrets have one front door. That's the whole point of Prompt 1.
