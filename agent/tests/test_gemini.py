"""Tests for `app.ai.gemini`: function-call responses parse to a `Decision`,
plain-text responses fall back to a `handoff_to_human` decision, and
transient errors get one retry before falling back. The real SDK is never
invoked — a fake client object with the same `.models.generate_content(...)`
shape stands in for `google.genai.Client`.

`decide`/`generate` are async: the sync SDK call runs via
`asyncio.to_thread` so a real Gemini round-trip never blocks the event
loop. These tests exercise that full async path — the fake client's sync
`generate_content` genuinely runs in the to_thread executor.
"""

from types import SimpleNamespace

from app.ai import gemini


def _function_call_response(name, args, prompt_tokens=42, output_tokens=None, cached_tokens=None):
    part = SimpleNamespace(function_call=SimpleNamespace(name=name, args=args), text=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            cached_content_token_count=cached_tokens,
        ),
        text=None,
    )


def _text_response(text, prompt_tokens=10, output_tokens=None, cached_tokens=None):
    part = SimpleNamespace(function_call=None, text=text)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            cached_content_token_count=cached_tokens,
        ),
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


async def test_decide_parses_function_call_to_decision():
    client = _FakeClient([_function_call_response("send_reply", {"text": "Hi there"})])

    decision = await gemini.decide("system prompt", "conversation context", client=client)

    assert decision.action == "send_reply"
    assert decision.args == {"text": "Hi there"}
    assert decision.raw_text is None
    assert decision.prompt_tokens == 42
    assert len(client.models.calls) == 1
    assert client.models.calls[0]["contents"] == "conversation context"


async def test_decide_plain_text_response_falls_back_to_handoff():
    client = _FakeClient([_text_response("I'm not sure what you mean")])

    decision = await gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.args == {"reason": "model returned no action"}
    assert decision.raw_text == "I'm not sure what you mean"


async def test_decide_retries_once_on_error_then_succeeds():
    client = _FakeClient(
        [RuntimeError("transient"), _function_call_response("handoff_to_human", {"reason": "ok"})]
    )

    decision = await gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.args == {"reason": "ok"}
    assert len(client.models.calls) == 2


async def test_decide_gives_up_after_retry_and_hands_off():
    client = _FakeClient([RuntimeError("boom"), RuntimeError("boom again")])

    decision = await gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.args == {"reason": "model returned no action"}
    assert len(client.models.calls) == 2


async def test_generate_returns_stripped_text():
    client = _FakeClient([SimpleNamespace(text="  Draft reply text  \n", candidates=[], usage_metadata=None)])

    text = await gemini.generate("system prompt", "context", client=client)

    assert text == "Draft reply text"


async def test_generate_raises_after_retry_exhausted():
    client = _FakeClient([RuntimeError("boom"), RuntimeError("boom again")])

    try:
        await gemini.generate("system prompt", "context", client=client)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass

    assert len(client.models.calls) == 2


# --- Token capture (P8 task 1): prompt_tokens, output_tokens, cached_tokens ---
#
# `None` means "not captured"; `0` means "captured, and it was zero". A cost
# report that conflates them understates spend for every call whose usage
# metadata genuinely came back zero on a field. Tests three and four are the
# pair that keeps that distinction real, not just documented.


async def test_output_tokens_are_extracted_from_usage_metadata():
    client = _FakeClient(
        [_function_call_response("send_reply", {"text": "Hi there"}, output_tokens=17)]
    )

    decision = await gemini.decide("system prompt", "conversation context", client=client)

    assert decision.output_tokens == 17


async def test_cached_tokens_are_extracted_when_present():
    client = _FakeClient(
        [_function_call_response("send_reply", {"text": "Hi there"}, cached_tokens=8)]
    )

    decision = await gemini.decide("system prompt", "conversation context", client=client)

    assert decision.cached_tokens == 8


async def test_absent_usage_metadata_records_none_for_all_three():
    part = SimpleNamespace(function_call=SimpleNamespace(name="send_reply", args={}), text=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate], usage_metadata=None, text=None)
    client = _FakeClient([response])

    decision = await gemini.decide("system prompt", "context", client=client)

    assert decision.prompt_tokens is None
    assert decision.output_tokens is None
    assert decision.cached_tokens is None


async def test_a_zero_token_field_records_zero_and_not_none():
    client = _FakeClient(
        [
            _function_call_response(
                "send_reply", {"text": "Hi"}, prompt_tokens=5, output_tokens=0, cached_tokens=0
            )
        ]
    )

    decision = await gemini.decide("system prompt", "context", client=client)

    assert decision.output_tokens == 0
    assert decision.output_tokens is not None
    assert decision.cached_tokens == 0
    assert decision.cached_tokens is not None


async def test_the_existing_prompt_tokens_extraction_is_unchanged():
    client = _FakeClient(
        [_function_call_response("send_reply", {"text": "Hi there"}, prompt_tokens=42)]
    )

    decision = await gemini.decide("system prompt", "conversation context", client=client)

    assert decision.prompt_tokens == 42


async def test_the_handoff_fallback_path_still_records_what_it_knows():
    # The model answered in plain text instead of calling a function, so
    # `decide` falls back to handoff_to_human -- but a real response (with
    # real usage_metadata) came back. Losing the token counts here would
    # silently under-report exactly the calls that went wrong.
    client = _FakeClient(
        [_text_response("I'm not sure what you mean", prompt_tokens=10, output_tokens=6, cached_tokens=2)]
    )

    decision = await gemini.decide("system prompt", "context", client=client)

    assert decision.action == "handoff_to_human"
    assert decision.prompt_tokens == 10
    assert decision.output_tokens == 6
    assert decision.cached_tokens == 2
