"""Tests for adapters (FR-DEP-004). LLM calls are mocked here (FR-DEP-006).

Prompt 4 "done when": a REST agent — including a Python agent behind
`python_wrapper.py` — can be called through `execute()` and returns the contract
shape; no framework-specific import exists outside `adapters/` (FR-ADP-006).

Agents run as real HTTP servers on localhost, so timeouts and bad replies are real.
"""

import asyncio
import inspect
import re
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from adapters.base_adapter import BaseAdapter
from adapters.rest_adapter import RestAdapter

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND.parent / "agent_wrappers"))

from python_wrapper import MissingToolError, create_app  # noqa: E402


def _serve(app: FastAPI) -> tuple[str, uvicorn.Server]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test agent did not start")
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}", server


def _free_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def _finance_agent(task: str, input: dict, context: dict) -> dict:
    tool_results = context.get("tool_results", {})
    if "calculate_compound_interest" not in tool_results:
        raise MissingToolError("calculate_compound_interest")
    return {"task": task, "company": input["company"], "interest": tool_results["calculate_compound_interest"]}


async def _async_agent(task: str, input: dict, context: dict) -> dict:
    await asyncio.sleep(0)
    return {"echo": input}


def _crashing_agent(task: str, input: dict, context: dict) -> dict:
    raise RuntimeError("boom")


def _raw_agent() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/execute/html/execute")
    def html():
        return PlainTextResponse("<html>not json</html>")

    @app.post("/execute/list/execute")
    def json_list():
        return JSONResponse([1, 2, 3])

    @app.post("/execute/nostatus/execute")
    def no_status():
        return {"output": {"x": 1}}

    @app.post("/execute/down/execute")
    def unavailable():
        return JSONResponse({"detail": "down"}, status_code=503)

    @app.post("/execute/slow/execute")
    async def slow_execute():
        await asyncio.sleep(2)
        return {"status": "SUCCEEDED", "output": {}}

    return app


@pytest.fixture(scope="module")
def finance_url():
    url, server = _serve(create_app(_finance_agent))
    yield url
    server.should_exit = True


@pytest.fixture(scope="module")
def raw_url():
    url, server = _serve(_raw_agent())
    yield url
    server.should_exit = True


def _run(coro):
    return asyncio.run(coro)


REQUEST = {"task": "Create a company investment report", "input": {"company": "Acme"}, "context": {}}


# --- base_adapter.py (FR-ADP-001) ---------------------------------------------

def test_base_adapter_defines_exactly_one_abstract_method():
    assert BaseAdapter.__abstractmethods__ == {"execute"}
    assert inspect.iscoroutinefunction(BaseAdapter.execute)
    assert list(inspect.signature(BaseAdapter.execute).parameters) == ["self", "input"]


def test_base_adapter_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BaseAdapter()


def test_rest_adapter_implements_the_contract():
    assert issubclass(RestAdapter, BaseAdapter)
    assert not RestAdapter.__abstractmethods__


# --- Python agent behind python_wrapper.py, called through execute() -----------

def test_success_reply_through_the_wrapper(finance_url):
    request = {**REQUEST, "context": {"tool_results": {"calculate_compound_interest": 1628.89}}}
    reply = _run(RestAdapter(finance_url).execute(request))
    assert reply == {
        "status": "SUCCEEDED",
        "output": {"task": REQUEST["task"], "company": "Acme", "interest": 1628.89},
    }


def test_missing_capability_reply_through_the_wrapper(finance_url):
    reply = _run(RestAdapter(finance_url).execute(REQUEST))
    assert reply == {"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest"}


def test_trailing_slash_on_endpoint_is_accepted(finance_url):
    reply = _run(RestAdapter(finance_url + "/").execute(REQUEST))
    assert reply["error"] == "MISSING_CAPABILITY"


def test_wrapper_accepts_async_agents():
    url, server = _serve(create_app(_async_agent))
    try:
        reply = _run(RestAdapter(url).execute(REQUEST))
    finally:
        server.should_exit = True
    assert reply == {"status": "SUCCEEDED", "output": {"echo": {"company": "Acme"}}}


def test_wrapper_turns_an_agent_crash_into_a_5xx():
    url, server = _serve(create_app(_crashing_agent))
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            _run(RestAdapter(url).execute(REQUEST))
    finally:
        server.should_exit = True
    assert exc.value.response.status_code == 500


def test_wrapper_input_and_context_are_optional(finance_url):
    reply = httpx.post(f"{finance_url}/execute", json={"task": "t"}).json()
    assert reply["error"] == "MISSING_CAPABILITY"


# --- health() (used by registration, FR-AGT-011) ------------------------------

def test_health_true_for_a_running_wrapper(finance_url):
    assert _run(RestAdapter(finance_url).health()) is True


def test_health_false_when_nothing_is_listening():
    assert _run(RestAdapter(_free_port_url()).health()) is False


def test_health_false_on_non_200(raw_url):
    assert _run(RestAdapter(raw_url + "/nothing-here").health()) is False


# --- failures are raised, for step_executor.py to normalise (P13, FR-ADP-008) ---

def test_timeout_raises_timeout_exception(raw_url):
    with pytest.raises(httpx.TimeoutException):
        _run(RestAdapter(raw_url + "/execute/slow", timeout=0.3).execute(REQUEST))


def test_unreachable_agent_raises_connect_error():
    with pytest.raises(httpx.ConnectError):
        _run(RestAdapter(_free_port_url()).execute(REQUEST))


def test_5xx_raises_http_status_error(raw_url):
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _run(RestAdapter(raw_url + "/execute/down").execute(REQUEST))
    assert exc.value.response.status_code == 503


@pytest.mark.parametrize("path", ["/execute/html", "/execute/list", "/execute/nostatus"])
def test_malformed_reply_raises_value_error(raw_url, path):
    with pytest.raises(ValueError):
        _run(RestAdapter(raw_url + path).execute(REQUEST))


def test_execute_is_bounded_by_default():
    assert 0 < RestAdapter("http://agent").timeout <= 60


# --- FR-ADP-006 / FR-ADP-007: framework isolation ------------------------------

FRAMEWORK_IMPORT = re.compile(r"^\s*(from|import)\s+(langchain\w*|crewai\w*|mcp)\b", re.MULTILINE)


def test_no_framework_import_outside_adapters():
    offenders = [
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*.py")
        if "adapters" not in path.relative_to(BACKEND).parts[:1]
        and FRAMEWORK_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_backend_never_imports_agent_wrappers():
    pattern = re.compile(r"^\s*(from|import)\s+(agent_wrappers|python_wrapper|demo_agents)\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.relative_to(BACKEND).parts[:1] and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
