from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel, ValidationError

from patchwork.interfaces import LLM
from patchwork.llm import AnthropicLLM, LLMError, ScriptedLLM


class Out(BaseModel):
    a: int


def test_backends_satisfy_llm_protocol():
    assert isinstance(ScriptedLLM({}), LLM)
    assert isinstance(AnthropicLLM(client=object()), LLM)


def test_scripted_llm_accepts_str_dict_model_and_callable():
    llm = ScriptedLLM({"x": ['{"a": 1}', {"a": 2}, Out(a=3), lambda system, prompt: {"a": len(prompt)}]})
    assert [llm.generate("x", "sys", "hi", Out).a for _ in range(4)] == [1, 2, 3, 2]
    assert [c.schema for c in llm.calls] == ["Out"] * 4


def test_scripted_llm_validates_against_schema():
    with pytest.raises(ValidationError):
        ScriptedLLM({"x": [{"a": "nope"}]}).generate("x", "s", "p", Out)


def test_scripted_llm_runs_out():
    with pytest.raises(LLMError, match="no response left"):
        ScriptedLLM({}).generate("x", "s", "p", Out)


class _FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class _FakeClient:
    def __init__(self, stop_reason, parsed=None):
        self.kwargs = None
        message = NS(stop_reason=stop_reason, parsed_output=parsed, usage=NS(input_tokens=10, output_tokens=5))

        def stream(**kwargs):
            self.kwargs = kwargs
            return _FakeStream(message)

        self.beta = NS(messages=NS(stream=stream))


def test_anthropic_llm_uses_structured_outputs_and_records_usage():
    client = _FakeClient("end_turn", Out(a=7))
    llm = AnthropicLLM(model="claude-opus-5-5", effort="high", client=client)
    assert llm.generate("planner", "sys", "hello", Out) == Out(a=7)
    assert client.kwargs["output_format"] is Out
    assert client.kwargs["model"] == "claude-opus-5-5"
    assert client.kwargs["output_config"] == {"effort": "high"}
    assert client.kwargs["fallbacks"] == "default"
    call = llm.calls[0]
    assert (call.role, call.schema, call.input_tokens, call.output_tokens) == ("planner", "Out", 10, 5)


@pytest.mark.parametrize("stop_reason,parsed", [("refusal", None), ("max_tokens", None), ("end_turn", None)])
def test_anthropic_llm_raises_on_unusable_response(stop_reason, parsed):
    with pytest.raises(LLMError):
        AnthropicLLM(client=_FakeClient(stop_reason, parsed)).generate("planner", "s", "p", Out)
