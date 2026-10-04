"""Shared plumbing for LLM-backed agents."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from patchwork.interfaces import LLM
from patchwork.llm import LLMError
from patchwork.models.schemas import EngineeringTask

D = TypeVar("D", bound=BaseModel)


class Agent:
    role: str = "agent"
    system_prompt: str = ""

    def __init__(self, llm: LLM, max_retries: int = 1) -> None:
        self.llm = llm
        self.max_retries = max_retries

    def ask(self, prompt: str, schema: type[D]) -> D:
        """Ask the model to fill `schema`.

        Structured outputs guarantee the JSON shape, but some constraints are
        only checked client-side (custom validators, numeric bounds). On a
        validation error the agent re-asks once with the error attached.
        """
        attempt_prompt = prompt
        for attempt in range(self.max_retries + 1):
            try:
                return self.llm.generate(self.role, self.system_prompt, attempt_prompt, schema)
            except ValidationError as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"{self.role}: invalid response: {exc}") from exc
                attempt_prompt = f"{prompt}\n\nYour previous reply failed validation:\n{exc}\nPlease correct it."
        raise AssertionError("unreachable")


def format_task(task: EngineeringTask) -> str:
    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(task.acceptance_criteria)) or "(none given)"
    return f"# Task {task.id}: {task.title}\n\n{task.description}\n\nAcceptance criteria (0-based):\n{criteria}"
