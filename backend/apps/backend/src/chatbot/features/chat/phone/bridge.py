"""Orchestrates a single phone call: Twilio Media Stream ⇄ Gemini Live."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.csat import record_csat_on_ticket
from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from chatbot.features.chat.phone.call_control import CallControl
from chatbot.features.chat.phone.handoff_csat_tools import parse_csat_score
from chatbot.features.chat.phone.handoff_target import HandoffTargetResolver, dial_twiml
from chatbot.features.chat.phone.kb_tool import dispatch_kb_search
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ToolCall,
)
from chatbot.features.chat.phone.transcript_classifier import classify
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

# Bound on the post-call transcript classification (Task 4): a one-shot
# Gemini call in finalize(), same shape as the two bounds above -- an
# unbounded wait here could hold the websocket teardown path open for
# however long a slow/hanging Gemini call takes. A classifier is not a
# source of truth, so timing out is just another failure mode: it falls
# back to today's exact binary status rule, same as any other classify()
# failure.
_CLASSIFY_TIMEOUT_SECONDS = 10.0

# Review fix (Important 1): unlike the ticket-create/recording-start tasks
# above (both detached, precisely so a slow Chatwoot/Twilio call never
# stalls audio), `_attempt_transfer` runs INLINE inside `pump()` -- it's
# the only coroutine forwarding Gemini audio to Twilio, and there's a real
# {"status": ...} the model needs back before it can react, so it can't
# simply be detached the same way. Bounding both awaits keeps a
# blackholed Chatwoot or Twilio from turning "the caller hears a couple
# seconds of dead air during a transfer" (expected -- a live redirect is
# not free) into "the caller hears dead air indefinitely". Both are
# shorter than ChatwootAdapter._request's own 10s timeout so THESE bounds
# are the ones that actually fire; a real Twilio/Chatwoot API call is
# typically sub-second, so 5s is generous, not tight. A timeout here is
# just another failure mode -- same as any other resolve()/redirect()
# failure, it falls back to today's exact "ticket_created" behaviour.
_HANDOFF_RESOLVE_TIMEOUT_SECONDS = 5.0
_HANDOFF_REDIRECT_TIMEOUT_SECONDS = 5.0


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
        call_control: CallControl | None = None,
        handoff_resolver: HandoffTargetResolver | None = None,
    ) -> None:
        self._live = live
        self._knowledge = knowledge_port
        self._log_port = conversation_log_port
        self._send_twilio = send_twilio
        self._settings = settings
        # Package C Task 5: injectable for tests (never construct a real
        # Twilio client from a test -- see call_control.py's own docstring);
        # defaults to a real CallControl otherwise. CallControl's own
        # constructor is cheap and never raises -- it only builds the
        # underlying twilio.rest.Client lazily, on first actual API call --
        # so constructing one here unconditionally is safe even when
        # phone_recording_enabled is off.
        self._call_control = call_control if call_control is not None else CallControl(settings)
        # Package C Task 6: same injectable-for-tests shape as call_control
        # above. Constructing one unconditionally is safe even when
        # phone_handoff_enabled is off -- resolve() checks the flag first
        # and never touches the log port otherwise.
        self._handoff_resolver = (
            handoff_resolver
            if handoff_resolver is not None
            else HandoffTargetResolver(settings, conversation_log_port)
        )
        # Review fix (Important 2): once a redirect has actually been
        # accepted by Twilio, the call is mid-<Dial> -- a SECOND
        # request_human_handoff arriving before the websocket tears down
        # (e.g. the model retries after a slow first response) must not
        # issue a second calls.update(), which would replace the
        # in-progress <Dial> and restart the ring from zero. Mirrors
        # _recording_start_attempted's "at most once" shape, but keyed on
        # SUCCESS (a failed attempt should still be retryable) rather than
        # "was attempted".
        self._transfer_dialed = False
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
        # Package C Task 5: recording is attempted at most ONCE per call
        # (unlike the ticket-create task above, which retries on the next
        # "start" event if it failed). A Twilio recording is a real, billed
        # resource, so a reconnect/duplicate "start" event must not risk
        # kicking off a second concurrent recording on the same call.
        self._recording_start_attempted = False
        # Fire-and-forget, same shape as _ticket_create_task -- see
        # _maybe_start_recording. finalize() awaits this (bounded) purely so
        # it isn't silently destroyed mid-flight on a fast hangup; nothing
        # downstream depends on its result.
        self._recording_task: asyncio.Task[None] | None = None
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
        # Lazily built, cached google-genai client for post-call transcript
        # classification (Task 4). Only ever touched from finalize() when
        # phone_transcript_classification_enabled is on -- see _genai().
        self._genai_client: Any | None = None

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
                if (
                    self.call_sid
                    and not self._recording_start_attempted
                    and self._settings.phone_recording_enabled
                ):
                    # Fire-and-forget, same reasoning as the ticket-create
                    # task just above: the Twilio REST call (via
                    # asyncio.to_thread inside CallControl) must never delay
                    # the greeting or the first bytes of caller audio.
                    self._recording_start_attempted = True
                    self._recording_task = asyncio.create_task(
                        self._maybe_start_recording(self.call_sid)
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

    def _recording_status_callback_url(self) -> str:
        base = self._settings.twilio_webhook_base_url
        if not base:
            return ""
        return f"{base.rstrip('/')}/webhooks/phone/recording-status"

    def _dial_status_action_url(self) -> str:
        base = self._settings.twilio_webhook_base_url
        if not base:
            return ""
        return f"{base.rstrip('/')}/webhooks/phone/dial-status"

    async def _maybe_start_recording(self, call_sid: str) -> None:
        """Start Twilio call recording once, fire-and-forget from
        handle_twilio's "start" branch -- same shape as
        _create_ticket_at_start, for the same reason: a slow or blackholed
        Twilio REST call must never delay the greeting or caller audio.

        Fails CLOSED on two things, deliberately unlike every other path in
        this package (see config.py's phone_recording_announcement
        docstring) -- both are "voice data would be captured with no
        guarantee of notice, or no way to ever find it again", which is the
        exact class of harm Step 4's fail-closed rule exists for, not the
        general fail-open rule the rest of this package follows:

        1. Malaysia's PDPA requires the caller hear a recorded-line notice
           BEFORE recording starts. If phone_recording_enabled is on but no
           announcement is configured -- or the announcement could not even
           be QUEUED into the live session (a closed/broken Live session
           raises exactly when a hint is sent) -- this refuses to start
           recording at all (logged at WARNING), rather than silently
           recording either without notice or with notice that never made
           it to the caller. A config mistake, or a broken Live session,
           must not silently become "recorded but not disclosed".
        2. If no status-callback URL can be built (twilio_webhook_base_url
           unset), a recording started anyway would capture the customer's
           voice with nothing that can ever attach it to a ticket or apply
           the retention policy -- an untracked, orphaned recording. So this
           checks the callback URL BEFORE doing anything else, including
           before queuing the announcement: there is no point telling the
           caller "this call is recorded" and then not recording it.

        The announcement itself is delivered as a text-hint into the live
        Gemini session -- the same primitive IVR-4's per-turn language
        reminder already uses (LiveSession.send_text_hint) -- instructing
        the model to speak it, verbatim, before continuing. There is no
        lower-level "play this exact audio clip" hook on LiveSession, so
        this is a best-effort instruction to the model, not a guaranteed
        byte-exact TTS playback of the configured text, and NOT sequenced
        before start_recording() below at the Twilio/TwiML level (queuing a
        text hint only queues it; it does not block until spoken).

        Package C Task 6: `router.py`'s `phone_incoming` now ALSO speaks
        this same announcement via a `<Say>` in the initial TwiML, before
        `<Connect><Stream>` (see `twiml.connect_stream_twiml`'s
        `announcement` parameter) -- that one runs deterministically before
        Twilio ever opens the Media Stream whose "start" event is what
        triggers this method, so IT is the provably-sequenced disclosure.
        This text-hint is kept, unchanged, as a secondary reinforcement
        (e.g. in case the caller talks over the `<Say>`) -- not a
        replacement for it.
        """
        announcement = self._settings.phone_recording_announcement
        if not announcement:
            _log.warning("phone_recording_no_announcement_configured", call_sid=call_sid)
            return
        callback = self._recording_status_callback_url()
        if not callback:
            _log.warning("phone_recording_no_callback_base_configured", call_sid=call_sid)
            return
        try:
            await self._live.send_text_hint(
                "(Recorded-line notice -- before anything else, tell the caller "
                f'now, verbatim, in English and Bahasa Melayu: "{announcement}" '
                "Then continue the conversation normally.)"
            )
        except Exception as e:
            _log.error("phone_recording_announcement_hint_failed", call_sid=call_sid, error=str(e))
            return
        sid = await self._call_control.start_recording(call_sid, callback)
        if sid:
            _log.info("phone_recording_started", call_sid=call_sid, recording_sid=sid)
        # A falsy sid means CallControl.start_recording already logged the
        # failure itself (bad credentials, Twilio API error, construction
        # failure, ...) -- fail-open, the call simply continues unrecorded.

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
            status = await self._attempt_transfer()
            await self._live.send_tool_response(event.id, event.name, {"status": status})
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

    async def _attempt_transfer(self) -> str:  # noqa: PLR0911 -- each early return is a distinct, documented fallback reason
        """Package C Task 6: try to redirect the live call into a real
        human transfer. Returns the status the `request_human_handoff` tool
        response carries -- "transferring" only when Twilio has genuinely
        accepted the redirect; "ticket_created" (today's exact behaviour)
        for every other outcome: already dialled once, the feature is off,
        unconfigured, no caller id configured, out of business hours, no
        callback base is configured, a resolve()/redirect() timeout, or the
        Twilio API call itself failed. All of those collapse to the SAME
        fallback on purpose -- self.handoff is already set by the caller,
        so finalize() still opens the ticket with a handoff note regardless
        of which branch below returns "ticket_created"; the caller is
        never silently dropped, and this method itself never raises (a
        resolver or CallControl failure must not break the live tool-call
        turn).

        Review fix (Important 4): this status string is best-effort
        bookkeeping for the AI-actions log, NOT a reliable cue the model
        can react to out loud -- `redirect()` below tears down the Media
        Stream (hence this WebSocket) as soon as Twilio accepts it, which
        can race the tool response actually reaching the live session, so
        a spoken "you're being transferred" line queued AFTER this returns
        may never reach the caller. The system prompt (see `router.py`'s
        `phone_stream`) instead tells the model to say that line BEFORE
        calling this tool, not after.

        Deliberately distinct from `/webhooks/phone/dial-status`'s
        unanswered-call fallback (apology TwiML + `open` + an
        `unanswered_handoff` tag): that only applies once a dial has
        ACTUALLY been placed and Twilio reports nobody picked up. Nothing
        here ever reaches that point unless `redirect()` below returns
        True.
        """
        # Review fix (Important 2): a transfer already in flight must not
        # be re-dialled by a second request_human_handoff call arriving
        # before the websocket tears down -- see _transfer_dialed's
        # docstring in __init__.
        if self._transfer_dialed:
            return "transferring"
        if self.call_sid is None:
            return "ticket_created"
        # Review fix (Important 1): both awaits below are bounded -- see
        # _HANDOFF_RESOLVE_TIMEOUT_SECONDS/_HANDOFF_REDIRECT_TIMEOUT_SECONDS'
        # module-level docstring. This method runs INLINE inside pump(),
        # unlike the detached ticket-create/recording-start tasks.
        try:
            target = await asyncio.wait_for(
                self._handoff_resolver.resolve(), timeout=_HANDOFF_RESOLVE_TIMEOUT_SECONDS
            )
        except Exception as e:
            _log.error("phone_handoff_resolve_failed", call_sid=self.call_sid, error=str(e))
            return "ticket_created"
        if target is None:
            return "ticket_created"
        action_url = self._dial_status_action_url()
        if not action_url:
            _log.warning("phone_handoff_no_action_url_configured", call_sid=self.call_sid)
            return "ticket_created"
        twiml = dial_twiml(
            target,
            action_url,
            self._settings.phone_handoff_timeout_seconds,
            self._settings.phone_handoff_caller_id,
        )
        try:
            ok = await asyncio.wait_for(
                self._call_control.redirect(self.call_sid, twiml),
                timeout=_HANDOFF_REDIRECT_TIMEOUT_SECONDS,
            )
        except Exception as e:
            _log.error("phone_handoff_redirect_failed", call_sid=self.call_sid, error=str(e))
            return "ticket_created"
        if not ok:
            return "ticket_created"
        self._transfer_dialed = True
        return "transferring"

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

    async def _settle_recording_task(self) -> None:
        """Await any in-flight recording-start task (see
        _maybe_start_recording) so it can't be silently destroyed mid-flight
        by a fast hangup right after "start". Nothing downstream depends on
        its result -- this exists purely so a slow/blackholed Twilio call
        doesn't leak an unawaited task -- so it is bounded the same way as
        the ticket-create settle just above, for the same reason.

        NOTE this bound is best-effort, not a hard cancel: on timeout,
        ``wait_for`` cancels our AWAIT of the task, but ``CallControl.
        start_recording`` runs the actual Twilio call via ``asyncio.
        to_thread`` -- a thread already submitted to the executor keeps
        running to completion regardless of the asyncio-level cancellation.
        So a very slow Twilio call can still result in a recording actually
        being created after this method has already given up waiting for
        it; that recording is simply one this process never logged a sid
        for (the callback, once it fires, still finds/updates the ticket
        correctly via find_conversation_ticket).
        """
        if self._recording_task is None:
            return
        try:
            await asyncio.wait_for(self._recording_task, timeout=_FLUSH_DRAIN_TIMEOUT_SECONDS)
        except Exception as e:
            _log.error("phone_finalize_recording_task_failed", call_sid=self.call_sid, error=str(e))

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

    def _genai(self) -> Any | None:
        """Lazily build (and cache) a google-genai client for post-call
        transcript classification. Mirrors ``CallControl._twilio()`` and
        ``main.py``'s ``_build_genai_client``: construction is fail-open and
        never raises. A construction failure is deliberately not cached, so
        a later call retries rather than repeating the failure forever."""
        if self._genai_client is not None:
            return self._genai_client
        try:
            from google.genai import Client  # noqa: PLC0415 -- lazy: fail-open without the SDK

            if self._settings.google_genai_use_vertexai:
                self._genai_client = Client(
                    vertexai=True,
                    project=self._settings.vertex_project_id,
                    location=self._settings.vertex_location,
                )
            else:
                self._genai_client = Client()
        except Exception as e:
            _log.error("phone_transcript_classify_client_init_failed", error=str(e))
            return None
        return self._genai_client

    async def _classify_transcript(self, body: str) -> dict[str, str]:
        """Best-effort post-call classification (Task 4). Only ever called
        from finalize() -- never from pump() -- so it cannot delay the live
        audio path. Bounded by _CLASSIFY_TIMEOUT_SECONDS so a slow/hanging
        Gemini call cannot hold the websocket teardown path open
        indefinitely either. Any failure -- no client, a timeout, a
        malformed response -- returns {} so finalize() falls back to
        today's exact binary status rule."""
        client = self._genai()
        if client is None:
            return {}
        try:
            return await asyncio.wait_for(classify(body, client), timeout=_CLASSIFY_TIMEOUT_SECONDS)
        except Exception as e:
            _log.error(
                "phone_transcript_classify_bounded_call_failed",
                call_sid=self.call_sid,
                error=str(e),
            )
            return {}

    async def _classify_and_apply(self, ticket_id: str, body: str, status: str) -> str:
        """Run post-call classification and apply its results: write case_
        type/division/concern as custom attributes, and return the status
        finalize() should actually use for the closing comment.

        `status` (today's exact binary default, already computed by the
        caller) is returned UNCHANGED whenever classification has nothing
        useful to say -- it failed, returned {}, or returned a status this
        function doesn't recognise -- which is exactly "fall back to
        today's exact binary rule". An explicit human handoff outranks any
        inference: the caller only reaches the classifier's own status
        decision when self.handoff is None, so a handoff-derived "open"
        can never be overwritten here.
        """
        classification = await self._classify_transcript(body)
        if classification:
            _log.info(
                "phone_transcript_classified",
                call_sid=self.call_sid,
                keys=sorted(classification.keys()),
            )
        # `status` above is already "solved" whenever we reach here (handoff
        # is None) -- so a classified "resolved" reading has nothing to
        # change TO, that's already where it starts. Only a classifier
        # reading the call as still needing action ("open"/"pending") has
        # any actual effect: it flips the default back to "open". There is
        # deliberately no branch for "resolved" -- one live transition, not
        # three.
        if self.handoff is None and classification.get("status") in ("open", "pending"):
            status = "open"
        if (
            classification.get("case_type")
            or classification.get("division")
            or classification.get("concern")
        ):
            try:
                await self._log_port.set_ticket_classification(
                    ticket_id,
                    case_type=classification.get("case_type"),
                    division=classification.get("division"),
                    concern=classification.get("concern"),
                )
            except Exception as e:
                _log.error(
                    "phone_transcript_classify_write_failed", call_sid=self.call_sid, error=str(e)
                )
        return status

    async def finalize(self) -> None:
        await self._settle_ticket_create_task()
        await self._settle_recording_task()
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

            if self._settings.phone_transcript_classification_enabled:
                status = await self._classify_and_apply(ticket_id, body, status)

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
