"""Session-service decorator that keeps inline media out of stored sessions.

ADK appends the user ``Content`` to the session *before* the agent runs, and
every LLM request is then rebuilt from those session events — so an inline
audio/image/video blob is not just persisted once, it is replayed to Gemini on
every subsequent turn of the same session. That is fatal here because the
chat-agent sessions are long-lived (``crm-{conversation_id}``, one per Chatwoot
conversation, never rotated):

* ``FirestoreSessionService.append_event`` rewrites the whole session as a
  single document via ``model_dump(mode="json")``, which base64-encodes the
  blob (~1.335x). A ~785 KB video already exceeds Firestore's 1 MiB document
  limit and the write raises ``InvalidArgument`` — the feature simply does not
  work on Firestore tenants.
* The in-memory store never evicts, so every blob is pinned for the process
  lifetime inside a 768 MB container.

ADK's own answer (``SaveFilesAsArtifactsPlugin`` / the deprecated
``save_input_blobs_as_artifacts``) replaces the blob with an artifact
*reference* before the model ever sees it, which needs an artifact service with
a model-accessible URI (GCS) — infrastructure this deployment does not have,
and without it the model loses the media entirely.

So this decorator splits the two lifetimes instead. The blob has to be in
``session.events`` for the current invocation (that is where the request is
built from), but it must never be in the *stored* session. On append, the inner
store is handed a copy whose inline parts are replaced by a short text
placeholder, and the live in-memory session is then given the original back so
this turn still reaches the model with its media intact. Any later append in
the same invocation re-strips first, so a store that rewrites the entire
session (Firestore) never serializes a blob either.

Instances are per-run (see ``OrchestratorService._default_runner_factory``), so
the map of full events is bounded by a single invocation.
"""

from __future__ import annotations

from typing import Any

import structlog
from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse
from google.genai import types

_log = structlog.get_logger(__name__)

# What a stripped-out blob leaves behind in the stored history. A placeholder
# rather than nothing so (a) the model still knows the customer sent media on
# that turn, and (b) a media-only user message (the voice channel sends exactly
# one audio part and no text) never degrades to a part-less Content.
_PLACEHOLDER = "[{mime_type} attachment sent earlier in this conversation]"


def strip_inline_blobs(event: Event) -> Event | None:
    """Return a blob-free copy of ``event``, or ``None`` if it carries no blob.

    The copy keeps the same event ``id`` so the two can be paired up again.
    """
    content = event.content
    if content is None or not content.parts:
        return None
    if not any(part.inline_data is not None for part in content.parts):
        return None

    new_parts: list[types.Part] = []
    for part in content.parts:
        if part.inline_data is None:
            new_parts.append(part)
            continue
        mime_type = part.inline_data.mime_type or "media"
        new_parts.append(types.Part(text=_PLACEHOLDER.format(mime_type=mime_type)))
    return event.model_copy(update={"content": types.Content(role=content.role, parts=new_parts)})


class BlobFreeSessionService(BaseSessionService):
    """Delegates to ``inner`` but never lets it store an inline media blob."""

    def __init__(self, inner: BaseSessionService) -> None:
        self._inner = inner
        # event id -> (stripped copy handed to the store, original with blob)
        self._pairs: dict[str, tuple[Event, Event]] = {}

    # -- the one method that actually does something -----------------------

    async def append_event(self, session: Session, event: Event) -> Event:
        stripped = strip_inline_blobs(event)

        # Swap every already-tracked event in the live session back to its
        # stripped form BEFORE delegating — a store that rewrites the whole
        # session on each append (Firestore) would otherwise re-serialize a
        # blob appended earlier in this same run. This has to happen for
        # blob-free events too: the model's own response event triggers such a
        # rewrite right after the user's media event.
        self._swap(session, to_full=False)
        try:
            await self._inner.append_event(session=session, event=stripped or event)
        finally:
            if stripped is not None:
                self._pairs[event.id] = (stripped, event)
            # ...and restore the originals so the rest of THIS invocation
            # still sends the media to the model.
            self._swap(session, to_full=True)
        if stripped is not None:
            _log.debug(
                "session_inline_blobs_stripped",
                session_id=session.id,
                event_id=event.id,
            )
        return event

    def _swap(self, session: Session, *, to_full: bool) -> None:
        """Replace tracked events in the live session with their full or
        stripped counterpart (identity-preserving for untracked events)."""
        if not self._pairs:
            return
        for index, existing in enumerate(session.events):
            pair = self._pairs.get(existing.id)
            if pair is None:
                continue
            session.events[index] = pair[1] if to_full else pair[0]

    # -- plain delegation ---------------------------------------------------

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        return await self._inner.create_session(
            app_name=app_name, user_id=user_id, state=state, session_id=session_id
        )

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        return await self._inner.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id, config=config
        )

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        return await self._inner.list_sessions(app_name=app_name, user_id=user_id)

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        await self._inner.delete_session(app_name=app_name, user_id=user_id, session_id=session_id)

    async def get_user_state(self, *, app_name: str, user_id: str) -> dict[str, Any]:
        return await self._inner.get_user_state(app_name=app_name, user_id=user_id)

    async def flush(self) -> None:
        # BaseSessionService.flush carries no return annotation upstream.
        await self._inner.flush()  # type: ignore[no-untyped-call]
