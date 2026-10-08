"""Runs exactly one step through its agent's adapter (Architecture §11.1, FR-ORC-012).

This is the one place every agent failure is normalised (P13, BR-13, FR-ADP-005,
FR-DIAG-002). Whatever goes wrong — an exception from the adapter or a `FAILED`
reply from the agent — what leaves `run_step` is always one of:

    {"status": "SUCCEEDED", "output": {...}}
    {"status": "FAILED", "error_type": "<ErrorType>", "raw_error": "...", "capability": "<only if named>"}

`error_type` is always an exact `ErrorType` value (Architecture §23.1), because the
diagnosis rules in `master_agent.py` match on those strings and nothing else.

The executor does not write step status; the Orchestrator does (P14).
"""

import asyncio
import json
import logging
from collections.abc import Callable
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from adapters.base_adapter import BaseAdapter
from adapters.rest_adapter import RestAdapter
from app.core.crypto import decrypt
from app.models import Agent, ExecutionStep
from app.models.enums import AgentFramework, ErrorType

__all__ = ["StepExecutor", "adapter_for", "failure", "normalise_reply", "normalise_exception", "RAW_ERROR_LIMIT"]

logger = logging.getLogger("agem.step_executor")

SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"

# Characters of error text kept on the step. Matches what master_agent.py sends to the LLM.
RAW_ERROR_LIMIT = 4000

# P6: framework knowledge lives only in adapters. The LangChain adapter arrives in Prompt 15.
ADAPTERS: dict[AgentFramework, type[BaseAdapter]] = {
    AgentFramework.REST: RestAdapter,
}


def failure(error_type: ErrorType, raw_error: str, capability: str | None = None) -> dict:
    """The FR-DIAG-002 shape. Every failure AGEM records is built here."""
    result = {"status": FAILED, "error_type": error_type.value, "raw_error": str(raw_error)[:RAW_ERROR_LIMIT]}
    if capability:
        result["capability"] = capability
    return result


def normalise_reply(reply: dict) -> dict:
    """Turn an agent's contract reply into the standard success or failure shape."""
    if reply.get("status") == SUCCEEDED:
        output = reply.get("output")
        if not isinstance(output, dict):
            return failure(ErrorType.INVALID_JSON, f'"output" must be a JSON object, got: {json.dumps(output)[:500]}')
        return {"status": SUCCEEDED, "output": output}

    # A FAILED reply. Only MISSING_CAPABILITY has a meaning in the frozen contract
    # (docs/api-spec.md); any other `error` is the agent's own and becomes AGENT_ERROR.
    error = reply.get("error")
    extra = {k: v for k, v in reply.items() if k != "status"}
    raw_error = error if isinstance(error, str) and set(extra) <= {"error", "capability"} else json.dumps(extra)
    if error == ErrorType.MISSING_CAPABILITY.value:
        capability = reply.get("capability")
        return failure(ErrorType.MISSING_CAPABILITY, raw_error,
                       capability if isinstance(capability, str) and capability.strip() else None)
    return failure(ErrorType.AGENT_ERROR, raw_error or "The agent replied FAILED without an error.")


def normalise_exception(exc: Exception) -> dict:
    """Map anything an adapter raises onto one `ErrorType` (Architecture §23.1)."""
    text = f"{type(exc).__name__}: {exc}"
    # ConnectTimeout is a TimeoutException, but the agent was never reached.
    if isinstance(exc, httpx.ConnectTimeout):
        return failure(ErrorType.CONNECTION_ERROR, text)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):  # asyncio.TimeoutError is TimeoutError
        return failure(ErrorType.TIMEOUT, text)
    if isinstance(exc, httpx.TransportError):  # ConnectError, ReadError, RemoteProtocolError, ...
        return failure(ErrorType.CONNECTION_ERROR, text)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        text = f"HTTP {code} from {exc.request.url}: {exc.response.text[:1000]}"
        return failure(ErrorType.HTTP_5XX if code >= 500 else ErrorType.AGENT_ERROR, text)
    if isinstance(exc, ValueError):  # body not JSON, or not the contract shape (rest_adapter.py)
        return failure(ErrorType.INVALID_JSON, text)
    return failure(ErrorType.AGENT_ERROR, text)


def adapter_for(agent: Agent) -> BaseAdapter:
    adapter = ADAPTERS.get(AgentFramework(agent.framework))
    if adapter is None:
        raise NotImplementedError(f'No adapter is built yet for framework "{agent.framework.value}".')
    credentials = decrypt(agent.encrypted_credentials) if agent.encrypted_credentials else None
    return adapter(agent.endpoint, credentials=credentials)


class StepExecutor:
    def __init__(self, session_factory: Callable[[], Session],
                 make_adapter: Callable[[Agent], BaseAdapter] = adapter_for) -> None:
        self._session = session_factory
        self._make_adapter = make_adapter

    async def run_step(self, step_id: UUID) -> dict:
        """Call the step's agent once with the input already stored on the step; return the normalised result.

        Never raises for anything the agent or adapter does. Raises `LookupError`
        only if the step does not exist, which is a bug in the caller.
        """
        with self._session() as db:
            step = db.get(ExecutionStep, step_id)
            if step is None:
                raise LookupError(f"ExecutionStep {step_id} does not exist.")
            request = {"task": step.execution.task, "input": step.input or {}, "context": {}}
            agent = step.agent
            log = {"execution_id": str(step.execution_id), "step_id": str(step_id), "agent_id": str(agent.agent_id)}
            try:
                adapter = self._make_adapter(agent)
            except Exception as exc:  # e.g. no adapter for the framework, undecryptable credentials
                return self._log(log, normalise_exception(exc))

        try:
            reply = await adapter.execute(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — P13: every exception becomes the one shape
            return self._log(log, normalise_exception(exc))
        if not isinstance(reply, dict):
            return self._log(log, failure(ErrorType.INVALID_JSON, f"Adapter returned {type(reply).__name__}."))
        return self._log(log, normalise_reply(reply))

    @staticmethod
    def _log(log: dict, result: dict) -> dict:
        # Metadata only: inputs, outputs and error text stay out of the logs (FR-AUTH-006).
        logger.info("step result", extra={"agem": {**log, "status": result["status"],
                                                   "error_type": result.get("error_type")}})
        return result
