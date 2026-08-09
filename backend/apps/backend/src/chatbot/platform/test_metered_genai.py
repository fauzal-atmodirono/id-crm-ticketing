"""Tests for the client-boundary Gemini metering wrapper.

`test_no_gemini_client_is_constructed_outside_the_wrapper` is the reason this
module exists. Everything else proves the wrapper works; only that one proves
the *architecture* holds for call sites nobody has written yet.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import google.genai
import pytest

from chatbot.features.metrics.token_usage import TokenUsage
from chatbot.platform import metered_genai
from chatbot.platform.config import Settings
from chatbot.platform.metered_genai import (
    SURFACE_ASSIST_SUGGEST,
    SURFACE_ASSIST_TRANSLATE,
    SURFACE_EMBED,
    MeteredGenaiClient,
    build_metered_genai_client,
    with_surface,
)

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeSink:
    """Records what the wrapper hands it. `fail` makes every record raise, to
    prove the wrapper is fail-open."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[TokenUsage] = []
        self.fail = fail

    async def record(self, usage: TokenUsage) -> None:
        if self.fail:
            raise RuntimeError("sink down")
        self.rows.append(usage)


def _usage(prompt: Any = 11, output: Any = 7, cached: Any = 3) -> SimpleNamespace:
    """A stand-in for `types.GenerateContentResponseUsageMetadata`, using the
    real field names verified against the installed SDK (google-genai 2.8.0)."""
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=output,
        cached_content_token_count=cached,
    )


class _FakeAsyncModels:
    def __init__(self, response: Any = None, *, chunks: list[Any] | None = None) -> None:
        self._response = response
        self._chunks = chunks or []
        self.calls: list[dict[str, Any]] = []
        self.raises: Exception | None = None

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self._response

    async def embed_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        async def _gen() -> Any:
            for c in self._chunks:
                yield c

        return _gen()

    async def count_tokens(self, **kwargs: Any) -> str:
        return "passed-through"


class _FakeSyncModels:
    def __init__(self, response: Any = None) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def embed_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response


class _FakeRawClient:
    def __init__(self, *, response: Any = None, chunks: list[Any] | None = None) -> None:
        self.models = _FakeSyncModels(response)
        self.aio = SimpleNamespace(
            models=_FakeAsyncModels(response, chunks=chunks),
            live=SimpleNamespace(connect=lambda **_kw: "live-session"),
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _settings(*, metering: bool = True) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            token_metering_enabled=metering,
            metrics_provider="noop",
            google_genai_use_vertexai=False,
            vertex_project_id="p",
            vertex_location="l",
        ),
    )


def _build(
    raw: _FakeRawClient,
    sink: _FakeSink,
    *,
    metering: bool = True,
    surface: str = SURFACE_ASSIST_SUGGEST,
) -> Any:
    return build_metered_genai_client(
        _settings(metering=metering), surface=surface, sink=sink, client=raw
    )


# --------------------------------------------------------------------------
# 1. A wrapped call records a row -- with all three token classes
# --------------------------------------------------------------------------


async def test_a_wrapped_call_records_a_token_usage_row() -> None:
    response = SimpleNamespace(text="hi", usage_metadata=_usage(11, 7, 3))
    raw = _FakeRawClient(response=response)
    sink = _FakeSink()
    client = _build(raw, sink)

    got = await client.aio.models.generate_content(model="gemini-2.5-flash", contents="x")

    # The wrapper wraps; it does not transform. Identity, not equality.
    assert got is response
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row == TokenUsage(
        service="backend",
        surface=SURFACE_ASSIST_SUGGEST,
        model="gemini-2.5-flash",
        prompt_tokens=11,
        output_tokens=7,
        cached_tokens=3,
    )


async def test_missing_usage_metadata_records_none_not_zero() -> None:
    """A zero-token call and an uncaptured call are different facts. Conflating
    them understates spend, so absence must be `None` -- and a real zero must
    survive as `0`, which a truthiness check would collapse into the same
    branch."""
    sink = _FakeSink()
    raw = _FakeRawClient(response=SimpleNamespace(text="hi"))  # no usage_metadata at all
    await _build(raw, sink).aio.models.generate_content(model="m", contents="x")
    assert (sink.rows[0].prompt_tokens, sink.rows[0].output_tokens, sink.rows[0].cached_tokens) == (
        None,
        None,
        None,
    )

    sink2 = _FakeSink()
    raw2 = _FakeRawClient(response=SimpleNamespace(usage_metadata=_usage(0, 0, 0)))
    await _build(raw2, sink2).aio.models.generate_content(model="m", contents="x")
    row = sink2.rows[0]
    assert (row.prompt_tokens, row.output_tokens, row.cached_tokens) == (0, 0, 0)
    assert row.prompt_tokens is not None  # observed-zero, not missing

    # Partial metadata: cached absent while the other two are present.
    sink3 = _FakeSink()
    raw3 = _FakeRawClient(
        response=SimpleNamespace(
            usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=0)
        )
    )
    await _build(raw3, sink3).aio.models.generate_content(model="m", contents="x")
    row3 = sink3.rows[0]
    assert (row3.prompt_tokens, row3.output_tokens, row3.cached_tokens) == (5, 0, None)


# --------------------------------------------------------------------------
# 2. Surface attribution
# --------------------------------------------------------------------------


async def test_the_surface_label_identifies_which_feature_made_the_call() -> None:
    """`main.py` shares one client across the assist routers and the embedder,
    so the label has to be re-attachable per call site rather than fixed at
    construction -- otherwise every row reads "backend" and the cost report
    cannot attribute spend to a product surface."""
    response = SimpleNamespace(usage_metadata=_usage())
    raw = _FakeRawClient(response=response)
    sink = _FakeSink()
    suggest = _build(raw, sink, surface=SURFACE_ASSIST_SUGGEST)
    translate = with_surface(suggest, SURFACE_ASSIST_TRANSLATE)

    await suggest.aio.models.generate_content(model="m", contents="x")
    await translate.aio.models.generate_content(model="m", contents="x")

    assert [r.surface for r in sink.rows] == [SURFACE_ASSIST_SUGGEST, SURFACE_ASSIST_TRANSLATE]
    # Re-labelling must not build a second SDK client.
    assert translate.raw is suggest.raw
    # `with_surface` is a no-op on an unmetered client, so call sites need no
    # `if token_metering_enabled` branch.
    unmetered = _build(_FakeRawClient(), _FakeSink(), metering=False)
    assert with_surface(unmetered, SURFACE_ASSIST_TRANSLATE) is unmetered


async def test_the_model_recorded_is_the_model_the_call_actually_used() -> None:
    """Cost is per-model, so the row must carry the model of *that* call, not a
    client-level default."""
    raw = _FakeRawClient(response=SimpleNamespace(usage_metadata=_usage()))
    sink = _FakeSink()
    client = _build(raw, sink)
    await client.aio.models.generate_content(model="gemini-2.5-pro", contents="x")
    await client.aio.models.generate_content(model="gemini-2.5-flash", contents="x")
    assert [r.model for r in sink.rows] == ["gemini-2.5-pro", "gemini-2.5-flash"]


# --------------------------------------------------------------------------
# 3. Embeddings
# --------------------------------------------------------------------------


async def test_an_embedding_call_is_metered() -> None:
    """`VertexEmbedder` uses the SDK's *synchronous* surface from inside
    `asyncio.to_thread`, so the sync path has to meter too.

    `EmbedContentResponse` carries no `usage_metadata` (embeddings are billed
    per character, via `metadata.billable_character_count`), so all three
    counts are honestly `None` -- the row still records that the call happened,
    against which surface and which model.
    """
    response = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])
    raw = _FakeRawClient(response=response)
    sink = _FakeSink()
    client = _build(raw, sink)

    got = await _in_worker_thread(
        lambda: client.models.embed_content(model="text-embedding-004", contents="hello")
    )

    assert got is response
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row.surface == SURFACE_EMBED  # not the client's construction surface
    assert row.model == "text-embedding-004"
    assert (row.prompt_tokens, row.output_tokens, row.cached_tokens) == (None, None, None)


async def _in_worker_thread(fn: Any) -> Any:
    """Run `fn` the way `VertexEmbedder.embed` does -- off the event loop, so
    the sync record path exercises its no-running-loop branch."""
    return await asyncio.to_thread(fn)


async def test_a_sync_call_made_on_the_loop_thread_still_records() -> None:
    """The other branch of the sync record path: a running loop is present, so
    the record is dispatched as a fire-and-forget task instead of blocking."""
    raw = _FakeRawClient(response=SimpleNamespace(usage_metadata=_usage()))
    sink = _FakeSink()
    client = _build(raw, sink)
    client.models.generate_content(model="m", contents="x")
    await asyncio.sleep(0)  # let the dispatched task run
    assert len(sink.rows) == 1


# --------------------------------------------------------------------------
# 4. Streaming
# --------------------------------------------------------------------------


async def test_a_streaming_call_records_usage_from_the_final_chunk() -> None:
    """Streamed usage metadata is cumulative and lands on the last chunk that
    carries it. One row per stream, from that chunk -- not one row per chunk,
    which would multiply spend by the chunk count."""
    chunks = [
        SimpleNamespace(text="he", usage_metadata=None),
        SimpleNamespace(text="llo", usage_metadata=_usage(11, 4, 0)),
        SimpleNamespace(text="!", usage_metadata=_usage(11, 9, 2)),
    ]
    raw = _FakeRawClient(chunks=chunks)
    sink = _FakeSink()
    client = _build(raw, sink)

    seen = []
    async for chunk in await client.aio.models.generate_content_stream(model="m", contents="x"):
        seen.append(chunk)

    # Chunks pass through by identity, in order, untransformed.
    assert seen == chunks
    assert [id(c) for c in seen] == [id(c) for c in chunks]
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert (row.prompt_tokens, row.output_tokens, row.cached_tokens) == (11, 9, 2)


async def test_a_stream_whose_chunks_carry_no_usage_records_none_not_zero() -> None:
    chunks = [SimpleNamespace(text="a"), SimpleNamespace(text="b")]
    raw = _FakeRawClient(chunks=chunks)
    sink = _FakeSink()
    client = _build(raw, sink)
    async for _ in await client.aio.models.generate_content_stream(model="m", contents="x"):
        pass
    assert len(sink.rows) == 1
    assert sink.rows[0].prompt_tokens is None


# --------------------------------------------------------------------------
# 5. Failure paths
# --------------------------------------------------------------------------


async def test_a_failed_call_records_no_usage_but_does_not_raise() -> None:
    """ "Does not raise" means the *metering* raises nothing of its own: the
    SDK's exception must still propagate unchanged, because callers
    (`VertexEmbedder.embed`, the phone bridge's bounded classify) implement
    their own fail-open handling on top of it and swallowing it here would turn
    a logged degradation into a silent wrong answer.

    What metering must not do is invent a row for a call that never produced
    one -- a fabricated 0-token row understates nothing but pollutes the cost
    report with calls that did not happen.
    """
    raw = _FakeRawClient()
    boom = RuntimeError("gemini down")
    raw.aio.models.raises = boom
    sink = _FakeSink()
    client = _build(raw, sink)

    with pytest.raises(RuntimeError) as exc:
        await client.aio.models.generate_content(model="m", contents="x")

    assert exc.value is boom  # the SDK's own exception, not a metering wrapper's
    assert sink.rows == []


async def test_the_sink_failing_never_breaks_the_underlying_call() -> None:
    """The priority, stated: losing a usage record is acceptable, dropping a
    customer's reply is not."""
    response = SimpleNamespace(text="hi", usage_metadata=_usage())
    raw = _FakeRawClient(response=response)
    sink = _FakeSink(fail=True)
    client = _build(raw, sink)

    got = await client.aio.models.generate_content(model="m", contents="x")
    assert got is response

    # Same guarantee on the sync path and the streaming path.
    embed_raw = _FakeRawClient(response=response)
    embed_client = _build(embed_raw, _FakeSink(fail=True))
    assert (
        await _in_worker_thread(lambda: embed_client.models.embed_content(model="m", contents="x"))
        is response
    )

    chunks = [SimpleNamespace(usage_metadata=_usage())]
    stream_client = _build(_FakeRawClient(chunks=chunks), _FakeSink(fail=True))
    out = [
        c
        async for c in await stream_client.aio.models.generate_content_stream(
            model="m", contents="x"
        )
    ]
    assert out == chunks


async def test_the_wrapper_forwards_everything_it_does_not_meter() -> None:
    """A proxy that only forwards what it knows about breaks the first caller
    that uses `client.files`, `client.caches` or `aio.live`. Forward by
    default, intercept by exception."""
    raw = _FakeRawClient()
    client = _build(raw, _FakeSink())
    assert client.aio.live.connect(model="m") == "live-session"
    assert await client.aio.models.count_tokens(contents="x") == "passed-through"
    client.close()
    assert raw.closed is True


# --------------------------------------------------------------------------
# 6. THE ARCHITECTURAL GUARD
# --------------------------------------------------------------------------

# Files that still construct `google.genai.Client` directly instead of going
# through `build_metered_genai_client`. Their Gemini calls are UNMETERED: their
# spend is missing from `v_ai_cost` entirely, which understates the total.
#
# Both are owned by in-flight sibling agents at the time this wrapper landed,
# so Task 2 was barred from editing them; routing them is the wiring wave's
# job. The assertion below is deliberately TWO-SIDED -- the set of direct
# constructions must equal this set exactly -- so that:
#   * adding a new unmetered call site anywhere fails this test, and
#   * routing one of these two through the wrapper ALSO fails this test, until
#     whoever did it deletes the line here. An allowlist that only ever grows
#     is how "temporarily unmetered" becomes permanent.
_UNMETERED_PENDING_WIRING = {
    # main.py's `_build_genai_client`: the shared client behind the three
    # /assist/* routers, VertexEmbedder (live-FAQ + resolved-case index) and
    # the KB paths.
    "main.py",
    # ChatService's raw client for transcription/STT.
    "features/chat/service.py",
}

# The only module allowed to construct the raw client.
_WRAPPER = "platform/metered_genai.py"

_BARE_CLIENT = re.compile(r"(?<![\w.])Client\s*\(")
_QUALIFIED_CLIENT = re.compile(r"(?<![\w.])genai\.Client\s*\(")
_GENAI_IMPORT = re.compile(r"from\s+google\.genai\s+import\s+([^\n#]*)")


def _direct_genai_construction_sites() -> set[str]:
    """Every non-test module under `src/chatbot/` that constructs a
    `google.genai.Client` itself, as a repo-relative posix path."""
    root = Path(__file__).resolve().parents[1]  # src/chatbot
    sites: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        imported: set[str] = set()
        for match in _GENAI_IMPORT.finditer(source):
            imported |= {
                name.strip() for name in match.group(1).replace("(", "").replace(")", "").split(",")
            }
        for line in source.splitlines():
            code = line.split("#", 1)[0]
            if _QUALIFIED_CLIENT.search(code) or (
                "Client" in imported and _BARE_CLIENT.search(code)
            ):
                sites.add(path.relative_to(root).as_posix())
                break
    return sites


async def test_no_gemini_client_is_constructed_outside_the_wrapper() -> None:
    """The whole point of the package.

    Metering at the client boundary is only structurally true if the boundary
    is the *only* door. This scans the source tree rather than asserting
    anything about the wrapper's own behaviour, because a test that only
    exercises the wrapper proves nothing about a sixth call site added next
    month -- which is exactly the failure mode the design calls out
    ("A new backend call site is added and silently unmetered").

    If this test fails with an unexpected extra file: route that call site
    through `build_metered_genai_client(settings, surface=...)`. Do not add it
    to `_UNMETERED_PENDING_WIRING` -- that set is for the two files this task
    was contractually barred from editing, not a suppression list.
    """
    sites = _direct_genai_construction_sites()

    # Sanity: the scanner must actually be able to see a construction, or a
    # broken regex would make this test vacuously green -- the exact failure
    # mode it exists to prevent.
    assert _WRAPPER in sites, (
        "the scanner found no construction in the wrapper itself, so it is not "
        "detecting `google.genai.Client(...)` at all and this guard is vacuous"
    )

    assert sites - {_WRAPPER} == _UNMETERED_PENDING_WIRING, (
        "the set of Gemini clients built outside the metering wrapper changed. "
        "Extra entries are UNMETERED spend missing from v_ai_cost -- route them "
        "through build_metered_genai_client. Missing entries mean a pending site "
        "was wired: delete it from _UNMETERED_PENDING_WIRING."
    )


def test_the_metered_phone_call_sites_are_actually_routed() -> None:
    """Reachability, not just absence: the two sites this task *could* edit
    must genuinely call the wrapper, not merely have stopped calling
    `Client(...)`."""
    for module in (
        "chatbot/features/chat/phone/bridge.py",
        "chatbot/features/chat/phone/gemini_live.py",
    ):
        source = (Path(__file__).resolve().parents[2] / module).read_text(encoding="utf-8")
        assert "build_metered_genai_client" in source, module


# --------------------------------------------------------------------------
# 7. Flag off
# --------------------------------------------------------------------------


async def test_the_flag_off_records_nothing_and_adds_no_latency() -> None:
    """Off is the default, and off must be byte-identical to pre-P8: the raw
    SDK client comes back *by identity*, so there is no proxy object on the
    model path at all -- no per-call branch to execute, no attribute lookups to
    forward, no sink to consult, no extra I/O. This is a stronger claim than
    "the wrapper checks a flag and returns early", and it is the reason the
    check lives in the factory rather than in the call path.
    """
    raw = _FakeRawClient(response=SimpleNamespace(usage_metadata=_usage()))
    sink = _FakeSink()

    client = _build(raw, sink, metering=False)

    assert client is raw
    assert not isinstance(client, MeteredGenaiClient)
    await client.aio.models.generate_content(model="m", contents="x")
    await _in_worker_thread(lambda: client.models.embed_content(model="m", contents="x"))
    assert sink.rows == []


async def test_the_default_settings_have_metering_off() -> None:
    """`Settings()` reads os.environ, so assert the field default off the model
    rather than off an instance -- a bare `Settings()` would pass vacuously on
    the flags-on gate run while asserting the opposite of its own name."""
    assert Settings.model_fields["token_metering_enabled"].default is False


async def test_a_client_that_cannot_be_constructed_fails_open_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot must not break because Gemini credentials are absent -- the
    previous per-site code returned None and so does the wrapper, flag on or
    off. `_construct_raw_client` itself swallows the SDK's exception; here we
    assert the factory above it does not then wrap a None."""
    monkeypatch.setattr(metered_genai, "_construct_raw_client", lambda _s: None)
    assert build_metered_genai_client(_settings(), surface=SURFACE_ASSIST_SUGGEST) is None
    assert (
        build_metered_genai_client(_settings(metering=False), surface=SURFACE_ASSIST_SUGGEST)
        is None
    )


async def test_the_raw_constructor_swallows_sdk_failures() -> None:
    """The one place `google.genai.Client` is built must never raise out --
    every previous per-site constructor was fail-open and the wrapper inherits
    that contract."""

    class _Raising:
        def __init__(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("boom")

    original = google.genai.Client
    try:
        google.genai.Client = _Raising  # type: ignore[misc, assignment]
        assert metered_genai._construct_raw_client(_settings()) is None
    finally:
        google.genai.Client = original  # type: ignore[misc]
