#!/usr/bin/env bash
#
# AGEM — the one command that starts everything (FR-DEP-007).
#
#   ./scripts/run_dev.sh              start all four services
#   ./scripts/run_dev.sh --build      force a rebuild first
#   ./scripts/run_dev.sh --down       stop everything
#
# Brings up frontend, backend, PostgreSQL and the sandbox image on this one
# machine (FR-DEP-001, Architecture §6, §8).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "error: Docker Compose not found. Install Docker Desktop (FRS §2.3)." >&2
  exit 1
fi

if [[ "${1:-}" == "--down" ]]; then
  "${COMPOSE[@]}" down
  exit 0
fi

# One .env per machine, each member with their own LLM key (Architecture §6).
if [[ ! -f .env ]]; then
  echo "no .env found — creating one from .env.example"
  cp .env.example .env
  echo
  echo "  Edit .env before running a workflow: API_KEY, LLM_API_KEY and"
  echo "  FERNET_KEY are all placeholders (Architecture §24)."
  echo
fi

BUILD=()
if [[ "${1:-}" == "--build" ]]; then
  BUILD=(--build)
fi

echo "starting AGEM: frontend · backend · postgres · sandbox"
"${COMPOSE[@]}" up "${BUILD[@]}"
