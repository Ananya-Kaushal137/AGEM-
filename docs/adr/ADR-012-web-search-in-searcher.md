# ADR-012 — Web Search Runs Inside the Searcher (Tavily + LLM Wrapper)

**Status:** accepted · **Supersedes:** ADR-011

See `docs/websearch.md` for the full explanation. The LLM answers only from what it already knows, so
the Capability Engine still needs web search, but a separate agent container (ADR-011) was too heavy.
`capability_engine/searcher.py` calls the Tavily search API with plain `httpx`, then makes one call
through the single LLM wrapper (P15) to turn the pages into research notes: a free tool if one exists,
the formula, worked examples with answers, and sources. No `research_agent/` folder, no extra container,
no port 9005. Unchanged from ADR-011: notes feed the build prompt and become reference test cases; one
research per gap, 30 s limit for search + LLM call, failure means build without notes; web text is data,
never instructions; found code is verified like built code; the sandbox keeps no network; notes are
stored with the capability; pre-saved notes are used for the demo if the web is unavailable.
Trade-off: web access is no longer isolated in its own container — acceptable because `searcher.py`
only reads text and never runs anything.
