"""LLM-based tool generation. Returns Python source, never executes it.

Builder.build(capability_name: str, spec: dict, notes: ResearchNotes, feedback: list[dict] | None) -> str
feedback = the previous round's failing cases (input, returned, expected).
"""
