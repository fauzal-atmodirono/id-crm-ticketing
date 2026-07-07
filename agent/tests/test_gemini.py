"""Tests for `app.ai.gemini`: function-call responses parse to a `Decision`,
plain-text responses fall back to a `handoff_to_human` decision, and
transient errors get one retry before falling back. The real SDK is never
invoked — a fake client object with the same `.models.generate_content(...)`
shape stands in for `google.genai.Client`.
"""

from types import SimpleNamespace

from app.ai import gemini


def _function_call_response(name, args, prompt_tokens=42):
    part = SimpleNamespace(function_call=SimpleNamespace(name=name, args=args), text=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=prompt_tokens),
        text=None,
    )


def _text_response(text, prompt_tokens=10):
    part = SimpleNamespace(function_call=None, text=text)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=prompt_tokens),
        text=text,
    )


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeClient:
    def __init__(self, responses):
        self.models = _FakeModels(responses)


def test_decide_parses_function_call_to_decision():
    client = _FakeClient([_function_call_response("send_reply", {"text": "Hi there"})])

    decision = gemini.decide("system prompt", "conversation context", client=client)

    assert decision.action == "send_reply"
    assert decision.args == {"text": "Hi there"}
    assert decision.raw_text is None
    assert decision.prompt_tokens == 42
    assert len(client.models.calls) == 1
    assert client.models.calls[0]["contents"] == "conversation context"


def test_decide_plain_text_response_falls_back_to_handoff():
    client = _FakeClient([_text_response("I'm not sure what you mean")])

    decision = gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.args == {"reason": "model returned no action"}
    assert decision.raw_text == "I'm not sure what you mean"


def test_decide_retries_once_on_error_then_succeeds():
    client = _FakeClient(
        [RuntimeError("transient"), _function_call_response("handoff_to_human", {"reason": "ok"})]
    )

    decision = gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.args == {"reason": "ok"}
    assert len(client.models.calls) == 2


def test_decide_gives_up_after_retry_and_hands_off():
    client = _FakeClient([RuntimeError("boom"), RuntimeError("boom again")])

    decision = gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.args == {"reason": "model returned no action"}
    assert len(client.models.calls) == 2


def test_generate_returns_stripped_text():
    client = _FakeClient([SimpleNamespace(text="  Draft reply text  \n", candidates=[], usage_metadata=None)])

    text = gemini.generate("system prompt", "context", client=client)

    assert text == "Draft reply text"


def test_generate_raises_after_retry_exhausted():
    client = _FakeClient([RuntimeError("boom"), RuntimeError("boom again")])

    try:
        gemini.generate("system prompt", "context", client=client)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass

    assert len(client.models.calls) == 2
