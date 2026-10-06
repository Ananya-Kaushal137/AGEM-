"""The single LLM wrapper — every LLM call in AGEM goes through `call_json` (P15, FR-DIAG-009).

No other module imports a provider SDK. The caller passes the Pydantic schema the
reply must match and the safe value to return when it can't get one:

    reply = await call_json(system=..., prompt=..., schema=Diagnosis,
                            fallback=Diagnosis(diagnosis="NORMAL_ERROR", ...), tier="cheap")

What the wrapper guarantees (Architecture §11.4, §12):
- the model is asked for JSON only, and the reply is validated against `schema` (FR-DIAG-005);
- on a parse/validation failure the same call is retried up to 2 times, with the
  validation error appended to the prompt (FR-DIAG-006);
- it never raises and never hangs: retries exhausted, the time limit passed (20 s by
  default, NFR-PERF-03), a provider error or a missing key all return `fallback` (FR-DIAG-007);
- every attempt is logged — model, attempt, verdict, latency, validation error (Architecture §12).
  The prompt and reply text carry agent payloads, so they are logged only when
  LLM_LOG_PAYLOADS is on (FR-AUTH-006: metadata by default, payloads behind a debug flag).

Callers: `orchestrator/master_agent.py` (diagnosis, cheap tier), `capability_engine/searcher.py`
(Prompt 10) and `capability_engine/builder.py` (Prompt 13, strong tier).
"""

import asyncio
import json
import logging
import time
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

__all__ = ["call_json", "Tier", "DEFAULT_TIMEOUT_SECONDS", "MAX_VALIDATION_RETRIES"]

logger = logging.getLogger("agem.llm")

T = TypeVar("T", bound=BaseModel)
Tier = Literal["cheap", "strong"]

DEFAULT_TIMEOUT_SECONDS = 20.0  # NFR-PERF-03: diagnosis is treated as a timeout at 20 s
MAX_VALIDATION_RETRIES = 2  # FR-DIAG-006: 1 call + 2 retries = at most 3 requests
MAX_OUTPUT_TOKENS = 4096

# Architecture §22, FR-DIAG-008: a cheaper tier for short structured classification,
# the stronger model for code generation. Overridable with LLM_MODEL_CHEAP / LLM_MODEL_STRONG.
DEFAULT_MODELS: dict[str, dict[Tier, str]] = {
    "anthropic": {"cheap": "claude-haiku-4-5", "strong": "claude-opus-5-5"},
    "openai": {"cheap": "gpt-4o-mini", "strong": "gpt-4o"},
}

_JSON_INSTRUCTION = (
    "Reply with ONLY one JSON object that matches this JSON Schema. "
    "No prose, no markdown, no code fences.\n{schema}"
)


class LLMReplyError(Exception):
    """The provider answered, but not with usable text (e.g. a refusal or an empty reply)."""


class _RetriesExhausted(Exception):
    """All 3 attempts returned JSON that failed validation."""


def model_for(tier: Tier) -> str:
    settings = get_settings()
    override = settings.llm_model_cheap if tier == "cheap" else settings.llm_model_strong
    return override or DEFAULT_MODELS[settings.llm_provider][tier]


async def call_json(
    *,
    system: str,
    prompt: str,
    schema: type[T],
    fallback: T,
    tier: Tier = "cheap",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    log_context: dict[str, Any] | None = None,
) -> T:
    """Ask the LLM for JSON matching `schema`; return the validated object or `fallback`.

    `timeout` bounds the whole call, retries included. `log_context` (e.g. execution_id,
    step_id, agent_id) is attached to every log line so one run can be followed.
    """
    ctx = {"tier": tier, **(log_context or {})}
    if not get_settings().llm_api_key.get_secret_value():
        logger.error("llm.no_api_key", extra={"agem": {**ctx, "verdict": "FALLBACK"}})
        return fallback
    try:
        return await asyncio.wait_for(_call_with_retries(system, prompt, schema, tier, ctx), timeout=timeout)
    except TimeoutError:
        logger.warning("llm.timeout", extra={"agem": {**ctx, "timeout_s": timeout, "verdict": "FALLBACK"}})
    except _RetriesExhausted:
        pass  # already logged attempt by attempt
    except Exception as exc:  # provider/network errors: fail safe, never crash the workflow
        logger.warning("llm.error", extra={"agem": {**ctx, "error": repr(exc)[:500], "verdict": "FALLBACK"}})
    return fallback


async def _call_with_retries(system: str, prompt: str, schema: type[T], tier: Tier, ctx: dict[str, Any]) -> T:
    full_system = f"{system}\n\n{_JSON_INSTRUCTION.format(schema=json.dumps(schema.model_json_schema()))}"
    model = model_for(tier)
    current_prompt = prompt
    for attempt in range(1, MAX_VALIDATION_RETRIES + 2):
        started = time.monotonic()
        text = await _complete(system=full_system, prompt=current_prompt, model=model)
        log = {**ctx, "model": model, "attempt": attempt, "latency_ms": round((time.monotonic() - started) * 1000)}
        if get_settings().llm_log_payloads:
            log |= {"prompt": current_prompt, "response": text}
        try:
            result = schema.model_validate_json(_strip_fences(text))
        except ValidationError as exc:
            error = _short_errors(exc)
            logger.warning("llm.invalid_reply", extra={"agem": {**log, "verdict": "INVALID", "error": error}})
            # FR-DIAG-006: the same call again, with what was wrong appended.
            current_prompt = (
                f"{prompt}\n\nYour previous reply was rejected because it did not match the schema:\n"
                f"{error}\nReply again with only the corrected JSON object."
            )
            continue
        logger.info("llm.reply", extra={"agem": {**log, "verdict": "VALID"}})
        return result
    logger.warning("llm.retries_exhausted", extra={"agem": {**ctx, "model": model, "verdict": "FALLBACK"}})
    raise _RetriesExhausted


async def _complete(*, system: str, prompt: str, model: str) -> str:
    """One request to the configured provider; returns the reply text.

    The only function in the codebase that touches a provider SDK. SDK-level retries
    are off (`max_retries=0`) so every request is counted and bounded here.
    """
    settings = get_settings()
    key = settings.llm_api_key.get_secret_value()

    if settings.llm_provider == "anthropic":
        import anthropic

        async with anthropic.AsyncAnthropic(api_key=key, max_retries=0) as client:
            response = await client.messages.create(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        if response.stop_reason == "refusal":
            raise LLMReplyError("model refused")
        text = "".join(block.text for block in response.content if block.type == "text")
    else:
        import openai

        async with openai.AsyncOpenAI(api_key=key, max_retries=0) as client:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            )
        text = response.choices[0].message.content or ""

    if not text.strip():
        raise LLMReplyError("empty reply")
    return text


def _strip_fences(text: str) -> str:
    """Accept a reply wrapped in ```json fences despite the instruction; nothing more lenient."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _short_errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors())[:1000]
