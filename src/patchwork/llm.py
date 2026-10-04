"""LLM backends.

Agents talk to an `LLM` through one method, `complete(role, system, prompt)`.
`AnthropicLLM` calls Claude; `ScriptedLLM` replays canned responses so demos
and tests run offline and deterministically.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_MODEL = "claude-opus-5-5"


class LLMError(Exception):
    pass


class LLM(Protocol):
    def complete(self, role: str, system: str, prompt: str) -> str: ...


@dataclass
class Call:
    role: str
    system: str
    prompt: str
    response: str


class AnthropicLLM:
    """Claude via the official Anthropic SDK.

    Streams each request (agent responses can be long full-file rewrites) and
    enables server-side refusal fallbacks so a declined request is retried on
    a fallback model inside the same call.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int = 64_000,
        client: object | None = None,
    ) -> None:
        import anthropic

        self.model = model or os.environ.get("PATCHWORK_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("PATCHWORK_EFFORT", "high")
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic()
        self.calls: list[Call] = []

    def complete(self, role: str, system: str, prompt: str) -> str:
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort},
            betas=[self.FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise LLMError(f"{role}: request declined by the model")
        if message.stop_reason == "max_tokens":
            raise LLMError(f"{role}: response truncated at max_tokens={self.max_tokens}")
        text = "".join(b.text for b in message.content if b.type == "text")
        self.calls.append(Call(role, system, prompt, text))
        return text


Responder = str | Callable[[str, str], str]


@dataclass
class ScriptedLLM:
    """Replays responses per agent role, in order.

    A response may be a string or a callable `(system, prompt) -> str`, which
    lets a script react to what the agent actually sent (e.g. test failures).
    """

    script: dict[str, Iterable[Responder]]
    calls: list[Call] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queues: dict[str, deque[Responder]] = defaultdict(deque)
        for role, responses in self.script.items():
            self._queues[role].extend(responses)

    def complete(self, role: str, system: str, prompt: str) -> str:
        queue = self._queues[role]
        if not queue:
            raise LLMError(f"ScriptedLLM has no response left for role {role!r}")
        item = queue.popleft()
        text = item(system, prompt) if callable(item) else item
        self.calls.append(Call(role, system, prompt, text))
        return text


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def extract_json(text: str) -> object:
    """Parse the JSON object in an LLM response.

    Accepts a bare object, a ```json fenced block, or an object embedded in
    prose (outermost braces).
    """
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    candidates.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise LLMError("no JSON object found in model response")
