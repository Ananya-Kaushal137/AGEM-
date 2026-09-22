"""Executes untrusted, LLM-generated code inside the sandbox container.

Invoked by backend/capability_engine/sandbox.py in a fresh container per run:
`--network none`, 10 s timeout, `--memory=256m --cpus=0.5`, destroyed afterwards
(Architecture §13.4). Code and inputs arrive by file mount; the result is written
back as JSON. Implemented in Prompt 8.
"""
