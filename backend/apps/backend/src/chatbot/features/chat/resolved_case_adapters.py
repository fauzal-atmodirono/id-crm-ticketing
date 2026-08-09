"""The two collaborators the resolved-case index needs and nobody had built.

`resolved_case_index.py` (P7 task 9) declares four optional ports and ships a
production implementation for two of them (`PgResolvedCaseRepository`, and the
embedder is the existing `VertexEmbedder`). The other two had no
implementation anywhere in the tree, which is why the index could be enabled
and still never store anything:

1. **`SummarizerPort`** — the summary must come from the EXISTING
   `POST /assist/summarize` logic, not a second summarisation prompt. That
   logic is a closure (`summarize`) inside `build_assist_router`, so it cannot
   be imported.
2. **`TranscriptPort`** — fetching a conversation's messages. `TicketingPort`
   and `ConversationLogPort` have no generic transcript read; the closest,
   `ChatwootAdapter.get_latest_public_comment`, returns the single latest
   incoming message.

Why `AssistSummarizeAdapter` reaches into the router object rather than
extracting the closure
----------------------------------------------------------------------
The obvious refactor -- lift `summarize()` out of `build_assist_router` into a
module-level function and call it from both places -- edits
`features/assist/router.py`. That file had just taken a PII-instruction fix
(commit f4d6258) from a concurrently-running task, and the one hard
requirement on this work is that `/assist/summarize`'s behaviour does not
change. So this adapter takes the *route object* `build_assist_router` already
returns and awaits its endpoint function in-process. That is not a stylistic
preference; it is what makes the "no behaviour change" claim checkable:

  * there is exactly one summariser prompt, one persona application, one model
    resolution and one Gemini call shape in the codebase, because this adapter
    executes the very function the HTTP route executes;
  * `_SUMMARIZE_SYSTEM` -- including the PII-omission sentence the resolved-case
    index's mitigation depends on -- cannot drift between the agent-triggered
    and the automatic path, because there is no second copy to drift;
  * `features/assist/router.py` is byte-for-byte untouched by this work.

What it does NOT go through is FastAPI: no HTTP request, no middleware, no
CORS, no request validation beyond constructing `SummarizeRequest` here (which
is the same model the route validates into). The `x-api-key` argument is
passed positionally to the endpoint's own `_authorize`, so the route's auth
logic still runs -- with the process's own configured key, since the caller
here is the process itself.

PII, since this adapter is the thing that puts a summary into a vector store:
the summariser prompt asks the model to omit customer identifiers, and that
request is all it is. Nothing here -- and nothing in `resolved_case_index.py`,
which stores the result -- inspects, redacts or validates the returned text.
The instruction itself survives every wiring path verbatim (`_apply_persona`
PREPENDS an operator persona prefix and has no branch that removes or replaces
the task prompt; it does not read the persona's `instructions` field at all),
so the residual risk is that an operator's own guardrails, sitting earlier in
the same prompt, tell the model the opposite. That is a
model-instruction-following risk, not a code path that drops the sentence --
stated precisely because this run's ledger briefly recorded the stronger claim
that persona `instructions` replaced the prompt wholesale, which is not what
this code does. See the blocked-work register (gap R16, blocked on Q7) and
`resolved_case_index.py`'s own PII section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog
from fastapi.routing import APIRoute

from chatbot.features.assist.router import SummarizeRequest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import APIRouter

    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# The path `build_assist_router` registers the summariser on (its APIRouter
# carries prefix="/assist"). Asserted by name rather than by position so a
# reordering of that file's three endpoints cannot silently bind the wrong one.
SUMMARIZE_PATH = "/assist/summarize"

# Chatwoot message_type values. 0/1 are the customer/agent turns; 2 is an
# activity entry ("Conversation was resolved by ...") and 3 a template, neither
# of which is anybody's utterance.
_INCOMING = 0
_OUTGOING = 1

# How many trailing turns a summary is built from. A cap exists because a long
# WhatsApp thread would otherwise put the whole history into one prompt on
# every resolve; trailing rather than leading because the resolution is at the
# end. Matches nothing else in the codebase -- there is no existing convention
# to follow, /assist/summarize is called from the composer with whatever the
# fork sends.
DEFAULT_MAX_MESSAGES = 60


class SummarizeEndpoint(Protocol):
    """`build_assist_router`'s `summarize` closure, as seen from outside.

    Its second parameter is the `x-api-key` header FastAPI would have supplied;
    called directly, we supply it ourselves.
    """

    async def __call__(
        self, req: SummarizeRequest, x_api_key: str | None = None
    ) -> dict[str, Any]: ...


def _iter_api_routes(router: Any) -> Iterator[APIRoute]:
    """Every APIRoute reachable from `router`, including through nested includes.

    FastAPI 0.137 keeps an included router as a wrapper object on the parent's
    `routes` list rather than flattening its routes into it, so a caller that
    hands us a whole `FastAPI` app -- instead of the router `build_assist_router`
    returned -- would otherwise find nothing. The descent is duck-typed
    (`original_router` is FastAPI-internal): a version that flattens is handled
    by the first branch, and an unrecognised route shape degrades to "not found"
    rather than to an AttributeError during boot.
    """
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_api_routes(nested)


def find_summarize_endpoint(router: APIRouter) -> SummarizeEndpoint | None:
    """Locate the live POST /assist/summarize endpoint on a built router.

    Returns None rather than raising: a caller that cannot find it is expected
    to log and carry on with no summariser, which is exactly what the resolved-
    case indexer does with `summarizer=None` (log and no-op, never crash a
    resolve).
    """
    for route in _iter_api_routes(router):
        methods = route.methods or set()
        if route.path == SUMMARIZE_PATH and "POST" in methods:
            return cast("SummarizeEndpoint", route.endpoint)
    return None


class AssistSummarizeAdapter:
    """`SummarizerPort` over the live `/assist/summarize` route.

    Two-step construction is deliberate. `main.py` must hand a summariser to
    `build_chat_router` (the resolve hook lives in the chat router) well before
    it builds the assist router, so the endpoint is bound afterwards via
    `bind()`. This mirrors the `EscalationNotifier` injection `main.py` already
    does, and rests on the same argument: nothing calls `summarize()` until an
    async request handler runs, which is long after `bootstrap_application()`
    has returned.

    Unbound, or misconfigured, or on any failure, `summarize()` returns `""`.
    The indexer treats an empty summary as "nothing to do" and stores nothing,
    so a broken summariser degrades to today's behaviour (no auto-summary, no
    index write) instead of turning a resolve into an error.
    """

    def __init__(self, settings: Settings, *, endpoint: SummarizeEndpoint | None = None) -> None:
        self._settings = settings
        self._endpoint = endpoint

    def bind(self, endpoint: SummarizeEndpoint | None) -> bool:
        """Attach the endpoint found by `find_summarize_endpoint`.

        Returns whether anything was bound, so the caller can log the negative
        case rather than discovering it one resolve at a time.
        """
        self._endpoint = endpoint
        return endpoint is not None

    @property
    def is_bound(self) -> bool:
        return self._endpoint is not None

    async def summarize(self, conversation_id: str, messages: list[str]) -> str:
        if self._endpoint is None:
            _log.warning("resolved_case_summarizer_unbound", conversation_id=conversation_id)
            return ""
        if not messages:
            # SummarizeRequest requires at least one message, and a summary of
            # nothing is not worth a Gemini call. An empty transcript is the
            # normal state on a Chatwoot-disabled or misconfigured tenant.
            _log.info("resolved_case_summarizer_empty_transcript", conversation_id=conversation_id)
            return ""
        key = self._settings.proton_backend_key
        if not key:
            # The route answers 503 without a key. Reporting that as a distinct
            # log line beats letting it read as a model failure -- it is an
            # operator configuration gap with an obvious fix.
            _log.warning("resolved_case_summarizer_no_backend_key", conversation_id=conversation_id)
            return ""
        try:
            result = await self._endpoint(
                SummarizeRequest(conversation_id=conversation_id or "unknown", messages=messages),
                key,
            )
        except Exception as exc:
            _log.warning(
                "resolved_case_summarizer_failed",
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ""
        summary = result.get("summary") if isinstance(result, dict) else None
        return str(summary or "")


class ChatwootTranscriptAdapter:
    """`TranscriptPort` over `GET /conversations/{id}/messages`.

    Takes `ChatwootAdapter._request` (the same injection
    `ChatwootAttachmentFetcher` takes, for the same reason: this module then
    needs no Chatwoot URL, token or transport knowledge, and a test can fake
    one callable). `_request` is itself fail-open -- it returns None on any
    error, including Chatwoot being disabled -- so a transcript read failure
    surfaces here as an empty list, and the summariser declines to summarise
    nothing.

    Output shape is `"Customer: ..."` / `"Agent: ..."` lines, which is what
    `/assist/summarize`'s prompt and `_retrieval_query` already expect from the
    fork's composer. Two exclusions are load-bearing rather than tidying:

    * **private notes are dropped.** The auto-summary this feeds is itself
      posted as a private note, so including them would feed the previous
      resolution's summary back into the next one -- a case resolved, reopened
      and resolved again would summarise a summary.
    * **activity entries are dropped.** "Conversation was resolved by Aisyah"
      is Chatwoot narrating itself, not a turn, and it appears on precisely the
      event that triggers this read.
    """

    def __init__(self, request: Any, *, max_messages: int = DEFAULT_MAX_MESSAGES) -> None:
        self._request = request
        self._max_messages = max_messages

    async def fetch_transcript(self, conversation_id: str) -> list[str]:
        try:
            data = await self._request("GET", f"/conversations/{conversation_id}/messages", None)
        except Exception as exc:
            _log.warning(
                "resolved_case_transcript_failed",
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )
            return []
        payload: Any = data.get("payload") if isinstance(data, dict) else data
        if not isinstance(payload, list):
            return []

        lines: list[str] = []
        for message in payload:
            if not isinstance(message, dict):
                continue
            if message.get("private"):
                continue
            try:
                message_type = int(message.get("message_type"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if message_type not in (_INCOMING, _OUTGOING):
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            speaker = "Customer" if message_type == _INCOMING else "Agent"
            lines.append(f"{speaker}: {content}")
        return lines[-self._max_messages :]
