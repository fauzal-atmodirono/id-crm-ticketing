"""The metered google-genai client: the one place a Gemini client is built.

**Why metering lives at the client boundary and not at the call sites.** The
backend calls Gemini from at least five places -- the three `/assist/*` routers
(suggest, translate, copilot), `ChatService`'s transcription client, the
`VertexEmbedder` behind live-FAQ and the resolved-case index, the phone
bridge's post-call transcript classifier, and the phone Live session. A
per-call-site change would meter exactly those and then be silently incomplete
the first time someone adds a sixth. The failure is invisible: the code works,
the tests pass, and the cost report is quietly missing a line item -- which
**understates spend**, in the direction that looks good, for however many
months it takes someone to notice.

So the metering is structural. `build_metered_genai_client` is the only
function in the backend that constructs `google.genai.Client`, and
`test_metered_genai.py::test_no_gemini_client_is_constructed_outside_the_wrapper`
scans the source tree to keep it that way: a new direct construction anywhere
under `src/chatbot/` fails that test. A new call site is metered by
construction rather than by remembering.

**Flag-off is byte-identical, not merely cheap.** With
`token_metering_enabled` off (the default) this function returns the raw SDK
client object itself -- no proxy, no sink, no wrapper allocation, nothing on
the model path to add latency or I/O to. There is no "metering disabled"
branch executing per call, because with the flag off there is no wrapper.

**The wrapper wraps; it does not transform.** With the flag on, the proxy
forwards every attribute it does not intercept straight through, returns the
SDK's own response object by identity, yields the SDK's own stream chunks by
identity, and lets the SDK's own exceptions propagate unchanged. Metering is
bookkeeping wrapped around a customer's conversation: losing a usage record
costs a row in a cost report, while swallowing an exception or rewriting a
response would cost the customer their reply. Every record path is therefore
wrapped in its own `try/except` (defence in depth -- `TokenUsageSink`'s
contract says "never raises", but a third-party sink is not bound by a
docstring).

**Surfaces, and why they are re-labellable.** `main.py` builds one client and
shares it across the three assist routers and the embedder, so a
construction-time label alone would be too coarse to attribute spend to a
product surface. `with_surface(client, "assist.translate")` returns a
cheap re-labelled view over the *same* SDK client (no second connection), and
is a no-op on an unmetered client so call sites need no flag branch.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.metrics.token_usage import (
    TokenUsageSink,
    build_token_usage_sink,
    token_usage_from_response,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# This service's name in a `TokenUsage` row. The `agent/` service records
# "agent" onto `ai_actions`; the cost view groups by this column.
SERVICE = "backend"

# The surface labels this codebase uses (design §3.1). Strings, not an enum,
# because the warehouse column is a string and a future surface must not
# require a code change in two places.
SURFACE_ASSIST_SUGGEST = "assist.suggest"
SURFACE_ASSIST_TRANSLATE = "assist.translate"
SURFACE_ASSIST_COPILOT = "assist.copilot"
SURFACE_CHAT_TURN = "chat.turn"
SURFACE_EMBED = "embed"
SURFACE_PHONE_LIVE = "phone.live"
SURFACE_PHONE_CLASSIFY = "phone.classify"

# Recorded when a call omits `model=` (no real call site does; a stub might).
# Deliberately a visible sentinel rather than an empty string, so an unpriced
# row in the cost report is obviously a metering gap rather than a new model.
_UNKNOWN_MODEL = "unknown"


def _swallow(task: asyncio.Task[None]) -> None:
    """Retrieve a fire-and-forget record task's exception so it neither
    propagates nor produces an unretrieved-exception warning."""
    try:
        task.result()
    except Exception as e:  # pragma: no cover - defensive
        _log.error("token_metering_record_failed", error=str(e))


class _Recorder:
    """Builds and dispatches `TokenUsage` rows for one surface.

    Holds the sink plus the labels. Split out from the proxy classes so
    `with_surface` can produce a re-labelled view without re-wrapping the SDK
    client, and so both the sync and async call paths share one fail-open
    implementation.
    """

    def __init__(self, *, sink: TokenUsageSink, service: str, surface: str) -> None:
        self._sink = sink
        self._service = service
        self._surface = surface

    @property
    def surface(self) -> str:
        return self._surface

    def relabel(self, surface: str) -> _Recorder:
        return _Recorder(sink=self._sink, service=self._service, surface=surface)

    def _usage(self, response: Any, model: str, surface: str | None) -> Any:
        return token_usage_from_response(
            response,
            service=self._service,
            surface=surface or self._surface,
            model=model,
        )

    async def arecord(self, response: Any, model: str, surface: str | None = None) -> None:
        """Record from an async call path. Never raises."""
        try:
            await self._sink.record(self._usage(response, model, surface))
        except Exception as e:
            _log.error(
                "token_metering_record_failed", surface=surface or self._surface, error=str(e)
            )

    def record(self, response: Any, model: str, surface: str | None = None) -> None:
        """Record from a *synchronous* call path. Never raises, never blocks
        the event loop.

        The SDK's sync surface (`client.models.embed_content`) is always called
        from a worker thread here -- `VertexEmbedder.embed` dispatches it via
        `asyncio.to_thread` precisely so the loop stays free -- so there is no
        running loop to await on. In that thread `asyncio.run` is the correct
        way to drive the async sink, and blocking the worker thread (not the
        loop) is the intended cost. If a running loop *is* present (a sync call
        made straight from the loop thread), the record is dispatched as a
        fire-and-forget task instead, so metering cannot stall the caller.
        """
        try:
            usage = self._usage(response, model, surface)
            try:
                loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None:
                asyncio.run(self._sink.record(usage))
            else:
                _swallow_later = loop.create_task(self._sink.record(usage))
                _swallow_later.add_done_callback(_swallow)
        except Exception as e:
            _log.error(
                "token_metering_record_failed", surface=surface or self._surface, error=str(e)
            )


def _model_of(kwargs: dict[str, Any]) -> str:
    model = kwargs.get("model")
    return model if isinstance(model, str) and model else _UNKNOWN_MODEL


class _MeteredModels:
    """Proxy over `client.models` / `client.aio.models`.

    Intercepts the three token-bearing methods and forwards everything else
    (`count_tokens`, `list`, ...) untouched. `is_async` selects between the
    SDK's sync and async surfaces, which have the same names but different
    calling conventions -- the async `generate_content_stream` is a coroutine
    that *returns* an async iterator, not an async generator.
    """

    def __init__(
        self, raw: Any, recorder: _Recorder, *, is_async: bool, embed_surface: str
    ) -> None:
        self._raw = raw
        self._recorder = recorder
        self._is_async = is_async
        self._embed_surface = embed_surface

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    # --- generate_content ------------------------------------------------

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        if self._is_async:
            return self._agenerate_content(*args, **kwargs)
        response = self._raw.generate_content(*args, **kwargs)
        self._recorder.record(response, _model_of(kwargs))
        return response

    async def _agenerate_content(self, *args: Any, **kwargs: Any) -> Any:
        response = await self._raw.generate_content(*args, **kwargs)
        await self._recorder.arecord(response, _model_of(kwargs))
        return response

    # --- embed_content ---------------------------------------------------

    def embed_content(self, *args: Any, **kwargs: Any) -> Any:
        if self._is_async:
            return self._aembed_content(*args, **kwargs)
        response = self._raw.embed_content(*args, **kwargs)
        self._recorder.record(response, _model_of(kwargs), self._embed_surface)
        return response

    async def _aembed_content(self, *args: Any, **kwargs: Any) -> Any:
        response = await self._raw.embed_content(*args, **kwargs)
        await self._recorder.arecord(response, _model_of(kwargs), self._embed_surface)
        return response

    # --- generate_content_stream -----------------------------------------

    def generate_content_stream(self, *args: Any, **kwargs: Any) -> Any:
        if self._is_async:
            return self._agenerate_content_stream(*args, **kwargs)
        return self._stream(self._raw.generate_content_stream(*args, **kwargs), _model_of(kwargs))

    async def _agenerate_content_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raw_iter = await self._raw.generate_content_stream(*args, **kwargs)
        return self._astream(raw_iter, _model_of(kwargs))

    async def _astream(self, raw_iter: Any, model: str) -> AsyncIterator[Any]:
        """Yield the SDK's own chunks by identity, recording once at the end.

        Streamed usage metadata is cumulative and arrives on the last chunk
        that carries it, so the wrapper keeps the most recent chunk with a
        `usage_metadata` and falls back to the final chunk (which then records
        `None` for all three -- honest, rather than a fabricated 0). An
        abandoned stream records nothing: we cannot know the totals of a
        response we never received.
        """
        last: Any = None
        with_usage: Any = None
        async for chunk in raw_iter:
            last = chunk
            if getattr(chunk, "usage_metadata", None) is not None:
                with_usage = chunk
            yield chunk
        final = with_usage if with_usage is not None else last
        if final is not None:
            await self._recorder.arecord(final, model)

    def _stream(self, raw_iter: Any, model: str) -> Iterator[Any]:
        last: Any = None
        with_usage: Any = None
        for chunk in raw_iter:
            last = chunk
            if getattr(chunk, "usage_metadata", None) is not None:
                with_usage = chunk
            yield chunk
        final = with_usage if with_usage is not None else last
        if final is not None:
            self._recorder.record(final, model)


class _MeteredAio:
    """Proxy over `client.aio`. Wraps `.models`; forwards `.live`, `.files`
    and the rest untouched.

    `.live` is deliberately *not* metered: a Live session's token accounting
    arrives asynchronously in server messages, not on a response object, so
    there is nothing here to read. Routing the phone Live path through this
    wrapper still buys the structural guarantee -- when Live usage is
    captured, there is exactly one place to add it.
    """

    def __init__(self, raw: Any, recorder: _Recorder, *, embed_surface: str) -> None:
        self._raw = raw
        self._recorder = recorder
        self._embed_surface = embed_surface

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    @property
    def models(self) -> _MeteredModels:
        return _MeteredModels(
            self._raw.models, self._recorder, is_async=True, embed_surface=self._embed_surface
        )


class MeteredGenaiClient:
    """Transparent proxy over `google.genai.Client` that records token usage.

    Only constructed when `token_metering_enabled` is on -- see the module
    docstring for why the off path returns the raw client instead.
    """

    def __init__(
        self,
        raw: Any,
        *,
        sink: TokenUsageSink,
        surface: str,
        service: str = SERVICE,
        embed_surface: str = SURFACE_EMBED,
    ) -> None:
        self._raw = raw
        self._recorder = _Recorder(sink=sink, service=service, surface=surface)
        self._embed_surface = embed_surface

    @property
    def raw(self) -> Any:
        """The underlying SDK client, for the rare caller that needs identity."""
        return self._raw

    @property
    def surface(self) -> str:
        return self._recorder.surface

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    @property
    def models(self) -> _MeteredModels:
        return _MeteredModels(
            self._raw.models, self._recorder, is_async=False, embed_surface=self._embed_surface
        )

    @property
    def aio(self) -> _MeteredAio:
        return _MeteredAio(self._raw.aio, self._recorder, embed_surface=self._embed_surface)

    def with_surface(self, surface: str) -> MeteredGenaiClient:
        """A re-labelled view over the same SDK client (no new connection)."""
        view = MeteredGenaiClient.__new__(MeteredGenaiClient)
        view._raw = self._raw
        view._recorder = self._recorder.relabel(surface)
        view._embed_surface = self._embed_surface
        return view


def with_surface(client: Any, surface: str) -> Any:
    """Re-label `client`'s surface if it is metered; otherwise return it as-is.

    Lets a call site attribute its own spend without branching on
    `token_metering_enabled` -- an unmetered client passes straight through.
    """
    if isinstance(client, MeteredGenaiClient):
        return client.with_surface(surface)
    return client


def _construct_raw_client(settings: Settings) -> Any | None:
    """Build the raw SDK client (ADC / Vertex). Fail-open: returns `None` when
    the SDK or credentials are unavailable, so wiring degrades rather than
    breaking boot.

    **This is the only `google.genai.Client(...)` construction in the
    backend**, and the architectural guard test exists to keep it that way.
    """
    try:
        from google.genai import Client  # noqa: PLC0415 — lazy: boot without the SDK

        if settings.google_genai_use_vertexai:
            return Client(
                vertexai=True,
                project=settings.vertex_project_id,
                location=settings.vertex_location,
            )
        return Client()
    except Exception as e:
        _log.error("genai_client_construction_failed", error=str(e))
        return None


def build_metered_genai_client(
    settings: Settings,
    *,
    surface: str,
    sink: TokenUsageSink | None = None,
    client: Any | None = None,
    service: str = SERVICE,
    embed_surface: str = SURFACE_EMBED,
) -> Any | None:
    """Build the Gemini client for one call site, metered if the flag is on.

    Args:
        settings: reads `token_metering_enabled`, the Vertex/ADC switch, and
            (via `build_token_usage_sink`) `metrics_provider`.
        surface: the product surface this client's calls are attributed to;
            see the `SURFACE_*` constants. Re-labellable per call site with
            `with_surface`.
        sink: injected in tests. Defaults to `build_token_usage_sink(settings)`
            -- which is `NoOpTokenUsageSink` unless the tenant streams metrics
            to BigQuery.
        client: an already-constructed raw client (tests, or a call site that
            must reuse one). When given, no SDK client is constructed.
        service: the `TokenUsage.service` label; "backend" here.
        embed_surface: the label for `embed_content` calls, which are
            embeddings whatever the client's other calls are.

    Returns:
        With the flag off: the raw client object itself, unwrapped, so the
        model path is byte-identical to pre-P8. With it on: a
        `MeteredGenaiClient` proxy. `None` if the raw client could not be
        constructed (fail-open, matching the previous per-site behaviour).
    """
    raw = client if client is not None else _construct_raw_client(settings)
    if raw is None:
        return None
    if not settings.token_metering_enabled:
        return raw
    return MeteredGenaiClient(
        raw,
        sink=sink if sink is not None else build_token_usage_sink(settings),
        surface=surface,
        service=service,
        embed_surface=embed_surface,
    )
