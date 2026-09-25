# ADR-008 — Rules-First Diagnosis; Tool Results Passed to Remote Agents

See Architecture §31 and `docs/failure_diagnosis.md`. Explicit `error_type` codes are
classified by rules; only unmatched errors go to the LLM. A verified capability is run
by AGEM in the sandbox and its result is sent to the resumed step in `context.tool_results`.
The registry is checked before search, and searched tools are verified like built ones.
