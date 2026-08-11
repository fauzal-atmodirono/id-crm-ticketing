"""/assist/* with attachments: what actually reaches Gemini.

Complements `test_assist_media.py` (which unit-tests the pipeline) by asserting
the wiring: that markers land in the prompt, that bytes land in `contents`, that
the media instruction is added only when there is media, and — the part most
likely to regress — that a text-only request still produces the exact call it
produced before any of this existed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.router import _MEDIA_INSTRUCTION, build_assist_router
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings

_URL = "https://crm.example.test/rails/active_storage/blobs/redirect/abc/clip.mp4"
_KEY = "testkey"
_HEADERS = {"x-api-key": _KEY}

# The reported bug, as a request body.
_VIDEO_MESSAGES = [
    {"role": "customer", "content": "warranty for X50?", "attachments": []},
    {"role": "agent", "content": "Which year?", "attachments": []},
    {"role": "customer", "content": "this one", "attachments": [{"file_type": "video"}]},
]


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 3) -> list:
        return [KbArticle(title="FAQ", content="body", url="http://faq/1")]


class _FakeContext:
    def __init__(self, raises: bool = False) -> None:
        self._raises = raises
        self.calls: list[str] = []

    async def get_messages(self, conversation_id: str) -> list[dict]:
        self.calls.append(conversation_id)
        if self._raises:
            raise RuntimeError("chatwoot is down")
        return [
            {
                "message_type": 0,
                "private": False,
                "attachments": [{"file_type": "video", "data_url": _URL}],
            }
        ]


def _build(
    *, media_enabled: bool = True, context: _FakeContext | None = None
) -> tuple[TestClient, MagicMock, _FakeContext | None]:
    mock_genai = MagicMock()
    response = MagicMock()
    response.text = "AI output."
    mock_genai.aio.models.generate_content = AsyncMock(return_value=response)

    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=Settings(
                proton_backend_key=_KEY,
                assist_gemini_model="gemini-2.5-flash",
                assist_media_understanding_enabled=media_enabled,
                assist_media_max_bytes=1_000_000,
            ),
            knowledge_port=_FakeKnowledge(),
            genai_client=mock_genai,
            chatwoot_context=context,
        )
    )
    return TestClient(app), mock_genai, context


def _mock_video_download() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, content=b"video-bytes", headers={"content-type": "video/mp4"}
        )
    )


def _call_kwargs(mock_genai: MagicMock) -> dict:
    return mock_genai.aio.models.generate_content.call_args.kwargs


# ---------------------------------------------------------------------------
# Markers reach the prompt — no flag, no network
# ---------------------------------------------------------------------------


def test_marker_is_in_the_prompt_even_with_media_disabled() -> None:
    client, mock_genai, _ = _build(media_enabled=False)
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert "[3] Customer: this one [sent a video]" in _call_kwargs(mock_genai)["contents"]


def test_caption_less_attachment_survives_into_the_prompt() -> None:
    client, mock_genai, _ = _build(media_enabled=False)
    client.post(
        "/assist/suggest",
        json={
            "conversation_id": "42",
            "messages": [{"role": "customer", "attachments": [{"file_type": "audio"}]}],
        },
        headers=_HEADERS,
    )
    assert "Customer: [sent a voice note]" in _call_kwargs(mock_genai)["contents"]


# ---------------------------------------------------------------------------
# The no-media path must not change
# ---------------------------------------------------------------------------


def test_legacy_string_payload_still_accepted() -> None:
    client, mock_genai, _ = _build(media_enabled=False)
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Customer: hi", "Agent: hello"]},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert _call_kwargs(mock_genai)["contents"] == "[1] Customer: hi\n[2] Agent: hello"


def test_text_only_request_sends_a_plain_string_and_no_media_instruction() -> None:
    """Byte-identical to the pre-media behaviour, including for P7's
    resolved-case summariser which calls the endpoint function in-process."""
    client, mock_genai, _ = _build(media_enabled=False)
    client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Customer: hi"]},
        headers=_HEADERS,
    )
    kwargs = _call_kwargs(mock_genai)
    assert isinstance(kwargs["contents"], str)
    assert _MEDIA_INSTRUCTION not in kwargs["config"]["system_instruction"]


def test_flag_off_makes_no_chatwoot_call() -> None:
    context = _FakeContext()
    client, _, _ = _build(media_enabled=False, context=context)
    client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert context.calls == []


# ---------------------------------------------------------------------------
# The media path
# ---------------------------------------------------------------------------


@respx.mock
def test_video_bytes_reach_gemini_as_an_inline_part() -> None:
    _mock_video_download()
    client, mock_genai, _ = _build(context=_FakeContext())
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    contents = _call_kwargs(mock_genai)["contents"]
    inline = [p for p in contents.parts if p.inline_data is not None]
    assert len(inline) == 1
    assert inline[0].inline_data.mime_type == "video/mp4"
    assert inline[0].inline_data.data == b"video-bytes"


@respx.mock
def test_legacy_string_payload_still_gets_the_video() -> None:
    """THE BACKEND-ONLY DEPLOY CASE. Media collection is keyed on
    conversation_id and reads the Chatwoot API directly, so it does NOT depend
    on the frontend sending attachment metadata.

    That means a backend deploy against an UNREBUILT Chatwoot image — which
    still posts the legacy pre-rendered `list[str]` — already fixes the
    reported bug: the video reaches Gemini even though the transcript has no
    "[sent a video]" marker. Ship the SPA later for the marker; the answer
    stops being "what do you mean by 'this one'?" today.
    """
    _mock_video_download()
    client, mock_genai, _ = _build(context=_FakeContext())
    r = client.post(
        "/assist/suggest",
        json={
            "conversation_id": "42",
            "messages": ["Customer: warranty for X50?", "Customer: this one"],
        },
        headers=_HEADERS,
    )
    assert r.status_code == 200
    contents = _call_kwargs(mock_genai)["contents"]
    inline = [p for p in contents.parts if p.inline_data is not None]
    assert len(inline) == 1
    assert inline[0].inline_data.mime_type == "video/mp4"
    # the legacy transcript is preserved verbatim as the leading text part
    parts_text = contents.parts[0].text
    assert "[1] Customer: warranty for X50?" in parts_text
    assert "[sent a video]" not in parts_text  # no marker without the new SPA
    assert _MEDIA_INSTRUCTION in _call_kwargs(mock_genai)["config"]["system_instruction"]


@respx.mock
def test_transcript_still_leads_the_parts_list() -> None:
    _mock_video_download()
    client, mock_genai, _ = _build(context=_FakeContext())
    client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    parts = _call_kwargs(mock_genai)["contents"].parts
    assert "this one [sent a video]" in parts[0].text


@respx.mock
def test_media_instruction_is_added_only_when_media_is_present() -> None:
    """Without this line the model still hedged and asked the customer to
    explain the video it had just been handed."""
    _mock_video_download()
    client, mock_genai, _ = _build(context=_FakeContext())
    client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert _MEDIA_INSTRUCTION in _call_kwargs(mock_genai)["config"]["system_instruction"]


@respx.mock
def test_summarize_and_ask_also_receive_media() -> None:
    _mock_video_download()
    for path, body in (
        ("/assist/summarize", {}),
        ("/assist/ask", {"question": "what is wrong with the car?"}),
    ):
        client, mock_genai, _ = _build(context=_FakeContext())
        r = client.post(
            path,
            json={"conversation_id": "42", "messages": _VIDEO_MESSAGES, **body},
            headers=_HEADERS,
        )
        assert r.status_code == 200, path
        contents = _call_kwargs(mock_genai)["contents"]
        assert any(p.inline_data is not None for p in contents.parts), path


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------


def test_unreachable_chatwoot_still_returns_a_draft() -> None:
    """The invariant: no media condition may turn a working draft into no draft."""
    client, mock_genai, _ = _build(context=_FakeContext(raises=True))
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["draft"] == "AI output."
    # ...and the marker still told the model a video existed.
    assert "[sent a video]" in _call_kwargs(mock_genai)["contents"]


@respx.mock
def test_failed_download_still_returns_a_draft() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(500))
    client, mock_genai, _ = _build(context=_FakeContext())
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert isinstance(_call_kwargs(mock_genai)["contents"], str)


# ---------------------------------------------------------------------------
# KB retrieval must not be polluted by markers
# ---------------------------------------------------------------------------


def test_attachment_marker_never_becomes_a_search_term() -> None:
    seen: list[str] = []

    class _RecordingKnowledge:
        async def search_kb(self, query: str, limit: int = 3) -> list:
            seen.append(query)
            return []

    mock_genai = MagicMock()
    response = MagicMock()
    response.text = "out"
    mock_genai.aio.models.generate_content = AsyncMock(return_value=response)
    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=Settings(proton_backend_key=_KEY),
            knowledge_port=_RecordingKnowledge(),
            genai_client=mock_genai,
        )
    )
    TestClient(app).post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _VIDEO_MESSAGES},
        headers=_HEADERS,
    )
    assert seen == ["warranty for X50?\nthis one"]
