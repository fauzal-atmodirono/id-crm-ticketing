"""P7 task 11 -- what `main.py` actually wires, driven through the real app.

P6's Critical finding was a router that shipped complete, green and mounted
nowhere: every endpoint 404ed and no unit suite could tell. P7 arrived with two
more of the same shape, because the tasks that wrote them did not own `main.py`:

* `features/assist/translate_router.py` -- ten passing tests, never mounted.
* `features/chat/resolved_case_index.py` -- nine passing tests, and two of its
  four collaborators had no implementation anywhere in the tree, so an operator
  could turn both P7 resolve flags on and nothing would ever be summarised.

So every test here boots `bootstrap_application()` and makes a request. An
`openapi()` path assertion is used only to say WHICH path should exist; it is
never the proof, because a path can be present while its dependencies are
misconfigured -- and in the resolve case the wiring under test is not a path at
all, it is which objects the chat router was handed.

Everything faked is faked at the edge: the Gemini client and Chatwoot's
`add_private_note`. The summariser prompt, the persona application, the route's
own authorisation, the transcript reader, the webhook dispatcher and the indexer
are all the real, wired ones.
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

_WEBHOOK_SECRET = "p7-wiring-secret"
_API_KEY = "p7-wiring-key"

# Every P7 flag, so a test can clear the lot before setting the ones it means.
# The both-flag-states gate runs this suite with eight of them exported as true.
_P7_FLAGS = (
    "SENTIMENT_CLASSIFIER_ENABLED",
    "SENTIMENT_TONE_ADJUSTMENT_ENABLED",
    "TRANSLATION_ENABLED",
    "TRANSLATION_OUTBOUND_TAMIL_ENABLED",
    "FAQ_SUGGESTION_POPUP_ENABLED",
    "MEDIA_DIAGNOSIS_PROMPT_ENABLED",
    "RESOLVED_CASE_INDEX_ENABLED",
    "AUTO_SUMMARY_ON_RESOLVE_ENABLED",
)


class _FakeChatwoot:
    """Chatwoot's HTTP edge: one canned transcript in, every call captured.

    Patched at `ChatwootAdapter._request` -- the single method every read and
    write in that adapter funnels through -- rather than at `add_private_note`,
    so the assertions can see the actual posted payload. `private: True` on that
    payload is the same customer-safety property the translate router's own
    suite pins: a summary of a customer's case, or a translation of their own
    message, must never leave as an outgoing message.

    Patched on the CLASS because `main.py` builds several ChatwootAdapters.
    """

    transcript: ClassVar[list[dict[str, Any]]] = []
    calls: ClassVar[list[tuple[str, str, Any]]] = []

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        _FakeChatwoot.calls.append((method, path, payload))
        if method == "GET" and path.endswith("/messages"):
            return {"payload": list(_FakeChatwoot.transcript)}
        return {}

    @staticmethod
    def posted_messages() -> list[dict[str, Any]]:
        return [
            payload
            for method, path, payload in _FakeChatwoot.calls
            if method == "POST" and path.endswith("/messages") and isinstance(payload, dict)
        ]


def _stub_genai(text: str) -> MagicMock:
    genai = MagicMock()
    response = MagicMock()
    response.text = text
    genai.aio.models.generate_content = AsyncMock(return_value=response)
    return genai


def _patch_edges(
    monkeypatch: pytest.MonkeyPatch,
    model_text: str,
    transcript: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Fake the model and Chatwoot's transport; everything between stays real."""
    _FakeChatwoot.calls = []
    _FakeChatwoot.transcript = transcript if transcript is not None else []
    genai = _stub_genai(model_text)
    monkeypatch.setattr("chatbot.main._build_genai_client", lambda _settings: genai)
    monkeypatch.setattr(
        "chatbot.features.chat.adapters.chatwoot.ChatwootAdapter._request",
        _FakeChatwoot._request,
    )
    return genai


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    for flag in _P7_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("PROTON_BACKEND_KEY", _API_KEY)
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    monkeypatch.delenv("RBAC_ENABLED", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from chatbot.main import bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    return bootstrap_application()


def _clear_settings_cache() -> None:
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()


def _resolve(client: TestClient, conv_id: int = 4242) -> Any:
    """The webhook Chatwoot sends when an agent resolves a conversation.

    The flat top-level shape, which is what conversation-level events actually
    look like (see `schemas.py::_normalize_flat_conversation`).
    """
    return client.post(
        f"/webhooks/chatwoot?token={_WEBHOOK_SECRET}",
        json={"event": "conversation_resolved", "id": conv_id, "status": "resolved"},
    )


# --- Job 1: the translate router ------------------------------------------


def test_the_translate_endpoint_refuses_rather_than_404s(monkeypatch):
    """A mounted route that refuses is the distinction that matters: 404 was the
    bug, and 401 proves both the mount and its permission dependency ran."""
    _patch_edges(monkeypatch, "{}")
    try:
        app = _boot(monkeypatch)
        assert "/assist/translate" in app.openapi()["paths"]
        client = TestClient(app)
        unauth = client.post(
            "/assist/translate", json={"conversation_id": "1", "text": "apa khabar"}
        )
        assert unauth.status_code == 401, unauth.text
    finally:
        _clear_settings_cache()


def test_the_translate_endpoint_translates_and_posts_a_private_note(monkeypatch):
    """The whole path through the real app: permission gate, flag, the wired
    Gemini client, and the wired TicketingPort -- whose `add_private_note` is the
    only thing this endpoint may ever call, since a translation of the
    customer's own message must never reach the customer."""
    genai = _patch_edges(
        monkeypatch, '{"detected_source_language": "ms", "translation": "how are you"}'
    )
    try:
        app = _boot(monkeypatch, TRANSLATION_ENABLED="true")
        client = TestClient(app)
        res = client.post(
            "/assist/translate",
            json={"conversation_id": "55", "text": "apa khabar"},
            headers={"x-api-key": _API_KEY},
        )
        assert res.status_code == 200, res.text
        assert res.json() == {"translation": "how are you", "detected_source_language": "ms"}
        genai.aio.models.generate_content.assert_awaited()
        posted = _FakeChatwoot.posted_messages()
        assert len(posted) == 1
        assert "how are you" in posted[0]["content"]
        assert posted[0]["private"] is True
        assert ("POST", "/conversations/55/messages") in [
            (method, path) for method, path, _ in _FakeChatwoot.calls
        ]
    finally:
        _clear_settings_cache()


def test_the_translate_endpoint_is_mounted_but_inert_with_the_flag_off(monkeypatch):
    """Mounted unconditionally on purpose (see main.py's comment). With the flag
    off the response carries no `translation` field for a UI to mistake for a
    successful translation, and neither the model nor the note is touched."""
    genai = _patch_edges(monkeypatch, "{}")
    try:
        app = _boot(monkeypatch)
        client = TestClient(app)
        res = client.post(
            "/assist/translate",
            json={"conversation_id": "55", "text": "apa khabar"},
            headers={"x-api-key": _API_KEY},
        )
        assert res.status_code == 200, res.text
        assert res.json()["disabled"] is True
        assert "translation" not in res.json()
        genai.aio.models.generate_content.assert_not_awaited()
        assert _FakeChatwoot.posted_messages() == []
    finally:
        _clear_settings_cache()


def test_outbound_tamil_is_still_refused_through_the_real_app(monkeypatch):
    """The one P7 flag the all-on gate deliberately omits. With translation on
    and outbound Tamil off, a reply INTO Tamil is refused before any model call
    -- asserted here at the mounted endpoint, not only in the router's own
    suite, because this is the gate the fork's button reaches."""
    genai = _patch_edges(monkeypatch, "{}")
    try:
        app = _boot(monkeypatch, TRANSLATION_ENABLED="true")
        client = TestClient(app)
        res = client.post(
            "/assist/translate",
            json={"conversation_id": "55", "text": "how are you", "target_language": "ta"},
            headers={"x-api-key": _API_KEY},
        )
        assert res.status_code == 403, res.text
        genai.aio.models.generate_content.assert_not_awaited()
        assert _FakeChatwoot.posted_messages() == []
    finally:
        _clear_settings_cache()


# --- Job 2: the resolved-case summariser and index ------------------------


def test_a_resolve_posts_the_auto_summary_through_the_wired_summariser(monkeypatch):
    """End to end, and the point of job 2: webhook -> chat router -> indexer ->
    the summariser bound to the LIVE /assist/summarize route -> a private note.

    Every one of those links was missing before this task: the indexer had no
    summariser and no transcript port to give it, so this flag being on did
    nothing at all.
    """
    genai = _patch_edges(
        monkeypatch,
        "- the customer's brake light was replaced",
        transcript=[
            {"message_type": 0, "content": "brake light is out, plate WXY 1234"},
            {"message_type": 1, "content": "replaced under warranty"},
        ],
    )
    try:
        app = _boot(monkeypatch, AUTO_SUMMARY_ON_RESOLVE_ENABLED="true")
        client = TestClient(app)
        res = _resolve(client)
        assert res.status_code == 200, res.text
        assert res.json() == {"status": "resolved"}

        # The prompt that ran is /assist/summarize's own, not a second copy.
        from chatbot.features.assist.router import _SUMMARIZE_SYSTEM  # noqa: PLC0415

        systems = [
            call.kwargs["config"]["system_instruction"]
            for call in genai.aio.models.generate_content.await_args_list
        ]
        assert _SUMMARIZE_SYSTEM in systems
        # The transcript port really read the conversation, and the labelled
        # turns reached the model.
        prompts = [
            call.kwargs["contents"] for call in genai.aio.models.generate_content.await_args_list
        ]
        assert any("Customer: brake light is out" in p for p in prompts)

        posted = _FakeChatwoot.posted_messages()
        assert len(posted) == 1
        assert "Auto-summary" in posted[0]["content"]
        assert "brake light was replaced" in posted[0]["content"]
        # A recap of the customer's own case is never sent to the customer.
        assert posted[0]["private"] is True
    finally:
        _clear_settings_cache()


def test_a_resolve_summarises_nothing_with_both_resolve_flags_off(monkeypatch):
    """The ship-dark half. Not "no visible effect" -- no summariser call and no
    note at all, so a tenant that never asked for this pays nothing per resolve.
    """
    genai = _patch_edges(
        monkeypatch,
        "- a summary nobody asked for",
        transcript=[{"message_type": 0, "content": "brake light is out"}],
    )
    try:
        app = _boot(monkeypatch)
        client = TestClient(app)
        assert _resolve(client).status_code == 200
        genai.aio.models.generate_content.assert_not_awaited()
        assert _FakeChatwoot.posted_messages() == []
        # Not even the transcript was read: the indexer returns before touching
        # a single collaborator when both flags are off.
        assert not [path for method, path, _ in _FakeChatwoot.calls if path.endswith("/messages")]
    finally:
        _clear_settings_cache()


def test_a_resolve_survives_the_index_being_enabled_without_its_database(monkeypatch):
    """`RESOLVED_CASE_INDEX_ENABLED` on while the pgvector KB is off is a real
    operator state -- the two are independently default-off. It must degrade to a
    logged no-op, never to a failed resolve: resolving is the agent's action and
    our summarisation is an add-on. The note still posts when its own flag is on,
    because the two flags are independent rather than nested.
    """
    _patch_edges(
        monkeypatch,
        "- the customer's brake light was replaced",
        transcript=[{"message_type": 0, "content": "brake light is out"}],
    )
    try:
        app = _boot(
            monkeypatch,
            RESOLVED_CASE_INDEX_ENABLED="true",
            AUTO_SUMMARY_ON_RESOLVE_ENABLED="true",
            KNOWLEDGE_PG_ENABLED="false",
            KNOWLEDGE_DATABASE_URL="",
        )
        client = TestClient(app)
        res = _resolve(client)
        assert res.status_code == 200, res.text
        assert len(_FakeChatwoot.posted_messages()) == 1
        # Nothing was indexed and nothing tried to: no engine was built, so the
        # table-creation startup hook has nothing to do either.
        assert getattr(app.state, "resolved_case_engine", None) is None
    finally:
        _clear_settings_cache()


def test_the_summariser_is_bound_to_the_live_assist_route(monkeypatch):
    """Structural companion to the end-to-end test above: the object the chat
    router holds is bound to the mounted route, not merely constructed. Asserted
    because an unbound summariser fails silently -- it returns "" and the indexer
    correctly treats that as nothing to do, which is indistinguishable from the
    flag being off.
    """
    _patch_edges(monkeypatch, "- summary")
    try:
        app = _boot(monkeypatch)
        paths = app.openapi()["paths"]
        assert "/assist/summarize" in paths

        from chatbot.features.chat.resolved_case_adapters import (  # noqa: PLC0415
            find_summarize_endpoint,
        )

        endpoint = find_summarize_endpoint(app.router)
        assert endpoint is not None
    finally:
        _clear_settings_cache()


def test_main_actually_binds_the_summariser_to_that_route(monkeypatch):
    """The companion above asserts less than its name, and did so silently.

    `/assist/summarize` is mounted by the assist router, so the route is
    findable whether or not `main.py` ever calls `AssistSummarizeAdapter.bind()`.
    Deleting that call therefore left the structural test green -- caught by the
    P7 final review, not by CI. An unbound summariser returns `""`, the indexer
    correctly reads that as "nothing to do", and the result is indistinguishable
    from the flag being off: a silent no-op on a feature the operator switched on.

    So assert the binding itself: `bind()` must be called during bootstrap, with
    the same endpoint object `find_summarize_endpoint` locates on the finished app.
    """
    from chatbot.features.chat.resolved_case_adapters import (  # noqa: PLC0415
        AssistSummarizeAdapter,
        find_summarize_endpoint,
    )

    bound: list[object] = []
    real_bind = AssistSummarizeAdapter.bind

    def _spy(self, endpoint):
        bound.append(endpoint)
        return real_bind(self, endpoint)

    monkeypatch.setattr(AssistSummarizeAdapter, "bind", _spy)

    _patch_edges(monkeypatch, "- summary")
    try:
        app = _boot(monkeypatch)

        assert bound, "main.py never called AssistSummarizeAdapter.bind()"
        assert bound[-1] is not None, "bind() was called with None -- nothing to summarise with"
        assert bound[-1] is find_summarize_endpoint(app.router), (
            "the summariser was bound to a different function than the mounted "
            "/assist/summarize route, so the automatic path can drift from the "
            "one the agent-facing endpoint uses"
        )
    finally:
        _clear_settings_cache()
