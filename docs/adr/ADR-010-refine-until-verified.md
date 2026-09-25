# ADR-010 — Refine the Generated Tool Until Verified; Do Not Fine-Tune the LLM

See Architecture §31 and `docs/final_flow.md` §5.3. The LLM is used as-is and never retrained.
The Capability Engine refines the generated tool instead: failing cases (input, returned value,
expected value) are fed into the next build prompt, at most 3 rounds. A tool is registered only at
accuracy ≥ 90% over about 10 cases, 0 regressions and identical results over 3 runs per case.
After resume, the step's output is checked against the next step's `input_mapping`; if it does not
fit, the step fails with `OUTPUT_DRIFT` and the next agent never receives it.
