"""LLM backends implementing `patchwork.interfaces.LLM`.

`AnthropicLLM` calls Claude with structured outputs, so every response is
constrained to the requested Pydantic schema. `ScriptedLLM` replays canned
responses so demos and tests run offline and deterministically. Both record
every call for tracing.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"

M = TypeVar("M", bound=BaseModel)


class LLMError(Exception):
    pass


@dataclass
class Call:
    """Trace record for one model call."""

    role: str
    schema: str
    system: str
    prompt: str
    response: str
    latency_s: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None


class AnthropicLLM:
    """Claude via the official Anthropic SDK.

    Streams each request (agents can return long full-file rewrites), uses
    `output_format` so the response is schema-constrained and parsed by the
    SDK, and enables server-side refusal fallbacks so a declined request is
    retried on a fallback model inside the same call.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int = 64_000,
        client: Any | None = None,
    ) -> None:
        import anthropic

        self._api_error = anthropic.APIError
        self.client = client if client is not None else anthropic.Anthropic()
        self.model = model or os.environ.get("PATCHWORK_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("PATCHWORK_EFFORT", "high")
        self.max_tokens = max_tokens
        self.calls: list[Call] = []

    def generate(self, role: str, system: str, prompt: str, schema: type[M]) -> M:
        start = time.monotonic()
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": self.effort},
                betas=[self.FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except self._api_error as exc:  # auth, rate limit, overload after SDK retries
            raise LLMError(f"{role}: API error: {exc}") from exc

        if message.stop_reason == "refusal":
            raise LLMError(f"{role}: request declined by the model")
        if message.stop_reason == "max_tokens":
            raise LLMError(f"{role}: response truncated at max_tokens={self.max_tokens}")
        parsed = message.parsed_output
        if parsed is None:
            raise LLMError(f"{role}: no structured output in response")

        usage = getattr(message, "usage", None)
        self.calls.append(
            Call(
                role=role,
                schema=schema.__name__,
                system=system,
                prompt=prompt,
                response=parsed.model_dump_json(),
                latency_s=round(time.monotonic() - start, 3),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )
        )
        return parsed


Response = str | dict | BaseModel | Callable[[str, str], "str | dict | BaseModel"]


@dataclass
class ScriptedLLM:
    """Replays responses per agent role, in order.

    A response may be a JSON string, a dict, a model instance, or a callable
    `(system, prompt) -> one of those`, which lets a script react to what the
    agent actually sent (e.g. test failures). Responses are validated against
    the requested schema exactly like real structured outputs would be.
    """

    script: dict[str, Iterable[Response]]
    calls: list[Call] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queues: dict[str, deque[Response]] = defaultdict(deque)
        for role, responses in self.script.items():
            self._queues[role].extend(responses)

    def generate(self, role: str, system: str, prompt: str, schema: type[M]) -> M:
        queue = self._queues[role]
        if not queue:
            raise LLMError(f"ScriptedLLM has no response left for role {role!r}")
        item = queue.popleft()
        if callable(item) and not isinstance(item, BaseModel):
            item = item(system, prompt)
        raw = (
            item.model_dump_json()
            if isinstance(item, BaseModel)
            else item
            if isinstance(item, str)
            else json.dumps(item)
        )
        self.calls.append(Call(role=role, schema=schema.__name__, system=system, prompt=prompt, response=raw))
        return schema.model_validate_json(raw)
