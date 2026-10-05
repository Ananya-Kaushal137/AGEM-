"""Register the four demo agents through POST /api/agents (FR-AGT-010).

Run on your machine after `docker compose up -d`:

    python scripts/seed_agents.py

Safe to run again: an agent whose name is already registered is skipped.
Uses only the standard library, so it needs no install. API_KEY and
BACKEND_PORT are read from the environment, or from the repo's `.env`; set
AGEM_URL to override the address completely.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Endpoints as the backend container sees them (compose service names).
DEMO_AGENTS = [
    {"name": "Research Agent", "endpoint": "http://research-agent:9001",
     "description": "Looks up a company's revenue and profit."},
    {"name": "Finance Agent", "endpoint": "http://finance-agent:9002",
     "description": "Projects an investment; needs calculate_compound_interest."},
    {"name": "Fact Checker", "endpoint": "http://fact-checker-agent:9003",
     "description": "Checks the financial figures are consistent."},
    {"name": "Writer Agent", "endpoint": "http://writer-agent:9004",
     "description": "Writes the final investment report."},
]


def _env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].split("#")[0].strip() or None
    return None


def _api_key() -> str:
    return _env("API_KEY") or sys.exit("API_KEY not found: set it in the environment or in .env")


# 127.0.0.1, not localhost: on Windows, localhost can resolve to an IPv6 port
# held by another program (e.g. WSL) instead of Docker.
AGEM_URL = _env("AGEM_URL") or f"http://127.0.0.1:{_env('BACKEND_PORT') or 8000}"


def _call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    request = urllib.request.Request(
        AGEM_URL + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-Key": _api_key(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main() -> None:
    status, existing = _call("GET", "/api/agents")
    if status != 200:
        sys.exit(f"Could not list agents ({status}): {existing}")
    registered = {a["name"] for a in existing}

    for agent in DEMO_AGENTS:
        if agent["name"] in registered:
            print(f"skip      {agent['name']} (already registered)")
            continue
        status, body = _call("POST", "/api/agents", {**agent, "framework": "rest"})
        if status == 201:
            print(f"ACTIVE    {agent['name']}  {body['agent_id']}")
        else:
            print(f"FAILED    {agent['name']}  {status}: {body.get('message', body)}")


if __name__ == "__main__":
    main()
