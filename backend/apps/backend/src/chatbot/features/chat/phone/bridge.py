"""Orchestrates a single phone call: Twilio Media Stream ⇄ Gemini Live."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from chatbot.features.chat.csat import record_csat_on_ticket
from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from chatbot.features.chat.phone.handoff_csat_tools import parse_csat_score
from chatbot.features.chat.phone.kb_tool import dispatch_kb_search
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ToolCall,
)
from chatbot.features.chat.phone.transcript_sink import TranscriptSink
from chatbot.features.chat.ports import ConversationLogResult

if TYPE_CHECKING:
    from chatbot.features.chat.phone.gemini_live import LiveSession
    from chatbot.features.chat.ports import ConversationLogPort, KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Cap on the two things finalize() awaits on the websocket teardown path: the
# in-flight call-start ticket-create task, and the flush-worker drain. Both
# are, worst case, a handful of sequential Chatwoot HTTP calls at the
# adapter's own 10s-per-call timeout -- without a ceiling, a blackholed
# Chatwoot at hangup could hold the teardown path open for 10s per queued
# block (or ~30s for the ticket create alone) instead of bounded to this.
_FLUSH_DRAIN_TIMEOUT_SECONDS = 10.0


class PhoneBridge:
    def __init__(
        self,
        live: LiveSession,
        knowledge_port: KnowledgePort,
        conversation_log_port: ConversationLogPort,
        send_twilio: Callable[[dict[str, object]], Awaitable[None]],
        settings: Settings,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._live = live
        self._knowledge = knowledge_port
        self._log_port = conversation_log_port
        self._send_twilio = send_twilio
        self._settings = settings
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.handoff: dict[str, str] | None = None
        self.csat_score: int | None = None
        # Ticket created at call start (Task 3), not just at hangup, so a
        # live transcript has somewhere to stream into. None until the
        # "start" event fires with the flag on, or (fallback) until
        # finalize() creates it the old way.
        self.ticket_id: str | None = None
        self._sink = TranscriptSink(
            flush_seconds=settings.phone_transcript_flush_seconds,
            now=clock or time.monotonic,
        )
        # Queued live-transcript blocks are posted by a single detached
        # worker task, one at a time, so Chatwoot latency never stalls the
        # audio pump AND blocks land in the order they were spoken (posting
        # each block as its own concurrent task would race on the network).
        self._flush_queue: asyncio.Queue[str] = asyncio.Queue()
        self._flush_worker: asyncio.Task[None] | None = None
        # Ticket creation at "start" is fire-and-forget (see handle_twilio):
        # a blackholed Chatwoot must not hold up Gemini's greeting or the
        # first bytes of caller audio. finalize() awaits this task so it
        # never races finalize()'s own decision to reuse vs. fall back.
        self._ticket_create_task: asyncio.Task[None] | None = None
        # True once ANY live block has been successfully posted (during
        # pump() or finalize()'s own forced flush) -- lets finalize() avoid
        # posting the whole transcript a second time when it's already in
        # the ticket turn-by-turn.
        self._live_blocks_posted = False
        # True once ANY live block failed to post (exception, a non-OK
        # ConversationLogResult, or no ticket to post to). Deliberately
        # tracked SEPARATELY from _live_blocks_posted: some blocks can
        # succeed while another fails (e.g. a transient blip mid-call), and
        # in that case the failed turn would be permanently missing from
        # the ticket if finalize() trusted _live_blocks_posted alone and
        # skipped the full-transcript fallback. One duplicated transcript
        # on a degraded call is a far better outcome than a silently
        # missing turn on a customer's record.
        self._live_blocks_failed = False

    async def handle_twilio(self, msg: dict[str, object]) -> None:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start")
            if isinstance(start, dict):
                sid = start.get("streamSid")
                self.stream_sid = str(sid) if sid is not None else None
                csid = start.get("callSid")
                self.call_sid = str(csid) if csid is not None else None
                if (
                    self.call_sid
                    and self.ticket_id is None
                    and (self._ticket_create_task is None or self._ticket_create_task.done())
                    and self._settings.phone_transcript_live_enabled
                ):
                    # Fire-and-forget: NEVER await this here. This branch also
                    # runs mid-call if Twilio resends "start" on a reconnect,
                    # which is inside from_twilio()'s receive loop feeding
                    # caller audio to Gemini -- an inline await would stall
                    # that audio, not just call setup. ensure_conversation_
                    # ticket can take up to ~30s (several sequential HTTP
                    # calls) on a blackholed Chatwoot; that must never show up
                    # as dead air. The `ticket_id is None` guard alone isn't
                    # enough to stop a second concurrent create on a rapid
                    # reconnect (ticket_id stays None until the task actually
                    # runs), so also check the task itself isn't still
                    # in-flight.
                    self._ticket_create_task = asyncio.create_task(
                        self._create_ticket_at_start(self.call_sid)
                    )
        elif event == "media":
            media = msg.get("media")
            if isinstance(media, dict):
                payload = media.get("payload")
                if payload:
                    pcm = mulaw8k_to_pcm16k(base64.b64decode(str(payload)))
                    await self._live.send_audio(pcm)
        # "stop"/"connected" need no action here; finalize() runs on socket close.

    async def _create_ticket_at_start(self, call_sid: str) -> None:
        """Create the conversation's ticket as soon as the call starts.

        Runs as a detached background task (see handle_twilio) so a slow or
        blackholed Chatwoot never delays the greeting or caller audio.
        ``ensure_conversation_ticket`` is keyed on session_id (find-or-
        create), so a retried/duplicated "start" event, or finalize()'s own
        fallback create, safely resolves to the SAME ticket -- never a
        second one for the same call.

        Fail-open covers more than exceptions: ``ConversationLogPort``
        implementations are not required to raise on failure.
        ``ChatwootAdapter`` in particular fails open by returning
        ``session_id`` itself (its own find-or-create sentinel for "the
        create didn't get an id back") rather than raising, and deliberately
        does NOT cache that outcome, so a later call retries instead of
        repeating the failure forever. Treating that truthy-but-fake id as a
        real ticket would silently stream every live block at a conversation
        that doesn't exist, so it's treated identically to an exception.

        (This sentinel check is inherently adapter-specific -- a different
        ``ConversationLogPort`` implementation that fails open to some other
        fixed placeholder id, rather than echoing ``session_id``, wouldn't be
        caught by an ``== session_id`` comparison. Known and accepted for
        now: the port protocol doesn't standardize a failure sentinel, and
        ``ChatwootAdapter`` is the only implementation this ships against.)

        Deliberately excluded from that error path: Chatwoot being
        DELIBERATELY disabled (``chatwoot_enabled=False``, e.g. a
        phone-only tenant with no Chatwoot integration at all) also returns
        this sentinel, but is expected, quiet behaviour, not a failure --
        logging it at ERROR would fire on every single call for such a
        tenant and look like a standing outage.
        """
        session_id = f"phone-{call_sid}"
        try:
            ticket_id = await self._log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[phone] Conversation {session_id}",
                customer_name=None,
                customer_phone=None,
            )
        except Exception as e:
            _log.error("phone_ticket_create_failed", session_id=session_id, error=str(e))
            return
        if ticket_id == session_id:
            if self._settings.chatwoot_enabled:
                _log.error(
                    "phone_ticket_create_failed",
                    session_id=session_id,
                    error="log port returned the session_id sentinel (create failed, fail-open)",
                )
            else:
                _log.info("phone_ticket_create_skipped_chatwoot_disabled", session_id=session_id)
            return
        self.ticket_id = ticket_id

    def _poll_transcript_flush(self) -> None:
        """Check whether the sink has a block due to post, and if so queue
        it for the detached flush worker.

        Called on EVERY pump() loop tick -- not only right after a
        transcript fragment is added -- because ``TranscriptSink`` has no
        clock of its own: its flush-interval timer only advances when
        something calls ``take_if_due()``. Restricting this call to the
        InputTranscript/OutputTranscript branches would mean a long
        assistant reply (all AudioOut/OutputTranscript, no new caller
        speech) never gets polled between transcript fragments, so a
        completed caller turn could sit unposted until finalize()'s forced
        flush at call end -- silently defeating "live" streaming.
        """
        if self.ticket_id is None:
            return
        block = self._sink.take_if_due()
        if block is None:
            return
        self._flush_queue.put_nowait(block)
        if self._flush_worker is None or self._flush_worker.done():
            self._flush_worker = asyncio.create_task(self._run_flush_worker())

    async def _run_flush_worker(self) -> None:
        """Drain the flush queue, posting one block at a time.

        Runs as a task detached from pump()'s event loop so a slow or
        failing Chatwoot call never stalls the audio pump; posting blocks
        one-at-a-time (rather than one task per block) is what keeps them
        landing in the ticket in the order they were spoken -- concurrent
        HTTP calls could otherwise complete out of order. A failed post is
        logged and skipped, not retried, so one bad block can't wedge every
        block queued after it.
        """
        while True:
            try:
                block = self._flush_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            ticket_id = self.ticket_id
            if ticket_id is None:
                # Not reachable via the current call sites (both only queue
                # once self.ticket_id is set) but drain rather than abandon:
                # returning here would leave this and every later queued
                # block stuck behind a worker that's already exited.
                _log.error("phone_transcript_flush_no_ticket", call_sid=self.call_sid, block=block)
                self._live_blocks_failed = True
                continue
            try:
                result = await self._log_port.append_conversation_comment(ticket_id, block)
            except Exception as e:
                _log.error("phone_transcript_flush_failed", call_sid=self.call_sid, error=str(e))
                self._live_blocks_failed = True
                continue
            if result != ConversationLogResult.OK:
                _log.error(
                    "phone_transcript_flush_failed", call_sid=self.call_sid, result=str(result)
                )
                self._live_blocks_failed = True
                continue
            self._live_blocks_posted = True

    def _append_transcript(self, role: str, text: str) -> None:
        # Gemini Live streams transcription as incremental deltas; concatenate
        # consecutive same-role fragments into one coherent turn rather than many
        # short rows. (If a live smoke test ever shows CUMULATIVE transcripts
        # instead of deltas, switch this to replace-last instead of concatenate.)
        if self.transcript and self.transcript[-1][0] == role:
            prev_role, prev_text = self.transcript[-1]
            self.transcript[-1] = (prev_role, prev_text + text)
        else:
            self.transcript.append((role, text))

    async def _handle_tool_call(self, event: ToolCall) -> None:
        if event.name == "kb_search":
            result = await dispatch_kb_search(event.args, self._knowledge)
            await self._live.send_tool_response(event.id, event.name, result)
        elif event.name == "request_human_handoff":
            self.handoff = {
                "reason": str(event.args.get("reason") or ""),
                "summary": str(event.args.get("summary") or ""),
            }
            await self._live.send_tool_response(event.id, event.name, {"status": "ticket_created"})
        elif event.name == "submit_csat":
            score = parse_csat_score(event.args)
            if score is not None:
                self.csat_score = score
            await self._live.send_tool_response(
                event.id,
                event.name,
                {"status": "recorded" if score is not None else "ignored"},
            )
        else:
            _log.warning("phone_unknown_tool", name=event.name, call_id=event.id)
            await self._live.send_tool_response(
                event.id, event.name, {"error": f"unknown tool: {event.name}"}
            )

    async def pump(self) -> None:
        async for event in self._live.events():
            if isinstance(event, AudioOut):
                if self.stream_sid:
                    mulaw = pcm24k_to_mulaw8k(event.pcm)
                    await self._send_twilio(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(mulaw).decode()},
                        }
                    )
            elif isinstance(event, Interrupted):
                if self.stream_sid:
                    await self._send_twilio({"event": "clear", "streamSid": self.stream_sid})
            elif isinstance(event, InputTranscript):
                is_new_user_turn = not (self.transcript and self.transcript[-1][0] == "USER")
                self._append_transcript("USER", event.text)
                if self._settings.phone_transcript_live_enabled:
                    self._sink.add("USER", event.text)
                if is_new_user_turn and self._settings.phone_language_nudge_enabled:
                    await self._live.send_text_hint(
                        "(Reminder: match your next reply's language to what "
                        "the caller just said, even mid-conversation.)"
                    )
            elif isinstance(event, OutputTranscript):
                self._append_transcript("ASSISTANT", event.text)
                if self._settings.phone_transcript_live_enabled:
                    self._sink.add("ASSISTANT", event.text)
            elif isinstance(event, ToolCall):
                await self._handle_tool_call(event)
            if self._settings.phone_transcript_live_enabled:
                self._poll_transcript_flush()

    async def _settle_ticket_create_task(self) -> None:
        """Await any in-flight call-start ticket-create task (see
        handle_twilio) so self.ticket_id reflects its outcome before
        finalize() decides whether to reuse it or fall back to creating one
        itself. _create_ticket_at_start never raises out of itself, but
        this is still wrapped defensively -- finalize() is the last-resort
        path that must reach the summary/status update below no matter
        what. Bounded the same way as the flush-worker drain: an unbounded
        wait here could hold the websocket teardown path (and the Gemini
        Live session under it) open for however long a blackholed Chatwoot
        takes on a call that hung up before its own call-start create
        finished -- worst case the ~30s several-sequential-HTTP-call path
        described in _create_ticket_at_start's docstring.
        """
        if self._ticket_create_task is None:
            return
        try:
            await asyncio.wait_for(self._ticket_create_task, timeout=_FLUSH_DRAIN_TIMEOUT_SECONDS)
        except Exception as e:
            _log.error("phone_finalize_ticket_task_failed", call_sid=self.call_sid, error=str(e))

    async def _drain_flush_queue(self) -> None:
        """Force out whatever's left in the live-transcript sink (a partial
        turn, or something that hadn't hit the flush interval yet) BEFORE
        the closing comment below, so streamed blocks and the closing
        comment land in speaking order. The sink itself is idempotent --
        take_if_due() empties what it returns -- so nothing posted live
        gets posted again here. A call that ends before anyone spoke leaves
        the sink empty, so this is a no-op (no blank post).

        Never let a slow drain or a worker failure skip the summary/status
        update in finalize() -- that's the last-resort guarantee that the
        call gets recorded at all. Bounded wait: each queued block is one
        HTTP call at the adapter's own 10s timeout, so an unbounded await
        here could hold the websocket teardown path open for 10s per
        queued block.
        """
        final_block = self._sink.take_if_due(force=True)
        if final_block is not None and self.ticket_id is not None:
            self._flush_queue.put_nowait(final_block)
        if not self._flush_queue.empty() and (
            self._flush_worker is None or self._flush_worker.done()
        ):
            self._flush_worker = asyncio.create_task(self._run_flush_worker())
        if self._flush_worker is None:
            return
        try:
            await asyncio.wait_for(self._flush_worker, timeout=_FLUSH_DRAIN_TIMEOUT_SECONDS)
        except Exception as e:
            _log.error("phone_finalize_flush_drain_failed", call_sid=self.call_sid, error=str(e))

    async def _resolve_finalize_ticket_id(self, session_id: str, body: str) -> str | None:
        """Return the ticket id finalize() should post the closing
        comment(s) to, or ``None`` if it should give up (already logged).

        Reuses ``self.ticket_id`` when the call-start create already
        succeeded; ``ensure_conversation_ticket`` is keyed on session_id
        anyway, so calling it again here would be safe too -- this just
        skips a redundant lookup on the common path. Otherwise falls back
        to creating one now, with the SAME sentinel check as
        ``_create_ticket_at_start`` (Critical 1): a failed create here also
        fails open to ``session_id`` itself rather than raising, and that
        truthy-but-fake id must not be adopted as real -- every subsequent
        call would then silently 404 against a conversation that doesn't
        exist, discarding the whole transcript with nothing left to look
        at but a log line. When that happens, the full transcript is
        logged alongside it so the call is at least recoverable from logs.
        """
        if self.ticket_id is not None:
            return self.ticket_id
        ticket_id = await self._log_port.ensure_conversation_ticket(
            session_id=session_id,
            subject=f"[phone] Conversation {session_id}",
            customer_name=None,
            customer_phone=None,
        )
        if ticket_id == session_id:
            _log.error(
                "phone_finalize_failed",
                session_id=session_id,
                error="log port returned the session_id sentinel (create failed, fail-open)",
                transcript=body,
            )
            return None
        self.ticket_id = ticket_id
        return ticket_id

    async def finalize(self) -> None:
        await self._settle_ticket_create_task()
        await self._drain_flush_queue()

        if not self.transcript or not self.call_sid:
            return
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        status = "open" if self.handoff is not None else "solved"
        try:
            ticket_id = await self._resolve_finalize_ticket_id(session_id, body)
            if ticket_id is None:
                return
            if self.handoff is not None:
                note = (
                    "[Handoff to human agent]\n"
                    f"{self.handoff.get('reason', '')}\n{self.handoff.get('summary', '')}"
                )
                await self._log_port.append_conversation_comment(ticket_id, note, status="open")
            # With live streaming on, at least one block already
            # successfully posted (during pump() or the forced flush just
            # above), AND none of them failed, the ticket already holds the
            # COMPLETE transcript turn-by-turn -- posting the whole joined
            # transcript again here would show the agent the call twice.
            # Post a short closing comment that still carries the status
            # flip instead. Otherwise -- nothing posted live at all (flag
            # off, ticket creation failed, Chatwoot down throughout, a very
            # short call that never hit a flush point), OR some blocks
            # posted but at least one did NOT -- fall back to today's
            # behaviour and post the whole transcript in full. A duplicated
            # transcript on a degraded call is a far better outcome than a
            # permanently missing turn on the customer's record.
            if (
                self._settings.phone_transcript_live_enabled
                and self._live_blocks_posted
                and not self._live_blocks_failed
            ):
                await self._log_port.append_conversation_comment(
                    ticket_id, "[Call ended]", status=status
                )
            else:
                await self._log_port.append_conversation_comment(ticket_id, body, status=status)
            await self._log_port.set_ticket_external_id(ticket_id, session_id)
            if self.handoff is None and self.csat_score is not None:
                await record_csat_on_ticket(self._log_port, ticket_id, self.csat_score, "phone")
        except Exception as e:
            _log.error(
                "phone_finalize_failed", session_id=session_id, error=str(e), transcript=body
            )
