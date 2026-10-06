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
