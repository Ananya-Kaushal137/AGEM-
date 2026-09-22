# ADR-001 — Separate Sandbox Image

See Architecture §31. Untrusted code never executes in the backend process;
`sandbox_runner/` is its own minimal image with no network access.
