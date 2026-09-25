"""Asks the Web Research Agent (research_agent/, ADR-011) before anything is built.

Searcher.research(capability_name: str, context: dict) -> ResearchNotes
ResearchNotes = free_tool: Tool | None, definition, examples, sources.
One call per gap, 30 s timeout; empty notes on timeout/error.
"""
