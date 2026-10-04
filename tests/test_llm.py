import pytest

from patchwork.llm import LLMError, ScriptedLLM, extract_json


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Here you go:\n```json\n{"a": 1}\n```\nThanks',
        'Sure! {"a": 1} hope that helps',
    ],
)
def test_extract_json(text):
    assert extract_json(text) == {"a": 1}


def test_extract_json_failure():
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_scripted_llm_replays_per_role_and_records_calls():
    llm = ScriptedLLM({"a": ["one", lambda system, prompt: prompt.upper()]})
    assert llm.complete("a", "sys", "x") == "one"
    assert llm.complete("a", "sys", "hi") == "HI"
    assert [c.response for c in llm.calls] == ["one", "HI"]
    with pytest.raises(LLMError):
        llm.complete("a", "sys", "again")


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
    def __init__(self, stop_reason, text="ok"):
        from types import SimpleNamespace as NS

        self.kwargs = None
        message = NS(stop_reason=stop_reason, content=[NS(type="thinking"), NS(type="text", text=text)])

        def stream(**kwargs):
            self.kwargs = kwargs
            return _FakeStream(message)

        self.beta = NS(messages=NS(stream=stream))


def test_anthropic_llm_request_shape_and_text():
    from patchwork.llm import AnthropicLLM

    client = _FakeClient("end_turn", '{"x": 1}')
    llm = AnthropicLLM(model="claude-opus-5-5", effort="high", client=client)
    assert llm.complete("planner", "sys", "hello") == '{"x": 1}'
    assert client.kwargs["model"] == "claude-opus-5-5"
    assert client.kwargs["output_config"] == {"effort": "high"}
    assert client.kwargs["fallbacks"] == "default"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_anthropic_llm_raises_on_unusable_stop(stop_reason):
    from patchwork.llm import AnthropicLLM

    with pytest.raises(LLMError):
        AnthropicLLM(client=_FakeClient(stop_reason)).complete("planner", "sys", "hi")
