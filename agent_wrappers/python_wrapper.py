"""Expose an existing Python agent over the AGEM agent contract (FR-ADP-011, docs/api-spec.md).

This file runs in the developer's own process, never inside AGEM (FR-ADP-007).
The agent's code is not changed: point the wrapper at any callable
`agent(task, input, context) -> dict` (sync or async) and run it.

    python python_wrapper.py my_agent:run --port 9001

Or build the app yourself:

    from python_wrapper import create_app
    from my_agent import run
    app = create_app(run)

Then register it with AGEM as framework `rest`, endpoint `http://<host>:9001`.

If the agent lacks a tool, raise `MissingToolError("tool_name")`; AGEM answers
with that tool's result in `context.tool_results` when the step resumes. Any
other exception becomes a 500, which AGEM treats as the agent's server failing.
"""

import argparse
import importlib
import inspect
from typing import Any, Callable

from fastapi import FastAPI
from pydantic import BaseModel


class MissingToolError(Exception):
    def __init__(self, name: str):
        super().__init__(f"Missing capability: {name}")
        self.name = name


class ExecuteRequest(BaseModel):
    task: str
    input: dict = {}
    context: dict = {}


def create_app(agent: Callable[..., Any]) -> FastAPI:
    app = FastAPI(title="AGEM agent wrapper")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/execute")
    async def execute(req: ExecuteRequest):
        try:
            output = agent(task=req.task, input=req.input, context=req.context)
            if inspect.isawaitable(output):
                output = await output
        except MissingToolError as e:
            return {"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": e.name}
        return {"status": "SUCCEEDED", "output": output}

    return app


def load_agent(target: str) -> Callable[..., Any]:
    module_name, _, attr = target.partition(":")
    if not attr:
        raise SystemExit("Agent must be given as module:callable, e.g. my_agent:run")
    return getattr(importlib.import_module(module_name), attr)


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve a Python agent over the AGEM agent contract.")
    parser.add_argument("agent", help="module:callable, e.g. my_agent:run")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()
    uvicorn.run(create_app(load_agent(args.agent)), host=args.host, port=args.port)
