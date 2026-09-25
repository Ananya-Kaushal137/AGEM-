# ADR-009 — Agents Registered by HTTP Endpoint; Wrapper Templates; MCP over HTTP Only

See Architecture §31 and `docs/final_flow.md` §1. Every agent is registered by endpoint and
answers `GET /health` and `POST /execute`. Code-only agents run behind a wrapper template in
`agent_wrappers/`, never inside AGEM. `framework` is `rest`, `langchain` or `crewai`.
MCP is a stretch adapter over HTTP only; `stdio` is forbidden.
