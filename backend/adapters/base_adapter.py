"""Common adapter interface — the interoperability contract (Architecture §25.1, FR-ADP-001).

Every agent, whatever its framework, reaches AGEM through `execute()`. The
Orchestrator only ever sees this one method (P6, BR-12).
"""

from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    @abstractmethod
    async def execute(self, input: dict) -> dict:
        """Send one step to the agent and return its contract reply.

        `input` is the request body `{task, input, context}` (docs/api-spec.md).
        The reply is `{"status": "SUCCEEDED", "output": {...}}` or
        `{"status": "FAILED", "error": "...", ...}`. Transport failures are raised,
        not returned: `step_executor.py` is the one place they are normalised (P13).
        """
