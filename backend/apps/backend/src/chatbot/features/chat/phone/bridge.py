"""Orchestrates a single phone call: Twilio Media Stream ⇄ Gemini Live."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.csat import record_csat_on_ticket
from chatbot.features.chat.nps import record_nps_agent_attribution, record_nps_on_ticket
from chatbot.features.chat.phone.agent_client_resolver import AgentClientResolver, ChainedResolver
from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from chatbot.features.chat.phone.call_control import CallControl
from chatbot.features.chat.phone.handoff_csat_tools import parse_csat_score, parse_nps_score
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
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
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

# Whole-branch review fix (Important 7): the three awaits above are each
# bounded, but everything finalize() does AFTER the drain was not -- the
# fallback ticket create (3-4 sequential HTTP calls), classification write
# (GET+POST merge) plus the division label (GET+POST), the closing comment
# plus toggle_status, set_ticket_external_id (another merge) and CSAT
# (comment + union-tag), all at the adapter's own 10s-per-call timeout and
# applied PER PHASE. Worst case that is ~200s of teardown, which under a
# Chatwoot brownout with concurrent calls pins one WebSocket handler task
# per call. One ceiling over the whole tail caps that; on expiry the
# transcript is logged (truncated -- see _log_transcript) so the call is
# still recoverable. 60s is deliberately generous: it must not fire on a
# merely-slow Chatwoot, only on a wedged one.
_FINALIZE_TAIL_TIMEOUT_SECONDS = 60.0

# Whole-branch review fix (Important 11): call transcripts are customer
# voice content on a PDPA-scoped feature. The rest of this package keeps
# recording URLs out of agent-visible text and behind a
# `call_recording.listen` permission; writing the same content untruncated
# into plain application logs would undo that. The failure paths that log a
# transcript do so to make the call RECOVERABLE, and a few hundred
# characters plus the true length is enough to identify and triage the
# call without dumping the whole conversation into log storage.
_LOG_TRANSCRIPT_MAX_CHARS = 400

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
# typically sub-second, so these are generous, not tight. A timeout here is
# just another failure mode -- same as any other resolve()/redirect()
# failure, it falls back to today's exact "ticket_created" behaviour.
#
# Whole-branch review fix (Important 6): the resolve() half is no longer
# the routine cost it was. `HandoffTargetResolver.prefetch()` is fired as
# a detached task at call start (see handle_twilio), so by the time a
# handoff tool call arrives -- always many seconds into a call -- the
# business-hours answer is already cached and resolve() returns without
# any HTTP at all. This bound now only covers the cold path (prefetch
# disabled, still in flight, or expired), so the inline dead air on the
# common path is just the redirect. The redirect bound is in turn dropped
# from 5.0 to 3.0 so the worst case a caller can hear is ~3s of silence,
# not ~10s; `call_control._TWILIO_HTTP_TIMEOUT_SECONDS` was lowered
# alongside it to stay SHORTER than this bound, preserving Task 6's
# invariant that a slow redirect FAILS on the SDK side (leaving
# `_transfer_dialed` consistent) rather than being abandoned mid-flight by
# this bound.
#
# Whole-branch review fix (Important 8): this ONE bound now covers up to
# THREE network lookups when the softphone chain is cold (assignee,
# registry, business hours), not the single lookup it was sized for. A
# slow-but-not-failing one could consume the whole budget by itself and
# starve the PSTN fallback of a chance to even try. `agent_client_resolver.
# ChainedResolver` now applies its own per-resolver sub-bound
# (`_RESOLVER_TIMEOUT_SECONDS`) so a slow resolver costs a ring stage, not
# the fallback -- this outer bound remains the audio-pump guarantee of
# last resort.
_HANDOFF_RESOLVE_TIMEOUT_SECONDS = 5.0
_HANDOFF_REDIRECT_TIMEOUT_SECONDS = 3.0


def _log_transcript(body: str) -> str:
    """Truncate a transcript for structured logging (Important 11).

    Every site that logs a transcript does so on a failure path, to keep
    the call recoverable when Chatwoot never got it. That goal is met by a
    prefix plus the true length; the full customer-voice body does not
    belong in unguarded application logs on a PDPA-scoped feature.
    """
    if len(body) <= _LOG_TRANSCRIPT_MAX_CHARS:
        return body
    return f"{body[:_LOG_TRANSCRIPT_MAX_CHARS]}... [truncated, {len(body)} chars total]"


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
        softphone_registry: SoftphoneRegistry | None = None,
        presence_fetcher: Any | None = None,
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
        self._softphone_registry = softphone_registry
        # Lets the resolver fan out when a conversation has no assignee -- the
        # normal case for an inbound phone call.
        self._presence_fetcher = presence_fetcher
        self._rebuild_handoff_resolver(handoff_resolver)
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
        # P8 task 5: mutually exclusive with csat_score in practice -- the
        # tool list built in router.py's phone_stream offers exactly ONE of
        # submit_csat/submit_nps per call (never both), based on whether the
        # call was sampled for NPS before the Gemini Live session opened.
        self.nps_score: int | None = None
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
        # Whole-branch review fix (Important 6): warms
        # HandoffTargetResolver's business-hours answer OFF the audio path,
        # so `_attempt_transfer` -- which runs INLINE in pump(), the sole
        # Gemini->Twilio forwarder -- doesn't pay a `GET /inboxes/{id}` on
        # every handoff while caller audio keeps arriving and nothing goes
        # back. Fire-and-forget, same shape as the two tasks above; nothing
        # downstream depends on it (resolve() still does the check itself
        # if the cache is cold).
        self._handoff_prefetch_task: asyncio.Task[None] | None = None
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

    def _rebuild_handoff_resolver(self, injected: Any | None = None) -> None:
        """Compose the handoff resolver chain: the assigned agent's softphone
        first, the static PSTN hunt group behind it.

        Order is the feature. Stage 1 returning None is the normal case (no
        assignee, nobody registered, flag off) and must fall through to the
        behaviour every tenant has today -- which is why this is a chain and
        not a replacement.

        `injected` keeps the existing test seam: a caller that passes its own
        resolver gets exactly that resolver, unchained.

        Reviewer note (fix round 1, Finding 1): this is called explicitly --
        from `__init__` once, and again by any test that reassigns
        `self._settings` on an already-built bridge (see
        `test_softphone_disabled_is_byte_identical_to_today`) -- rather than
        via a `_settings` property/setter. Nothing in production reassigns
        `_settings` on a live bridge, so an automatic-rebuild-on-assignment
        setter was side-effecting complexity on a hot-path class for a need
        that was purely a test-fixture artifact.
        """
        if injected is not None:
            self._handoff_resolver = injected
            return
        pstn = HandoffTargetResolver(self._settings, self._log_port)
        if not self._settings.phone_agent_softphone_enabled or self._softphone_registry is None:
            self._handoff_resolver = pstn
            return
        self._handoff_resolver = ChainedResolver(
            [
                AgentClientResolver(
                    self._settings,
                    self._log_port,
                    self._softphone_registry,
                    lambda: self.ticket_id,
                    self._presence_fetcher,
                ),
                pstn,
            ]
        )

    def _handoff_parameters(self) -> dict[str, str]:
        """Context the ringing browser shows BEFORE the agent accepts.

        `reason`/`summary` are model-generated; `handoff_target._parameters_xml`
        escapes them. Truncated because Twilio caps the total TwiML size and a
        rambling summary on a live call is not worth a TwiML error.
        """
        handoff = self.handoff or {}
        params = {
            "conversation_id": str(self.ticket_id or ""),
            "reason": str(handoff.get("reason") or "")[:200],
            "summary": str(handoff.get("summary") or "")[:400],
        }
        return {k: v for k, v in params.items() if v}

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
                if (
                    self._settings.phone_handoff_enabled
                    or self._settings.phone_agent_softphone_enabled
                ) and (self._handoff_prefetch_task is None or self._handoff_prefetch_task.done()):
                    # Fire-and-forget, same reasoning again: the
                    # business-hours lookup is a real Chatwoot GET, and
                    # doing it here (once, at call setup) instead of
                    # inline in _attempt_transfer is what keeps the
                    # transfer path from stalling the audio pump.
                    self._handoff_prefetch_task = asyncio.create_task(
                        self._handoff_resolver.prefetch()
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
        # Stage 1 hands its outcome to the stage-2 handler; without the
        # softphone there is no stage 2 and the outcome is final.
        suffix = "/fanout" if self._settings.phone_agent_softphone_enabled else ""
        return f"{base.rstrip('/')}/webhooks/phone/dial-status{suffix}"

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
           announcement is configured, this refuses to start recording at
           all (logged at WARNING), rather than recording without notice.
           The gate stays here, on the same setting `router.py`'s TwiML
           `<Say>` is built from, so "recording on, announcement empty" can
           never become "recorded but not disclosed" -- whichever of the
           two paths is examined.
        2. If no status-callback URL can be built (twilio_webhook_base_url
           unset), a recording started anyway would capture the customer's
           voice with nothing that can ever attach it to a ticket or apply
           the retention policy -- an untracked, orphaned recording.

        WHO ACTUALLY SPEAKS THE NOTICE: `router.py`'s `phone_incoming`
        emits it as a `<Say>` in the same `<Response>` immediately before
        `<Connect><Stream>` (see `twiml.connect_stream_twiml`'s
        `announcement` parameter). TwiML verbs run in document order, so
        that notice provably precedes the Media Stream -- whose "start"
        event is the only thing that triggers this method, and therefore
        the only thing that can trigger recording. The disclosure is
        sequenced by construction.

        Whole-branch review fix (Important 4): this method used to ALSO
        queue a text hint asking the Gemini model to speak the same notice
        "verbatim, before anything else". Once Task 6 made the `<Say>`
        deterministic, that hint stopped being reinforcement and became a
        SECOND reading of the notice -- in a different voice, seconds after
        the first, on every recorded call. It is gone; the `<Say>` is the
        single disclosure.
        """
        announcement = self._settings.phone_recording_announcement
        if not announcement:
            _log.warning("phone_recording_no_announcement_configured", call_sid=call_sid)
            return
        callback = self._recording_status_callback_url()
        if not callback:
            _log.warning("phone_recording_no_callback_base_configured", call_sid=call_sid)
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
                _log.error(
                    "phone_transcript_flush_no_ticket",
                    call_sid=self.call_sid,
                    block=_log_transcript(block),
                )
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
        elif event.name == "submit_nps":
            nps_score = parse_nps_score(event.args)
            if nps_score is not None:
                self.nps_score = nps_score
            await self._live.send_tool_response(
                event.id,
                event.name,
                {"status": "recorded" if nps_score is not None else "ignored"},
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
        ACTUALLY been placed and Twilio reports nobody picked up.

        Review fix (Important 3, round 2): this is true whenever `redirect()`
        returns normally, but NOT airtight against the bounded
        `asyncio.wait_for` above -- `CallControl.redirect`'s Twilio SDK call
        runs in a thread (`asyncio.to_thread`); `wait_for`'s timeout cancels
        our AWAIT of that thread, not the thread itself, so a redirect that
        actually lands on Twilio's side just after the bound expires would
        leave `_transfer_dialed` False (and this returning "ticket_created")
        even though a `<Dial>` may be in flight. `CallControl`'s own
        SDK-level HTTP timeout (see `call_control.py`) is what actually
        prevents that in practice by making a slow call FAIL well before
        this bound, rather than merely abandoning an in-flight one -- this
        bound exists as the audio-pump guarantee, the SDK timeout as the
        "don't leave state inconsistent" guarantee. The two together make
        that race exceptional, not eliminate it outright.
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
        if target.kind == "client":
            timeout = self._settings.phone_agent_ring_timeout_seconds
        elif target.kind == "clients":
            # Immediate fan-out: give it the fan-out budget, not the shorter
            # single-agent one -- several people need a chance to reach for it.
            timeout = self._settings.phone_fanout_ring_timeout_seconds
        else:
            timeout = self._settings.phone_handoff_timeout_seconds
        twiml = dial_twiml(
            target,
            action_url,
            timeout,
            self._settings.phone_handoff_caller_id,
            self._handoff_parameters(),
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

    async def _settle_handoff_prefetch_task(self) -> None:
        """Await any in-flight business-hours prefetch task (see
        ``_handoff_prefetch_task`` / ``HandoffTargetResolver.prefetch``) so
        it isn't left detached past teardown -- the same convention this
        file states explicitly for ``_ticket_create_task`` and
        ``_recording_task`` just above: every task created fire-and-forget
        at call setup gets settled here.

        Benign either way -- ``prefetch()`` never raises (fails open to a
        cold cache, which just means ``resolve()`` does the lookup inline
        next time) and completes inside its own bounded HTTP call -- but an
        un-awaited task is still a lingering task, so it gets the same
        bounded settle as the other two for consistency, not because a
        failure here has any observable effect.
        """
        if self._handoff_prefetch_task is None:
            return
        try:
            await asyncio.wait_for(
                self._handoff_prefetch_task, timeout=_FLUSH_DRAIN_TIMEOUT_SECONDS
            )
        except Exception as e:
            _log.error(
                "phone_finalize_handoff_prefetch_task_failed", call_sid=self.call_sid, error=str(e)
            )

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

        Whole-branch review fix (Important 1, DATA LOSS): the timeout below
        CANCELS the flush worker, so whatever block it was posting -- and
        every block still queued behind it -- never lands. If at least one
        earlier block had already posted, `_live_blocks_posted` would be
        True and `_live_blocks_failed` still False, and finalize() would
        take the short "[Call ended]" branch: the caller's last turn would
        be permanently missing from the ticket, with no full-body fallback.
        That directly contradicts the invariant this package states three
        times ("a duplicated transcript on a degraded call is far better
        than a permanently missing turn"). Abandoning the drain IS a live
        block failing, so it is recorded as one.
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
            self._live_blocks_failed = True
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

        Whole-branch review minor: this used to emit ``phone_finalize_
        failed``, the SAME event name finalize()'s own catch-all uses for a
        structurally different failure (an exception thrown by one of the
        closing writes). Event-name-keyed alerting could not tell "the
        ticket could never be created" from "a closing write blew up", so
        they now have distinct names.
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
                "phone_finalize_ticket_create_failed",
                session_id=session_id,
                error="log port returned the session_id sentinel (create failed, fail-open)",
                transcript=_log_transcript(body),
            )
            return None
        self.ticket_id = ticket_id
        return ticket_id

    def _genai(self) -> Any | None:
        """Lazily build (and cache) a google-genai client for post-call
        transcript classification. Mirrors ``CallControl._twilio()``:
        construction is fail-open and never raises. A construction failure is
        deliberately not cached, so a later call retries rather than repeating
        the failure forever.

        P8: the client comes from ``build_metered_genai_client`` rather than
        from ``google.genai.Client`` directly, so this call site's tokens are
        counted by construction. With ``token_metering_enabled`` off (the
        default) that returns the raw SDK client unwrapped -- byte-identical
        to the previous code, no proxy on the classification path."""
        if self._genai_client is not None:
            return self._genai_client
        try:
            from chatbot.platform.metered_genai import (  # noqa: PLC0415 -- lazy: fail-open without the SDK
                SURFACE_PHONE_CLASSIFY,
                build_metered_genai_client,
            )

            self._genai_client = build_metered_genai_client(
                self._settings, surface=SURFACE_PHONE_CLASSIFY
            )
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

    async def _close_abandoned_call(self, session_id: str) -> None:
        """Whole-branch review fix (Important 3): self-clean a ticket that
        was created at call start for a call nobody ever spoke on.

        With ``phone_transcript_live_enabled`` on, the conversation is
        created at the Twilio "start" event -- before a single word is
        said. A caller who hangs up during the greeting (wrong numbers,
        spam scans, our own smoke tests) used to leave finalize() at its
        ``not self.transcript`` guard, stranding a contact plus an open,
        empty, unlabelled, ``external_id``-less conversation in the agent
        queue forever. Before this package such a call created nothing at
        all, so this is a leak the package introduced.

        Post a short marker comment, resolve it, and stamp the
        ``external_id`` so the recording-status/dial-status callbacks can
        still find it by session id. Deliberately a no-op when
        ``self.ticket_id`` is None -- which is exactly the flags-off case
        (nothing was created, so there is nothing to clean up), keeping
        flags-off behaviour byte-identical.
        """
        ticket_id = self.ticket_id
        if ticket_id is None:
            return
        await self._log_port.append_conversation_comment(
            ticket_id, "[Call ended — no conversation]", status="solved"
        )
        await self._log_port.set_ticket_external_id(ticket_id, session_id)

    async def finalize(self) -> None:
        """Teardown: settle the detached call-start tasks, drain whatever
        live transcript is still queued, then do the closing CRM writes.

        Whole-branch review fix (Important 7): the four awaits above are
        individually bounded, but the closing writes were not bounded at
        ALL -- see ``_FINALIZE_TAIL_TIMEOUT_SECONDS``. They now run under
        one ceiling, and a timeout logs the (truncated) transcript so the
        call stays recoverable from logs, exactly like every other
        finalize failure path here.
        """
        await self._settle_ticket_create_task()
        await self._settle_recording_task()
        await self._settle_handoff_prefetch_task()
        await self._drain_flush_queue()

        if not self.call_sid:
            return
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        try:
            await asyncio.wait_for(
                self._write_finalize_result(session_id, body),
                timeout=_FINALIZE_TAIL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _log.error(
                "phone_finalize_timed_out",
                session_id=session_id,
                error=f"closing writes exceeded {_FINALIZE_TAIL_TIMEOUT_SECONDS}s",
                transcript=_log_transcript(body),
            )

    async def _write_finalize_result(self, session_id: str, body: str) -> None:
        if not self.transcript and self.handoff is None:
            # Nobody spoke and nothing was escalated. Nothing to summarise
            # -- but a ticket may already exist from the call-start create,
            # and an empty open conversation must not be left behind.
            #
            # Closing fix: a recorded handoff must NEVER take this branch,
            # even with an empty transcript -- e.g. a request_human_handoff
            # tool call that arrives before the first transcript event. The
            # abandoned-call path resolves the ticket and drops the
            # "[Handoff to human agent]" note entirely, silently closing an
            # escalation nobody handled. self.handoff not None routes into
            # the normal path below instead, which posts the note and keeps
            # status "open".
            try:
                await self._close_abandoned_call(session_id)
            except Exception as e:
                _log.error("phone_finalize_failed", session_id=session_id, error=str(e))
            return
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
            if self.handoff is None and self.nps_score is not None:
                # P8 task 5: attribute to whoever is assigned AT THIS MOMENT
                # (call finalize is the survey-answer instant for phone --
                # there is no later reply to wait for, unlike WhatsApp/email).
                # Best-effort and independent of the tag write itself: a
                # failed assignee lookup must not stop the score recording.
                await record_nps_on_ticket(self._log_port, ticket_id, self.nps_score, "phone")
                agent_id = await self._log_port.get_conversation_assignee(ticket_id)
                if agent_id is not None:
                    await record_nps_agent_attribution(self._log_port, ticket_id, agent_id)
            elif self.handoff is None and self.csat_score is not None:
                await record_csat_on_ticket(self._log_port, ticket_id, self.csat_score, "phone")
        except Exception as e:
            _log.error(
                "phone_finalize_failed",
                session_id=session_id,
                error=str(e),
                transcript=_log_transcript(body),
            )
