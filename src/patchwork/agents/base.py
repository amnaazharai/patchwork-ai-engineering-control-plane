"""Shared plumbing for LLM-backed agents."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from patchwork.llm import LLM, LLMError, extract_json

M = TypeVar("M", bound=BaseModel)


class Agent:
    role: str = "agent"
    system_prompt: str = ""

    def __init__(self, llm: LLM, max_retries: int = 1) -> None:
        self.llm = llm
        self.max_retries = max_retries

    def ask(self, prompt: str, schema: type[M], **extra: object) -> M:
        """Send `prompt`, parse the JSON reply into `schema`.

        On malformed output the agent re-asks once with the validation error,
        which fixes most formatting slips without a full pipeline retry.
        `extra` fields are merged in before validation (e.g. ids the model
        should not have to echo back).
        """
        attempt_prompt = prompt
        for attempt in range(self.max_retries + 1):
            raw = self.llm.complete(self.role, self.system_prompt, attempt_prompt)
            try:
                data = extract_json(raw)
                if not isinstance(data, dict):
                    raise LLMError("expected a JSON object")
                return schema.model_validate({**data, **extra})
            except (LLMError, ValidationError) as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"{self.role}: invalid response: {exc}") from exc
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply could not be used:\n{exc}\n"
                    "Reply again with only the corrected JSON object."
                )
        raise AssertionError("unreachable")


def format_task(task) -> str:
    criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria) or "- (none given)"
    return f"# Task {task.id}: {task.title}\n\n{task.description}\n\nAcceptance criteria:\n{criteria}"
