# ADR-002 — Master Agent and Orchestrator: Separate Concepts, One Service for MVP

See Architecture §31. The Orchestrator decides what runs next; the Master Agent
diagnoses failures. Separate files, same Python process.
