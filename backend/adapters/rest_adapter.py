"""Adapter for REST/API agents (FR-ADP-002), speaking the frozen contract in docs/api-spec.md (FR-ADP-010).

A plain Python agent behind `agent_wrappers/python_wrapper.py` is a REST agent too.
"""

import httpx

from adapters.base_adapter import BaseAdapter

# P11: every agent call is bounded. The docs fix no per-step agent timeout; 30 s
# matches the only other call limit they do fix (web research, docs/websearch.md).
EXECUTE_TIMEOUT_SECONDS = 30.0
HEALTH_TIMEOUT_SECONDS = 5.0

CONTRACT_STATUSES = {"SUCCEEDED", "FAILED"}


class RestAdapter(BaseAdapter):
    def __init__(self, endpoint: str, timeout: float = EXECUTE_TIMEOUT_SECONDS, credentials: str | None = None):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        # Decrypted Agent.encrypted_credentials, sent on every call (docs/api-spec.md).
        self.headers = {"Authorization": f"Bearer {credentials}"} if credentials else {}

    async def execute(self, input: dict) -> dict:
        """POST `{task, input, context}` to `{endpoint}/execute`.

        Raises `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.HTTPStatusError`
        (non-2xx) or `ValueError` (body is not JSON, or not the contract shape);
        `step_executor.py` maps these to TIMEOUT, CONNECTION_ERROR, HTTP_5XX /
        AGENT_ERROR and INVALID_JSON.
        """
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            response = await client.post(f"{self.endpoint}/execute", json=input)
        response.raise_for_status()
        reply = response.json()
        if not isinstance(reply, dict) or reply.get("status") not in CONTRACT_STATUSES:
            raise ValueError(f"Agent reply does not follow the agent contract: {response.text[:500]}")
        return reply

    async def health(self) -> bool:
        """True only if `GET {endpoint}/health` answers 200 (FR-AGT-011). Never raises."""
        try:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS, headers=self.headers) as client:
                response = await client.get(f"{self.endpoint}/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200
