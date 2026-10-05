"""Searches the web (Tavily) and summarises it through the LLM wrapper before anything is built (docs/websearch.md).

Searcher.research(capability_name: str, context: dict) -> ResearchNotes
ResearchNotes = free_tool: Tool | None, definition, examples, sources.
One research per gap, 30 s limit for search + LLM call; empty notes on timeout/error.
"""
