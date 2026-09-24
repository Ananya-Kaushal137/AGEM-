"""Tests for the orchestration layer (FR-DEP-004). LLM calls are mocked (FR-DEP-006).

For now this covers the step state machine only — `can_transition` is the whole
of it (Architecture §11.5, FR-ORC-009). The rest of `orchestrator/` arrives in
Prompts 5-7.
"""

import itertools

import pytest

from app.models.enums import TERMINAL_STEP_STATUSES, StepStatus, can_transition

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
