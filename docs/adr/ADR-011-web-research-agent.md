# ADR-011 — A Web Research Agent Performs Web Search for the Capability Engine

See Architecture §31 and `docs/final_flow.md` §5.4. The LLM answers only from what it already
knows, so a system agent in `research_agent/` (port 9005, standard `/health` + `/execute` contract)
searches the web when a capability is missing. It returns research notes: a free tool if one exists,
the formula, worked examples with answers, and sources. Notes feed the build prompt and become
reference test cases. One call per gap, 30 s timeout, failure means build without notes. Web text is
data, never instructions; found code is verified like built code; the sandbox keeps no network.
