"""Register the four demo agents through POST /api/agents (FR-AGT-010).

Run on your machine after `docker compose up -d`:

    python scripts/seed_agents.py

Safe to run again: an agent whose name is already registered is skipped.
Uses only the standard library, so it needs no install. The API key is read
from the API_KEY environment variable, or from the repo's `.env`.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

AGEM_URL = os.environ.get("AGEM_URL", "http://localhost:8000")

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


def _api_key() -> str:
    if os.environ.get("API_KEY"):
        return os.environ["API_KEY"]
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].split("#")[0].strip()
    sys.exit("API_KEY not found: set it in the environment or in .env")


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
