"""Tests for the orchestration layer (FR-DEP-004). LLM calls are mocked (FR-DEP-006).

Covers the step state machine (Architecture §11.5, FR-ORC-009) and the Master
Agent's diagnosis with the single LLM wrapper (Prompt 6). The rest of
`orchestrator/` arrives in Prompts 8-9.
"""

import asyncio
import itertools
import logging
import re
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.core import llm
from app.core.config import get_settings
from app.models.enums import TERMINAL_STEP_STATUSES, ErrorType, StepStatus, can_transition
from orchestrator import master_agent
from orchestrator.master_agent import DiagnosisReply, diagnose_failure

# The six edges drawn in Architecture §15.1, transcribed by hand rather than
# imported, so a change to the source set cannot silently pass this test.
LEGAL = {
    (StepStatus.PENDING, StepStatus.RUNNING),
    (StepStatus.RUNNING, StepStatus.SUCCEEDED),
    (StepStatus.RUNNING, StepStatus.FAILED),
    (StepStatus.RUNNING, StepStatus.PAUSED),
    (StepStatus.PAUSED, StepStatus.RUNNING),  # resumed
    (StepStatus.PAUSED, StepStatus.FAILED),  # 3 attempts exhausted
}

ALL_PAIRS = list(itertools.product(StepStatus, StepStatus))
ILLEGAL = [pair for pair in ALL_PAIRS if pair not in LEGAL]


def test_step_status_has_exactly_five_values():
    """FR-ORC-008: exactly 5 states, no more."""
    assert len(list(StepStatus)) == 5


def test_recovery_stage_has_exactly_eight_values():
    """Architecture §15.2: the recovery sub-state has 8 values."""
    from app.models.enums import RecoveryStage

    assert len(list(RecoveryStage)) == 8


@pytest.mark.parametrize(
    "old,new", sorted(LEGAL, key=lambda p: (p[0].value, p[1].value))
)
def test_legal_transitions_are_allowed(old, new):
    assert can_transition(old, new) is True


@pytest.mark.parametrize("old,new", ILLEGAL, ids=lambda s: s.value)
def test_every_illegal_transition_is_rejected(old, new):
    """The done-when: every one of the 19 illegal pairs is refused."""
    assert can_transition(old, new) is False


def test_the_matrix_is_exhaustive():
    """All 25 ordered pairs are covered, so nothing is silently untested."""
    assert len(ALL_PAIRS) == 25
    assert len(LEGAL) + len(ILLEGAL) == 25


@pytest.mark.parametrize(
    "terminal", sorted(TERMINAL_STEP_STATUSES, key=lambda s: s.value)
)
@pytest.mark.parametrize("target", list(StepStatus))
def test_terminal_states_never_transition(terminal, target):
    """SUCCEEDED and FAILED are final — once reached, a step can't change."""
    assert can_transition(terminal, target) is False


def test_no_self_transitions():
    """A status may not transition to itself; a no-op write is a bug, not a move."""
    for status in StepStatus:
        assert can_transition(status, status) is False


def test_pending_cannot_skip_running():
    """PENDING has exactly one exit: RUNNING."""
    assert can_transition(StepStatus.PENDING, StepStatus.SUCCEEDED) is False
    assert can_transition(StepStatus.PENDING, StepStatus.FAILED) is False
    assert can_transition(StepStatus.PENDING, StepStatus.PAUSED) is False


def test_paused_resumes_through_running_only():
    """A paused step cannot jump straight to SUCCEEDED; it resumes into RUNNING."""
    assert can_transition(StepStatus.PAUSED, StepStatus.SUCCEEDED) is False
    assert can_transition(StepStatus.PAUSED, StepStatus.RUNNING) is True


def test_accepts_plain_strings():
    """Values round-trip from the DB as strings; the guard must still hold."""
    assert can_transition("PENDING", "RUNNING") is True
    assert can_transition("SUCCEEDED", "RUNNING") is False


# --- Master Agent: rules-first diagnosis + the single LLM wrapper (Prompt 6) ---
#
# FR-DIAG-003/004/005/006/007/009/014. The LLM is always mocked (FR-DEP-006): either
# `llm.call_json` as a whole, or `llm._complete` — the one function that talks to a
# provider — so the wrapper's validation, retry and fail-safe run for real.

STEP_ID = uuid.uuid4()
OUTCOMES = {"NORMAL_ERROR", "CAPABILITY_GAP"}

# Architecture §23.1 / FR-DIAG-014, typed out by hand.
RULES = {
    "MISSING_CAPABILITY": "CAPABILITY_GAP",
    "TIMEOUT": "NORMAL_ERROR",
    "CONNECTION_ERROR": "NORMAL_ERROR",
    "HTTP_5XX": "NORMAL_ERROR",
    "INVALID_JSON": "NORMAL_ERROR",
}


def failure(error_type, raw_error="boom", **extra) -> dict:
    """The normalised FR-DIAG-002 shape."""
    return {"status": "FAILED", "error_type": error_type, "raw_error": raw_error, **extra}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def llm_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")
    monkeypatch.delenv("LLM_MODEL_CHEAP", raising=False)
    monkeypatch.delenv("LLM_MODEL_STRONG", raising=False)
    monkeypatch.delenv("LLM_LOG_PAYLOADS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mocked_wrapper(monkeypatch):
    mock = AsyncMock(return_value=DiagnosisReply(diagnosis="CAPABILITY_GAP", capability="calculate_irr",
                                                 reason="no IRR tool"))
    monkeypatch.setattr(llm, "call_json", mock)
    return mock


def fake_complete(monkeypatch, replies):
    """Replace the provider call with canned reply texts; returns the requests it received."""
    requests = []
    replies = iter(replies)

    async def _complete(*, system, prompt, model):
        requests.append({"system": system, "prompt": prompt, "model": model})
        return next(replies)

    monkeypatch.setattr(llm, "_complete", _complete)
    return requests


def test_error_type_constants_match_architecture():
    assert {e.value for e in ErrorType} == set(RULES) | {"AGENT_ERROR"}


@pytest.mark.parametrize("error_type,expected", sorted(RULES.items()))
def test_each_rule_classifies_without_the_llm(mocked_wrapper, error_type, expected):
    """The done-when: MISSING_CAPABILITY and TIMEOUT (and the other rules) never reach the LLM."""
    assert run(diagnose_failure(STEP_ID, failure(error_type))) == expected
    mocked_wrapper.assert_not_called()


def test_rules_accept_the_enum_too(mocked_wrapper):
    assert run(diagnose_failure(STEP_ID, failure(ErrorType.MISSING_CAPABILITY))) == "CAPABILITY_GAP"
    mocked_wrapper.assert_not_called()


def test_unmatched_error_makes_exactly_one_wrapper_call(mocked_wrapper):
    """FR-DIAG-004: at most one diagnosis call per failure — on the cheap tier, 20 s limit."""
    result = run(diagnose_failure(STEP_ID, failure("AGENT_ERROR", "ValueError: cannot compute IRR")))
    assert result == "CAPABILITY_GAP"
    mocked_wrapper.assert_awaited_once()
    kwargs = mocked_wrapper.call_args.kwargs
    assert kwargs["tier"] == "cheap"
    assert kwargs["timeout"] == 20
    assert kwargs["schema"] is DiagnosisReply
    assert kwargs["fallback"].diagnosis == "NORMAL_ERROR"
    assert "cannot compute IRR" in kwargs["prompt"]


@pytest.mark.parametrize("error", [
    {},
    {"status": "FAILED"},
    {"status": "FAILED", "error_type": "missing_capability", "raw_error": "lower case is not a match"},
    {"status": "FAILED", "error_type": None, "raw_error": None},
], ids=["empty", "no-type", "wrong-case", "nulls"])
def test_unknown_or_malformed_errors_go_to_the_llm(mocked_wrapper, error):
    """Only the exact strings match a rule; everything else is the LLM's job."""
    assert run(diagnose_failure(STEP_ID, error)) in OUTCOMES
    mocked_wrapper.assert_awaited_once()


def test_forced_validation_failure_three_times_returns_normal_error(llm_key, monkeypatch):
    """The done-when: 3 invalid replies on an unmatched error → NORMAL_ERROR, no exception."""
    requests = fake_complete(monkeypatch, ['{"diagnosis": "MAYBE", "reason": "x"}', "not json", "{}"])
    assert run(diagnose_failure(STEP_ID, failure("AGENT_ERROR"))) == "NORMAL_ERROR"
    assert len(requests) == 3  # 1 call + 2 retries, never more (FR-DIAG-006)
    # Each retry is the same call with the validation error appended.
    assert requests[1]["prompt"].startswith(requests[0]["prompt"])
    assert "rejected" in requests[1]["prompt"] and "diagnosis" in requests[1]["prompt"]
    assert requests[1]["system"] == requests[0]["system"]


def test_valid_reply_on_a_retry_is_used(llm_key, monkeypatch):
    requests = fake_complete(monkeypatch, [
        "Sure! It is a gap.",
        '{"diagnosis": "CAPABILITY_GAP", "capability": "calculate_irr", "reason": "no tool"}',
    ])
    assert run(diagnose_failure(STEP_ID, failure("AGENT_ERROR"))) == "CAPABILITY_GAP"
    assert len(requests) == 2


def test_cheap_model_is_used_for_diagnosis(llm_key, monkeypatch):
    """FR-DIAG-008."""
    requests = fake_complete(monkeypatch, ['{"diagnosis": "NORMAL_ERROR", "reason": "bad input"}'])
    run(diagnose_failure(STEP_ID, failure("AGENT_ERROR")))
    assert requests[0]["model"] == llm.DEFAULT_MODELS["anthropic"]["cheap"]
    assert llm.model_for("cheap") != llm.model_for("strong")


def test_raw_error_is_framed_as_data_and_cut(llm_key, monkeypatch):
    requests = fake_complete(monkeypatch, ['{"diagnosis": "NORMAL_ERROR", "reason": "r"}'])
    run(diagnose_failure(STEP_ID, failure("AGENT_ERROR", "x" * 10_000)))
    assert "<error>" in requests[0]["prompt"]
    assert len(requests[0]["prompt"]) < 5_000


def test_diagnosis_survives_a_provider_timeout(llm_key, monkeypatch):
    async def slow(**_):
        await asyncio.sleep(5)

    monkeypatch.setattr(llm, "_complete", slow)
    monkeypatch.setattr(llm, "DEFAULT_TIMEOUT_SECONDS", 0.1)
    assert run(diagnose_failure(STEP_ID, failure("AGENT_ERROR"))) == "NORMAL_ERROR"


# --- The wrapper on its own: general, the caller supplies schema and fallback ---


class Notes(BaseModel):
    definition: str
    examples: list[int]


EMPTY_NOTES = Notes(definition="", examples=[])


def test_wrapper_returns_the_callers_schema(llm_key, monkeypatch):
    fake_complete(monkeypatch, ['```json\n{"definition": "A = P(1+r)^t", "examples": [1, 2]}\n```'])
    notes = run(llm.call_json(system="s", prompt="p", schema=Notes, fallback=EMPTY_NOTES, tier="strong"))
    assert notes == Notes(definition="A = P(1+r)^t", examples=[1, 2])


def test_wrapper_asks_for_json_matching_the_schema(llm_key, monkeypatch):
    requests = fake_complete(monkeypatch, ['{"definition": "d", "examples": []}'])
    run(llm.call_json(system="be brief", prompt="p", schema=Notes, fallback=EMPTY_NOTES))
    assert requests[0]["system"].startswith("be brief")
    assert "JSON" in requests[0]["system"] and '"examples"' in requests[0]["system"]


def test_wrapper_times_out_to_the_fallback(llm_key, monkeypatch):
    """FR-DIAG-007: never hang — the time limit covers the whole call."""
    async def slow(**_):
        await asyncio.sleep(5)
        return "{}"

    monkeypatch.setattr(llm, "_complete", slow)
    started = time.monotonic()
    result = run(llm.call_json(system="s", prompt="p", schema=Notes, fallback=EMPTY_NOTES, timeout=0.2))
    assert result is EMPTY_NOTES
    assert time.monotonic() - started < 2


def test_wrapper_provider_error_returns_the_fallback(llm_key, monkeypatch):
    async def broken(**_):
        raise ConnectionError("provider down")

    monkeypatch.setattr(llm, "_complete", broken)
    assert run(llm.call_json(system="s", prompt="p", schema=Notes, fallback=EMPTY_NOTES)) is EMPTY_NOTES


def _logged(caplog) -> str:
    return " ".join(str(getattr(r, "agem", "")) + r.getMessage() for r in caplog.records)


def test_payloads_are_not_logged_by_default(llm_key, monkeypatch, caplog):
    """FR-AUTH-006: metadata only — the agent's error text never reaches the logs."""
    fake_complete(monkeypatch, ['{"diagnosis": "MAYBE"}', '{"diagnosis": "NORMAL_ERROR", "reason": "r"}'])
    with caplog.at_level(logging.DEBUG):
        run(diagnose_failure(STEP_ID, failure("AGENT_ERROR", "secret-customer-data-123")))
    logged = _logged(caplog)
    assert "secret-customer-data-123" not in logged
    assert "VALID" in logged and "INVALID" in logged  # the audit trail is still there


def test_payloads_are_logged_behind_the_debug_flag(llm_key, monkeypatch, caplog):
    monkeypatch.setenv("LLM_LOG_PAYLOADS", "true")
    get_settings.cache_clear()
    fake_complete(monkeypatch, ['{"diagnosis": "NORMAL_ERROR", "reason": "r"}'])
    with caplog.at_level(logging.DEBUG):
        run(diagnose_failure(STEP_ID, failure("AGENT_ERROR", "secret-customer-data-123")))
    assert "secret-customer-data-123" in _logged(caplog)


def test_wrapper_without_api_key_never_calls_the_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        requests = fake_complete(monkeypatch, [])
        assert run(llm.call_json(system="s", prompt="p", schema=Notes, fallback=EMPTY_NOTES)) is EMPTY_NOTES
        assert requests == []
    finally:
        get_settings.cache_clear()


def test_no_module_calls_an_llm_api_directly():
    """FR-DIAG-009: provider SDKs are imported in app/core/llm.py and nowhere else."""
    backend = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"^\s*(import|from)\s+(anthropic|openai)\b", re.MULTILINE)
    offenders = [
        path.relative_to(backend).as_posix()
        for path in backend.rglob("*.py")
        if "tests" not in path.parts and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == ["app/core/llm.py"]


def test_master_agent_is_a_module_not_a_service():
    """ADR-001: diagnosis is a function inside the backend process, no HTTP hop."""
    source = Path(master_agent.__file__).read_text(encoding="utf-8")
    assert "httpx" not in source and "FastAPI" not in source


# --- Orchestrator + StepExecutor (Prompt 8: FR-ORC-001…014, FR-ADP-005, BR-13) ---
#
# Agents are faked at the adapter, so these run with no network. The end-to-end
# run through the API and real HTTP agents is in test_api.py.

import httpx  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from adapters.base_adapter import BaseAdapter  # noqa: E402
from app.api.executions import create_execution  # noqa: E402
from app.models import Agent, Base, Execution, ExecutionStep, User, Workflow, WorkflowAgent  # noqa: E402
from app.models.enums import AgentFramework, ExecutionStatus, RecoveryStage, WorkflowStatus  # noqa: E402
from app.schemas.execution import ExecutionCreate  # noqa: E402
from orchestrator import orchestrator as orchestrator_module  # noqa: E402
from orchestrator import step_executor  # noqa: E402
from orchestrator.checkpoint_manager import CheckpointManager  # noqa: E402
from orchestrator.orchestrator import MAX_ATTEMPTS, IllegalTransition, Orchestrator, rollup  # noqa: E402
from orchestrator.step_executor import StepExecutor, normalise_exception, normalise_reply  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):
    return "JSON"


_REQUEST = httpx.Request("POST", "http://agent/execute")


def _status_error(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError("bad", request=_REQUEST, response=httpx.Response(code, text="boom", request=_REQUEST))


@pytest.mark.parametrize("exc,expected", [
    (httpx.ReadTimeout("slow", request=_REQUEST), "TIMEOUT"),
    (asyncio.TimeoutError(), "TIMEOUT"),
    (httpx.ConnectTimeout("no route", request=_REQUEST), "CONNECTION_ERROR"),
    (httpx.ConnectError("refused", request=_REQUEST), "CONNECTION_ERROR"),
    (httpx.RemoteProtocolError("hung up", request=_REQUEST), "CONNECTION_ERROR"),
    (_status_error(500), "HTTP_5XX"),
    (_status_error(503), "HTTP_5XX"),
    (_status_error(404), "AGENT_ERROR"),
    (ValueError("Expecting value: line 1 column 1"), "INVALID_JSON"),
    (KeyError("revenue"), "AGENT_ERROR"),
    (RuntimeError("anything else"), "AGENT_ERROR"),
], ids=["read-timeout", "asyncio-timeout", "connect-timeout", "connect-error", "protocol-error",
        "500", "503", "404", "bad-json", "key-error", "other"])
def test_every_exception_becomes_one_error_type(exc, expected):
    """FR-ADP-005 / FR-DIAG-002: one shape, one of the six codes."""
    result = normalise_exception(exc)
    assert set(result) == {"status", "error_type", "raw_error"}
    assert result["status"] == "FAILED" and result["error_type"] == expected
    assert isinstance(result["raw_error"], str) and result["raw_error"]


@pytest.mark.parametrize("reply,expected", [
    ({"status": "SUCCEEDED", "output": {"revenue": 1}},
     {"status": "SUCCEEDED", "output": {"revenue": 1}}),
    ({"status": "SUCCEEDED", "output": [1, 2]},
     {"status": "FAILED", "error_type": "INVALID_JSON"}),
    ({"status": "SUCCEEDED"},
     {"status": "FAILED", "error_type": "INVALID_JSON"}),
    ({"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_cagr"},
     {"status": "FAILED", "error_type": "MISSING_CAPABILITY", "raw_error": "MISSING_CAPABILITY",
      "capability": "calculate_cagr"}),
    ({"status": "FAILED", "error": "MISSING_CAPABILITY"},
     {"status": "FAILED", "error_type": "MISSING_CAPABILITY", "raw_error": "MISSING_CAPABILITY"}),
    ({"status": "FAILED", "error": "company not found"},
     {"status": "FAILED", "error_type": "AGENT_ERROR", "raw_error": "company not found"}),
    ({"status": "FAILED", "error": "TIMEOUT"},  # the agent's own word, not something AGEM observed
     {"status": "FAILED", "error_type": "AGENT_ERROR", "raw_error": "TIMEOUT"}),
    ({"status": "FAILED"},
     {"status": "FAILED", "error_type": "AGENT_ERROR"}),
], ids=["ok", "output-list", "no-output", "gap-named", "gap-unnamed", "agent-error", "agent-says-timeout", "bare"])
def test_agent_replies_are_normalised(reply, expected):
    result = normalise_reply(reply)
    assert {k: result[k] for k in expected} == expected
    if result["status"] == "FAILED":
        assert result["error_type"] in {e.value for e in ErrorType}
        assert ("capability" in result) == ("capability" in expected)


def test_error_types_are_the_master_agent_rule_strings():
    """BR-13: what step_executor emits is exactly what the diagnosis rules match."""
    emitted = {normalise_exception(e)["error_type"] for e in (
        httpx.ReadTimeout("t", request=_REQUEST), httpx.ConnectError("c", request=_REQUEST),
        _status_error(502), ValueError("v"))}
    emitted.add(normalise_reply({"status": "FAILED", "error": "MISSING_CAPABILITY"})["error_type"])
    assert emitted == master_agent.RULE_GAP | master_agent.RULE_NORMAL


def test_long_errors_are_cut():
    assert len(normalise_exception(RuntimeError("x" * 50_000))["raw_error"]) <= step_executor.RAW_ERROR_LIMIT


@pytest.mark.parametrize("statuses,expected", [
    (["PENDING", "PENDING"], "PENDING"),
    (["RUNNING", "PENDING"], "RUNNING"),
    (["SUCCEEDED", "PENDING"], "RUNNING"),
    (["SUCCEEDED", "SUCCEEDED"], "SUCCEEDED"),
    (["SUCCEEDED", "FAILED", "PENDING"], "FAILED"),
    (["SUCCEEDED", "PAUSED", "PENDING"], "PAUSED"),
    (["PAUSED", "FAILED"], "PAUSED"),
])
def test_execution_status_rolls_up_from_steps(statuses, expected):
    """Architecture §15.3 / FR-ORC-010."""
    assert rollup(StepStatus(s) for s in statuses) == ExecutionStatus(expected)


# --- runs against SQLite with fake adapters ---


class FakeAdapter(BaseAdapter):
    """Plays one agent: `behaviour(input) -> reply`, or raises. Records every call, in order."""

    def __init__(self, name, behaviour, calls, delay=0.0):
        self.name, self.behaviour, self.calls, self.delay = name, behaviour, calls, delay

    async def execute(self, input):
        step_input = input["input"]  # the request is {task, input, context}
        self.calls.append((self.name, "start", step_input))
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append((self.name, "end", step_input))
        return self.behaviour(step_input)


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Harness:
    """Builds a workflow of fake agents and runs it through the real Orchestrator."""

    def __init__(self, sessions):
        self.sessions, self.calls, self.behaviours, self.delays = sessions, [], {}, {}
        with sessions() as db:
            user = User(email="owner@agem.local", api_key_hash="x")
            db.add(user)
            db.commit()
            self.user_id = user.user_id

    def agent(self, name, behaviour, delay=0.0):
        with self.sessions() as db:
            agent = Agent(user_id=self.user_id, name=name, framework=AgentFramework.REST, endpoint=f"http://{name}")
            db.add(agent)
            db.commit()
        self.behaviours[agent.agent_id], self.delays[agent.agent_id] = behaviour, delay
        return agent.agent_id

    def make_adapter(self, agent):
        return FakeAdapter(agent.name, self.behaviours[agent.agent_id], self.calls, self.delays[agent.agent_id])

    def workflow(self, steps):
        """steps: [(key, agent_id, depends_on, {target: (from, field)})], already in step_order."""
        ids = {key: uuid.uuid4() for key, *_ in steps}
        with self.sessions() as db:
            wf = Workflow(user_id=self.user_id, name="wf", status=WorkflowStatus.ACTIVE,
                          definition={"steps": [{"workflow_agent_id": str(ids[k]), "key": k} for k, *_ in steps]})
            for order, (key, agent_id, deps, mapping) in enumerate(steps, start=1):
                wf.steps.append(WorkflowAgent(
                    workflow_agent_id=ids[key], agent_id=agent_id, step_order=order,
                    depends_on=[str(ids[d]) for d in deps],
                    input_mapping={t: {"from": f if f == "input" else str(ids[f]), "field": fld, "type": "any"}
                                   for t, (f, fld) in mapping.items()}))
            db.add(wf)
            db.commit()
            return wf.workflow_id

    def start(self, workflow_id, task="Create a company investment report", input=None):
        with self.sessions() as db:
            return create_execution(db, db.get(Workflow, workflow_id),
                                    ExecutionCreate(task=task, input=input or {})).execution_id

    def run(self, workflow_id, **kwargs):
        execution_id = self.start(workflow_id, **kwargs)
        self.orchestrate(execution_id)
        return execution_id

    def orchestrate(self, execution_id):
        executor = StepExecutor(self.sessions, make_adapter=self.make_adapter)
        asyncio.run(Orchestrator(self.sessions, executor).run_execution(execution_id))

    def load(self, execution_id):
        return self.sessions().get(Execution, execution_id)

    def order(self):
        return [name for name, phase, _ in self.calls if phase == "start"]


@pytest.fixture
def h(sessions):
    return Harness(sessions)


def ok(**output):
    return lambda _input: {"status": "SUCCEEDED", "output": output}


def test_two_agents_run_end_to_end_in_declared_order(h):
    """The done-when: a 2-agent workflow runs end to end in declared order (US-04)."""
    research = h.agent("Research", ok(company="Tesla", revenue=96.77, profit=14.97))
    writer = h.agent("Writer", lambda i: {"status": "SUCCEEDED", "output": {"report": f"{i['company']}: {i['revenue']}"}})
    wf = h.workflow([
        ("research", research, [], {"company": ("input", "company")}),
        ("writer", writer, ["research"], {"company": ("research", "company"), "revenue": ("research", "revenue")}),
    ])
    execution = h.load(h.run(wf, input={"company": "Tesla"}))

    assert h.order() == ["Research", "Writer"]
    assert execution.status == ExecutionStatus.SUCCEEDED
    assert execution.started_at and execution.finished_at
    first, second = execution.steps
    assert first.input == {"company": "Tesla"}
    assert second.input == {"company": "Tesla", "revenue": 96.77}  # FR-ORC-005: no manual wiring
    assert [s.status for s in execution.steps] == [StepStatus.SUCCEEDED] * 2
    assert all(s.attempts == 1 and s.recovery_stage == RecoveryStage.NONE for s in execution.steps)
    assert execution.final_output == {"report": "Tesla: 96.77"}


def test_every_agent_gets_the_task_input_and_empty_context(h, monkeypatch):
    requests = []

    class Recorder(BaseAdapter):
        async def execute(self, input):
            requests.append(input)
            return {"status": "SUCCEEDED", "output": {}}

    h.make_adapter = lambda agent: Recorder()
    h.run(h.workflow([("a", h.agent("A", None), [], {"company": ("input", "company")})]),
          task="Summarise Tesla", input={"company": "Tesla", "unmapped": 1})
    assert requests == [{"task": "Summarise Tesla", "input": {"company": "Tesla"}, "context": {}}]


def test_steps_follow_the_stored_step_order(h):
    """FR-ORC-003: READY steps start in step_order; the Orchestrator never re-sorts."""
    a, b, c = (h.agent(n, ok(x=1)) for n in "ABC")
    h.run(h.workflow([("c", c, [], {}), ("a", a, [], {}), ("b", b, [], {})]))
    assert h.order() == ["C", "A", "B"]


def test_siblings_run_at_the_same_time(h):
    """FR-ORC-004: Research → (Finance ‖ Market) → Writer; the two middle steps overlap."""
    research = h.agent("Research", ok(revenue=10))
    finance = h.agent("Finance", ok(profit=1), delay=0.3)
    market = h.agent("Market", ok(share=0.2), delay=0.3)
    writer = h.agent("Writer", lambda i: {"status": "SUCCEEDED", "output": {"report": i}})
    wf = h.workflow([
        ("research", research, [], {}),
        ("finance", finance, ["research"], {"revenue": ("research", "revenue")}),
        ("market", market, ["research"], {}),
        ("writer", writer, ["finance", "market"], {"profit": ("finance", "profit"), "share": ("market", "share")}),
    ])
    started = time.monotonic()
    execution = h.load(h.run(wf))
    assert time.monotonic() - started < 0.55  # one after the other would take ≥ 0.6 s
    phases = [(n, p) for n, p, _ in h.calls]
    assert phases[2:6] == [("Finance", "start"), ("Market", "start"), ("Finance", "end"), ("Market", "end")]
    assert phases[-1] == ("Writer", "end")
    assert execution.status == ExecutionStatus.SUCCEEDED
    assert execution.final_output == {"report": {"profit": 1, "share": 0.2}}


def test_several_end_steps_give_a_keyed_final_output(h):
    a, b, c = h.agent("A", ok(x=1)), h.agent("B", ok(y=2)), h.agent("C", ok(z=3))
    execution = h.load(h.run(h.workflow([("a", a, [], {}), ("b", b, ["a"], {}), ("c", c, ["a"], {})])))
    assert execution.final_output == {"b": {"y": 2}, "c": {"z": 3}}


def test_a_failed_step_pauses_with_the_normalised_error(h):
    """Prompt 9 (FR-CKP-004): only the failed step pauses, downstream stays PENDING, the run is PAUSED."""
    research = h.agent("Research", ok(revenue=1))
    finance = h.agent("Finance", lambda i: {"status": "FAILED", "error": "MISSING_CAPABILITY",
                                            "capability": "calculate_compound_interest"})
    writer = h.agent("Writer", ok(report="r"))
    wf = h.workflow([("research", research, [], {}), ("finance", finance, ["research"], {}),
                     ("writer", writer, ["finance"], {})])
    execution = h.load(h.run(wf))

    assert [s.status for s in execution.steps] == [StepStatus.SUCCEEDED, StepStatus.PAUSED, StepStatus.PENDING]
    assert execution.steps[1].error == {"status": "FAILED", "error_type": "MISSING_CAPABILITY",
                                        "raw_error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest"}
    assert execution.status == ExecutionStatus.PAUSED and execution.finished_at is None
    assert execution.final_output is None
    assert h.order() == ["Research", "Finance"]


def test_an_adapter_exception_is_normalised_too(h):
    def boom(_):
        raise httpx.ConnectError("connection refused", request=_REQUEST)

    execution = h.load(h.run(h.workflow([("a", h.agent("A", boom), [], {})])))
    assert execution.steps[0].error["error_type"] == "CONNECTION_ERROR"
    assert execution.status == ExecutionStatus.PAUSED


def test_a_failed_sibling_lets_the_other_finish(h):
    root = h.agent("Root", ok(x=1))
    bad = h.agent("Bad", lambda i: {"status": "FAILED", "error": "nope"})
    good = h.agent("Good", ok(y=2), delay=0.1)
    end = h.agent("End", ok(z=3))
    execution = h.load(h.run(h.workflow([("root", root, [], {}), ("bad", bad, ["root"], {}),
                                         ("good", good, ["root"], {}), ("end", end, ["bad", "good"], {})])))
    assert [s.status for s in execution.steps] == [StepStatus.SUCCEEDED, StepStatus.PAUSED,
                                                   StepStatus.SUCCEEDED, StepStatus.PENDING]


def test_a_missing_upstream_field_fails_before_calling_the_agent(h):
    a, b = h.agent("A", ok(revenue=1)), h.agent("B", ok(done=True))
    execution = h.load(h.run(h.workflow([("a", a, [], {}), ("b", b, ["a"], {"profit": ("a", "profit")})])))
    step = execution.steps[1]
    assert step.status == StepStatus.PAUSED and step.error["error_type"] == "AGENT_ERROR"
    assert '"profit"' in step.error["raw_error"]
    assert h.order() == ["A"]


def test_success_writes_a_checkpoint_in_the_same_transaction(h):
    """FR-ORC-013: one checkpoint per successful step, holding the §11.3 contents."""
    a, b = h.agent("A", ok(revenue=5)), h.agent("B", ok(report="r"))
    execution_id = h.run(h.workflow([("a", a, [], {}), ("b", b, ["a"], {"revenue": ("a", "revenue")})]))
    with h.sessions() as db:
        steps = db.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == execution_id)
                           .order_by(ExecutionStep.step_order)).all()
        checkpoints = [[c.state for c in s.checkpoints] for s in steps]
    assert [len(c) for c in checkpoints] == [1, 1]
    last = checkpoints[1][0]
    assert set(last) == {"current_step", "completed_steps", "outputs", "pending_inputs", "workflow_state", "recovery"}
    assert last["outputs"] == {str(steps[0].step_id): {"revenue": 5}, str(steps[1].step_id): {"report": "r"}}
    assert last["workflow_state"]["execution_status"] == "SUCCEEDED"
    assert last["recovery"] == {"recovery_stage": "NONE", "attempts": 1}


def test_a_failed_commit_leaves_no_half_written_success(h, monkeypatch):
    """FR-ORC-013: if the checkpoint cannot be written, the SUCCEEDED status is not written either."""
    def broken(step, plan):
        raise RuntimeError("database write failed")

    a = h.agent("A", ok(x=1))
    monkeypatch.setattr(orchestrator_module, "_checkpoint_state", broken)
    execution = h.load(h.run(h.workflow([("a", a, [], {})])))
    step = execution.steps[0]
    assert step.status == StepStatus.RUNNING and step.output is None and step.checkpoints == []


def test_input_is_read_from_the_checkpoint(h, monkeypatch):
    """FR-ORC-005: the upstream value comes from its checkpoint, not from the step row."""
    a, b = h.agent("A", ok(revenue=5)), h.agent("B", ok(done=True))
    original = CheckpointManager.output_of
    monkeypatch.setattr(CheckpointManager, "output_of",
                        lambda self, sid: {**original(self, sid), "revenue": "from-checkpoint"})
    execution = h.load(h.run(h.workflow([("a", a, [], {}), ("b", b, ["a"], {"revenue": ("a", "revenue")})])))
    assert execution.steps[1].input == {"revenue": "from-checkpoint"}


def test_running_an_execution_twice_does_not_call_agents_twice(h):
    execution_id = h.run(h.workflow([("a", h.agent("A", ok(x=1)), [], {})]))
    h.orchestrate(execution_id)
    assert h.order() == ["A"]


def test_an_unknown_execution_is_ignored(h):
    h.orchestrate(uuid.uuid4())  # a background task has nobody to raise to
    assert h.calls == []


def test_an_illegal_transition_is_refused(h):
    """FR-ORC-009: every status write goes through can_transition."""
    execution_id = h.run(h.workflow([("a", h.agent("A", ok(x=1)), [], {})]))
    with h.sessions() as db:
        step = db.get(Execution, execution_id).steps[0]
        plan = orchestrator_module._plan_for(step.execution)
        with pytest.raises(IllegalTransition):
            orchestrator_module._move(step, StepStatus.RUNNING, plan)


def test_a_framework_without_an_adapter_is_a_normalised_failure(h):
    a = h.agent("A", ok(x=1))
    with h.sessions() as db:
        db.get(Agent, a).framework = AgentFramework.LANGCHAIN
        db.commit()
    execution_id = h.start(h.workflow([("a", a, [], {})]))
    asyncio.run(Orchestrator(h.sessions).run_execution(execution_id))  # the real adapter_for
    step = h.load(execution_id).steps[0]
    assert step.status == StepStatus.PAUSED and step.error["error_type"] == "AGENT_ERROR"
    assert "langchain" in step.error["raw_error"]


def test_bookkeeping_stays_under_300ms_per_step(h, caplog):
    """FR-ORC-014: orchestration overhead, excluding agent time, is < 300 ms per step."""
    keys = list("abcde")
    agents = [h.agent(k.upper(), ok(v=1)) for k in keys]
    wf = h.workflow([(k, a, keys[i - 1:i], {"v": (keys[i - 1], "v")} if i else {})
                     for i, (k, a) in enumerate(zip(keys, agents))])
    with caplog.at_level(logging.INFO, logger="agem.orchestrator"):
        started = time.perf_counter()
        execution = h.load(h.run(wf))
        total = time.perf_counter() - started
    assert execution.status == ExecutionStatus.SUCCEEDED
    overheads = [r.agem["overhead_ms"] for r in caplog.records if "overhead_ms" in getattr(r, "agem", {})]
    assert len(overheads) == 5 and max(overheads) < 300
    assert total / 5 < 0.3  # the agents are instant here, so this is all bookkeeping


def test_orchestrator_has_no_framework_code():
    """P6: HTTP and framework knowledge stays in adapters/ and step_executor.py."""
    source = Path(orchestrator_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(httpx|adapters)\b", source, re.MULTILINE)



# --- Checkpoints, pause and resume (Prompt 9: FR-CKP-001…008, US-06) ---------


class Flaky:
    """Fails with MISSING_CAPABILITY until `fixed` is set, then succeeds with `output`."""

    def __init__(self, **output):
        self.fixed, self.output = False, output

    def __call__(self, _input):
        if self.fixed:
            return {"status": "SUCCEEDED", "output": self.output}
        return {"status": "FAILED", "error": "MISSING_CAPABILITY", "capability": "calculate_compound_interest"}


def _paused_report(h):
    """Research → Finance (pauses) → Writer, run once. Returns (execution_id, finance step id, flaky)."""
    finance = Flaky(projected_value=1628.89)
    research = h.agent("Research", ok(company="Tesla", revenue=96.77))
    fin = h.agent("Finance", finance)
    writer = h.agent("Writer", lambda i: {"status": "SUCCEEDED", "output": {"report": f"{i['company']}: {i['value']}"}})
    wf = h.workflow([
        ("research", research, [], {"company": ("input", "company")}),
        ("finance", fin, ["research"], {"company": ("research", "company"), "revenue": ("research", "revenue")}),
        ("writer", writer, ["finance"], {"company": ("research", "company"), "value": ("finance", "projected_value")}),
    ])
    execution_id = h.run(wf, input={"company": "Tesla"})
    return execution_id, h.load(execution_id).steps[1].step_id, finance


def _resume(h, step_id, tool_results=None):
    executor = StepExecutor(h.sessions, make_adapter=h.make_adapter)
    asyncio.run(Orchestrator(h.sessions, executor).resume_step(step_id, tool_results))


def _checkpoints(h, execution_id):
    with h.sessions() as db:
        steps = db.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == execution_id)
                           .order_by(ExecutionStep.step_order)).all()
        return [[c.state for c in sorted(s.checkpoints, key=lambda c: c.created_at)] for s in steps]


def test_checkpoint_manager_save_and_load(h):
    """FR-CKP-001: load returns the newest checkpoint of the execution, {} when there is none."""
    execution_id, finance_id, _ = _paused_report(h)
    with h.sessions() as db:
        state = CheckpointManager(db).load(execution_id)
        assert state["current_step"] == str(finance_id)  # the pause was the last thing written
        assert CheckpointManager(db).load(uuid.uuid4()) == {}
        with pytest.raises(ValueError):
            CheckpointManager(db).save(finance_id, {"current_step": "x"})  # FR-CKP-003 contents enforced


def test_a_checkpoint_is_written_on_success_and_on_pause(h):
    """FR-CKP-002 / 003: Research has its success checkpoint, Finance its pause checkpoint."""
    execution_id, finance_id, _ = _paused_report(h)
    research_cps, finance_cps, writer_cps = _checkpoints(h, execution_id)
    assert len(research_cps) == 1 and len(finance_cps) == 1 and writer_cps == []

    pause = finance_cps[0]
    assert set(pause) == {"current_step", "completed_steps", "outputs", "pending_inputs", "workflow_state", "recovery"}
    assert pause["workflow_state"]["step_status"][str(finance_id)] == "PAUSED"
    assert pause["workflow_state"]["execution_status"] == "PAUSED"
    assert pause["pending_inputs"] == {str(finance_id): {"company": "Tesla", "revenue": 96.77}}
    assert pause["recovery"]["attempts"] == 1
    assert pause["recovery"]["error"]["error_type"] == "MISSING_CAPABILITY"
    assert list(pause["outputs"].values()) == [{"company": "Tesla", "revenue": 96.77}]  # Research's, kept


def test_only_the_failed_step_pauses(h):
    """FR-CKP-004: upstream stays SUCCEEDED and untouched, downstream stays PENDING."""
    execution_id, _, _ = _paused_report(h)
    execution = h.load(execution_id)
    assert [s.status for s in execution.steps] == [StepStatus.SUCCEEDED, StepStatus.PAUSED, StepStatus.PENDING]
    assert execution.status == ExecutionStatus.PAUSED
    assert execution.steps[0].attempts == 1


def test_resume_continues_without_rerunning_upstream(h):
    """US-06 / FR-CKP-005 / BR-01: the done-when. Research runs once; Finance resumes; Writer then runs."""
    execution_id, finance_id, finance = _paused_report(h)
    finance.fixed = True
    _resume(h, finance_id)

    assert h.order() == ["Research", "Finance", "Finance", "Writer"]  # Research never re-run
    execution = h.load(execution_id)
    assert [s.status for s in execution.steps] == [StepStatus.SUCCEEDED] * 3
    assert execution.status == ExecutionStatus.SUCCEEDED
    assert execution.steps[1].attempts == 2
    assert execution.final_output == {"report": "Tesla: 1628.89"}
    assert [len(c) for c in _checkpoints(h, execution_id)] == [1, 2, 1]  # Finance: pause, then success


def test_resume_reads_its_input_from_the_pause_checkpoint(h):
    """FR-CKP-005: even if the step row changed, the input saved at the pause is what is sent."""
    execution_id, finance_id, finance = _paused_report(h)
    with h.sessions() as db:
        db.get(ExecutionStep, finance_id).input = {"tampered": True}
        db.commit()
    finance.fixed = True
    _resume(h, finance_id)
    finance_inputs = [i for name, phase, i in h.calls if name == "Finance" and phase == "start"]
    assert finance_inputs == [{"company": "Tesla", "revenue": 96.77}] * 2


def test_resume_sends_tool_results_as_context(h):
    """What Prompt 14 will use: the verified tool's result reaches the agent in context.tool_results."""
    requests = []
    execution_id, finance_id, finance = _paused_report(h)
    finance.fixed = True
    real = h.make_adapter

    def recording(agent):
        adapter = real(agent)
        original = adapter.execute

        async def execute(request):
            requests.append((agent.name, request["context"]))
            return await original(request)

        adapter.execute = execute
        return adapter

    h.make_adapter = recording
    _resume(h, finance_id, tool_results={"calculate_compound_interest": 1628.89})
    assert requests[0] == ("Finance", {"tool_results": {"calculate_compound_interest": 1628.89}})
    assert requests[1] == ("Writer", {})  # only the resumed step gets the tool result


def test_resuming_a_succeeded_step_is_a_no_op(h):
    """FR-CKP-006."""
    execution_id, finance_id, finance = _paused_report(h)
    finance.fixed = True
    _resume(h, finance_id)
    calls = len(h.calls)
    _resume(h, finance_id)
    _resume(h, h.load(execution_id).steps[0].step_id)
    assert len(h.calls) == calls


def test_two_resumes_at_once_run_the_step_once(h):
    """FR-CKP-006: a double-click or a retried background task can't duplicate work."""
    _, finance_id, finance = _paused_report(h)
    finance.fixed = True
    executor = StepExecutor(h.sessions, make_adapter=h.make_adapter)
    orchestrator = Orchestrator(h.sessions, executor)

    async def both():
        await asyncio.gather(orchestrator.resume_step(finance_id), orchestrator.resume_step(finance_id))

    asyncio.run(both())
    assert h.order().count("Finance") == 2  # the first run, plus exactly one resume


def test_after_three_failed_attempts_the_step_is_failed(h):
    """FR-CKP-008: a PAUSED step does not pause forever."""
    assert MAX_ATTEMPTS == 3
    execution_id, finance_id, _ = _paused_report(h)  # attempt 1 fails
    _resume(h, finance_id)                            # attempt 2 fails
    assert h.load(execution_id).steps[1].status == StepStatus.PAUSED
    _resume(h, finance_id)                            # attempt 3 fails → FAILED

    execution = h.load(execution_id)
    step = execution.steps[1]
    assert step.status == StepStatus.FAILED and step.attempts == 3 and step.finished_at
    assert execution.status == ExecutionStatus.FAILED and "MISSING_CAPABILITY" in execution.error_summary
    assert len(_checkpoints(h, execution_id)[1]) == 3  # every failed attempt left its pause checkpoint
    _resume(h, finance_id)                            # a FAILED step is not resumed
    assert h.order().count("Finance") == 3 and h.order().count("Research") == 1


def test_there_is_no_public_resume_endpoint():
    """FR-CKP-007 / BR-07 / P9: resume is internal only."""
    from fastapi.routing import APIRoute

    from main import create_app

    paths = [r.path for r in create_app().routes if isinstance(r, APIRoute)]
    assert not [p for p in paths if "resume" in p.lower()]
