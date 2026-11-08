"""FIX-15 (RFP 2026_028 4.6): hold (with recorded media), resume, and transfer
for a call an agent has answered on the in-CRM softphone.

WHY A CONFERENCE, AND WHY ONLY ON DEMAND
----------------------------------------
Today's handoff bridges caller and agent with a plain `<Dial><Client>` on the
CALLER's leg (handoff_target.dial_twiml / fanout_twiml). A `<Dial>` bridge can
neither hold nor add a third party: redirecting the caller's leg hangs the
agent up, and there is no "hold" on a bridged pair. Twilio's tool for both is a
Conference. Moving every handoff into a conference from the start would mean
rebuilding ringing, stage-1/stage-2 fan-out and the dial-status callbacks that
abandon recording (Task 18) and ACW hang off. So the ringing path is left
exactly as it is, and the call is moved into a conference only when an agent
first presses Hold or Transfer:

1. The AGENT's leg is redirected into room `proton-<caller CallSid>`. That
   ends the caller's `<Dial>` (the child left the bridge), and Twilio posts
   the parent's `<Dial action>` -- our existing dial-status handler, with
   `DialCallStatus=completed`.
2. The dial-status handler asks `caller_twiml_after_dial()` first. For a call
   with a session here it returns hold TwiML (one play of
   PHONE_HOLD_MEDIA_URL, then a redirect to the next segment; see "No caller
   is stranded" below) instead of hanging up. Abandon recording has already
   been queued by then with `completed` = answered, which is correct: the call
   was answered. ACW is NOT entered there; it moves to the conference leave
   event, when the agent really leaves.
3. Resume redirects the caller into the room. A later hold redirects the
   caller out to the hold TwiML again. The caller is never put into the room
   in a held state, so no Participant API (and no ConferenceSid lookup) is
   needed -- only `calls().update`, already used by this package.

Transfer rings the target agent with a REST call to `client:agent_N?...`
(query parameters arrive as `call.customParameters` in their browser, the
same channel the original `<Parameter>`s use, so the target's panel opens the
same conversation and its transcript). The caller waits on hold. When the
target answers, Twilio fetches `/webhooks/phone/transfer/answer`, which brings
the caller into the room and returns the room TwiML for the target. Cold (the
default) hangs the transferring agent up at that point; warm leaves them in
for a three-way. The transferring agent stays on the line until the target
answers in BOTH modes, so a no-answer never strands the caller: the owner can
Resume, and if they already hung up, the transfer status callback apologises
to the caller and ends the call.

All participants join with `endConferenceOnExit=false` because we move the
caller in and out deliberately. Ending the call is done from the conference
`leave` callback instead: the caller leaving while in the room = they hung up
(end the agent legs); the last agent leaving with no transfer ringing = end
the caller's call, the same outcome as an agent hanging up a `<Dial>` today.

NO CALLER IS STRANDED (review fix). Hold is a bounded segment loop: each
hold TwiML plays the recording ONCE (or pauses up to 60 s when no URL is
set) and then `<Redirect>`s to `/webhooks/phone/hold/next`, which plays the
next segment or, once PHONE_HOLD_MAX_SECONDS have passed since the caller was
put on hold, ends the agent legs and says the bilingual apology + `<Hangup/>`.
A fixed `<Play loop=N>` cannot be capped by time without knowing the track's
length (Proton's recording is not supplied yet), and if the redirect webhook
cannot be reached Twilio ends the call itself, so the cap never depends on a
webhook arriving. A caller who hangs up while on hold (outside the room, so
no conference event) is caught by a `statusCallback` set on the caller leg
when the call is first moved, which releases the agents.

A caller `leave` from the room tears the call down only when Twilio says the
caller leg really ended (`fetch_call`) and the event is not older than our
last move of the caller: a late leave from a Hold move must not end a call
that was resumed in the meantime.

Concurrent writers (agent Resume vs. a target answering, two webhooks) are
serialised with a compare-and-set on a `version` field (a Firestore
transaction in production): agent actions get 409 on a conflict, webhooks
re-read and retry.

State is Firestore-backed (multi-worker backend, same as SoftphoneRegistry):
one doc per caller CallSid plus one alias doc per agent leg, so an agent's
request (which only knows its own leg's CallSid) finds the session. Every
store read fails to "no session", which falls back to today's behaviour.

Default OFF (PHONE_HOLD_TRANSFER_ENABLED): with it off nothing is wired, the
dial-status handler has no hook, and the agent routes 404.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from chatbot.features.chat.phone.agent_token import agent_identity
from chatbot.features.chat.phone.twiml import (
    GOOGLE_VOICE_EN_US,
    GOOGLE_VOICE_MS_MY,
    fallback_twiml,
)
from chatbot.features.chat.twilio_signature import verify_twilio_signature

if TYPE_CHECKING:
    from chatbot.features.chat.phone.call_control import CallControl
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_SESSIONS = "phone_call_sessions"
_LEGS = "phone_call_legs"
_XML = '<?xml version="1.0" encoding="UTF-8"?>'
_HANGUP = f"{_XML}<Response><Hangup/></Response>"
_TRANSFER_FAILED_EN = (
    "Sorry, we could not connect you to another agent. "
    "We have noted your call and will get back to you."
)
_TRANSFER_FAILED_MS = (
    "Maaf, kami tidak dapat menyambungkan anda kepada ejen lain. "
    "Panggilan anda telah direkodkan dan kami akan menghubungi anda semula."
)
_UNANSWERED = frozenset({"no-answer", "busy", "failed", "canceled"})
_ENDED = frozenset({"completed", "canceled", "failed", "busy", "no-answer"})
_SILENT_SEGMENT_SECONDS = 60
_WEBHOOK_RETRIES = 3


# --- TwiML ------------------------------------------------------------------


def room_name(caller_sid: str) -> str:
    return f"proton-{caller_sid}"


def conference_twiml(room: str, status_callback: str) -> str:
    """Join `room`. Every participant starts the room (no wait music) and none
    ends it on exit -- see the module docstring for who ends the call."""
    return (
        f"{_XML}<Response><Dial><Conference"
        ' startConferenceOnEnter="true" endConferenceOnExit="false" beep="false"'
        f" statusCallback={quoteattr(status_callback)}"
        ' statusCallbackEvent="leave" statusCallbackMethod="POST">'
        f"{escape(room)}</Conference></Dial></Response>"
    )


def hold_twiml(media_url: str, next_url: str, remaining_seconds: float) -> str:
    """ONE hold segment, then back to `next_url` (which enforces the cap).
    Blank URL = a silent pause, capped at what is left of the hold window."""
    if media_url.strip():
        body = f'<Play loop="1">{escape(media_url.strip())}</Play>'
    else:
        pause = max(1, min(_SILENT_SEGMENT_SECONDS, math.ceil(remaining_seconds)))
        body = f'<Pause length="{pause}"/>'
    return f'{_XML}<Response>{body}<Redirect method="POST">{escape(next_url)}</Redirect></Response>'


def hold_expired_twiml() -> str:
    """Bilingual apology + `<Hangup/>` (fallback_twiml always ends the call)."""
    return fallback_twiml(
        [
            (_TRANSFER_FAILED_EN, "en-US", GOOGLE_VOICE_EN_US),
            (_TRANSFER_FAILED_MS, "ms-MY", GOOGLE_VOICE_MS_MY),
        ]
    )


_transfer_failed_twiml = hold_expired_twiml


# --- state ------------------------------------------------------------------


@dataclass
class CallSession:
    caller_sid: str
    room: str
    owner_agent_id: int
    # agent leg CallSid -> Chatwoot user id, for every agent currently on it
    legs: dict[str, int] = field(default_factory=dict)
    caller_state: str = "held"  # held | conference
    conversation_id: str = ""
    caller_from: str = ""
    # {target_agent_id, target_leg_sid, mode, from_agent_id} while ringing
    transfer: dict[str, Any] | None = None
    held_since: float = 0.0  # epoch seconds the caller was last put on hold
    caller_moved_at: float = 0.0  # epoch seconds of our last caller redirect
    version: int = 0  # compare-and-set; 0 = never written

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CallSession | None:
        try:
            return cls(
                caller_sid=str(d["caller_sid"]),
                room=str(d["room"]),
                owner_agent_id=int(d["owner_agent_id"]),
                legs={str(k): int(v) for k, v in (d.get("legs") or {}).items()},
                caller_state=str(d.get("caller_state") or "held"),
                conversation_id=str(d.get("conversation_id") or ""),
                caller_from=str(d.get("caller_from") or ""),
                transfer=d.get("transfer") or None,
                held_since=float(d.get("held_since") or 0.0),
                caller_moved_at=float(d.get("caller_moved_at") or 0.0),
                version=int(d.get("version") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None


class _FirestoreCollection:
    def __init__(self, client: Any, ref: Any) -> None:
        self._client = client
        self._ref = ref

    def get(self, doc_id: str) -> dict[str, Any] | None:
        snap = self._ref.document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def set(self, doc_id: str, data: dict[str, Any]) -> None:
        self._ref.document(doc_id).set(data)

    def delete(self, doc_id: str) -> None:
        self._ref.document(doc_id).delete()

    def cas(self, doc_id: str, expected_version: int, data: dict[str, Any]) -> bool:
        """Write `data` only if the stored `version` is still `expected_version`
        (absent doc = 0), inside a Firestore transaction."""
        from google.cloud import firestore  # noqa: PLC0415

        ref = self._ref.document(doc_id)

        @firestore.transactional
        def _run(tx: Any) -> bool:
            snap = ref.get(transaction=tx)
            current = int((snap.to_dict() or {}).get("version") or 0) if snap.exists else 0
            if current != expected_version:
                return False
            tx.set(ref, data)
            return True

        return bool(_run(self._client.transaction()))


class CallSessionStore:
    """`_sessions` / `_legs` are plain, lazily built attributes so tests can
    swap in a dict-backed get/set/delete/cas stand-in (SoftphoneRegistry's
    pattern). Reads fail to None, writes log and return False."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: Any = None
        self._legs: Any = None

    def _coll(self, name: str) -> Any:
        attr = "_sessions" if name == _SESSIONS else "_legs"
        if getattr(self, attr) is None:
            from google.cloud import firestore  # noqa: PLC0415

            client = firestore.Client(
                project=self._settings.firestore_project_id,
                database=self._settings.firestore_database_id,
            )
            setattr(self, attr, _FirestoreCollection(client, client.collection(name)))
        return getattr(self, attr)

    async def get(self, caller_sid: str) -> CallSession | None:
        try:
            d = await asyncio.to_thread(self._coll(_SESSIONS).get, caller_sid)
        except Exception as e:
            _log.error("call_session_read_failed", caller_sid=caller_sid, error=str(e))
            return None
        return CallSession.from_dict(d) if d else None

    async def caller_for_leg(self, leg_sid: str) -> str | None:
        try:
            d = await asyncio.to_thread(self._coll(_LEGS).get, leg_sid)
        except Exception as e:
            _log.error("call_leg_alias_read_failed", leg_sid=leg_sid, error=str(e))
            return None
        return str(d["caller_sid"]) if d and d.get("caller_sid") else None

    async def save(self, s: CallSession, dropped_legs: tuple[str, ...] = ()) -> bool:
        """Compare-and-set on `s.version`. False = someone else wrote first
        (re-read and decide again) or the store is down. On success
        `s.version` is the new stored version."""
        data = asdict(s)
        data["version"] = s.version + 1
        try:
            ok = await asyncio.to_thread(self._coll(_SESSIONS).cas, s.caller_sid, s.version, data)
            if not ok:
                _log.info("call_session_write_conflict", caller_sid=s.caller_sid)
                return False
            s.version += 1
            for leg in s.legs:
                await asyncio.to_thread(self._coll(_LEGS).set, leg, {"caller_sid": s.caller_sid})
            for leg in dropped_legs:
                await asyncio.to_thread(self._coll(_LEGS).delete, leg)
            return True
        except Exception as e:
            _log.error("call_session_write_failed", caller_sid=s.caller_sid, error=str(e))
            return False

    async def delete(self, s: CallSession, extra_legs: tuple[str, ...] = ()) -> None:
        try:
            await asyncio.to_thread(self._coll(_SESSIONS).delete, s.caller_sid)
            for leg in (*s.legs, *extra_legs):
                await asyncio.to_thread(self._coll(_LEGS).delete, leg)
        except Exception as e:
            _log.error("call_session_delete_failed", caller_sid=s.caller_sid, error=str(e))


# --- controller -------------------------------------------------------------


class HoldTransferHook(Protocol):
    """What the dial-status handler needs (ChatRouter's `hold_transfer`)."""

    async def caller_twiml_after_dial(self, caller_sid: str) -> str | None: ...


class HoldTransferError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_RETRY = object()  # webhook sentinel: CAS conflict, re-read and re-decide


def _conflict() -> HoldTransferError:
    return HoldTransferError(409, "The call changed meanwhile; try again")


def _event_epoch(timestamp: str) -> float | None:
    """Twilio's RFC 2822 `Timestamp` as epoch seconds, or None."""
    if not timestamp:
        return None
    try:
        return parsedate_to_datetime(timestamp).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


class HoldTransferController:
    def __init__(
        self,
        settings: Settings,
        *,
        call_control: CallControl,
        store: CallSessionStore,
        registry: Any,
        presence: Any,
        find_ticket: Callable[[str], Awaitable[str | None]],
        on_agent_leave: Callable[[int], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._cc = call_control
        self._store = store
        self._registry = registry
        self._presence = presence
        self._find_ticket = find_ticket
        self._on_agent_leave = on_agent_leave
        self._clock = clock

    # --- helpers --------------------------------------------------------

    def _url(self, path: str, caller_sid: str) -> str:
        base = self._settings.twilio_webhook_base_url.rstrip("/")
        return f"{base}{path}?{urlencode({'caller_sid': caller_sid})}"

    def _room_twiml(self, s: CallSession) -> str:
        return conference_twiml(
            s.room, self._url("/webhooks/phone/conference-status", s.caller_sid)
        )

    def _remaining_hold(self, s: CallSession) -> float:
        return self._settings.phone_hold_max_seconds - (self._clock() - s.held_since)

    def _hold_twiml(self, s: CallSession) -> str:
        return hold_twiml(
            self._settings.phone_hold_media_url,
            self._url("/webhooks/phone/hold/next", s.caller_sid),
            self._remaining_hold(s),
        )

    def _mark_held(self, s: CallSession) -> None:
        now = self._clock()
        s.caller_state = "held"
        s.held_since = now
        s.caller_moved_at = now

    def _mark_in_room(self, s: CallSession) -> None:
        s.caller_state = "conference"
        s.caller_moved_at = self._clock()

    async def notify_agent_left(self, agent_id: int) -> None:
        """ACW hook; run as a background task, never raises."""
        if self._on_agent_leave is None:
            return
        try:
            await self._on_agent_leave(agent_id)
        except Exception as e:
            _log.error("hold_transfer_agent_leave_hook_failed", agent_id=agent_id, error=str(e))

    async def _teardown(self, s: CallSession, *, end_caller: bool) -> list[int]:
        """End every agent leg and a ringing transfer, optionally the caller,
        drop the session. Returns the agents released (for ACW)."""
        for leg in s.legs:
            await self._cc.end_call(leg)
        if s.transfer:
            await self._cc.end_call(str(s.transfer.get("target_leg_sid") or ""), ringing=True)
        if end_caller:
            await self._cc.end_call(s.caller_sid)
        await self._store.delete(s)
        return sorted(set(s.legs.values()))

    async def _authorise(self, agent_id: int, call_sid: str) -> tuple[CallSession, bool]:
        """The session for `call_sid` if `agent_id` owns it. Returns
        (session, is_new). Ownership is proven server-side only: either our
        own session record says this leg is this agent's and they own the call,
        or Twilio says the leg was dialled to `client:agent_<id>` and is live.
        """
        if not self._settings.twilio_webhook_base_url:
            raise HoldTransferError(503, "TWILIO_WEBHOOK_BASE_URL not configured")
        caller_sid = await self._store.caller_for_leg(call_sid)
        if caller_sid:
            s = await self._store.get(caller_sid)
            if s and s.legs.get(call_sid) == agent_id and s.owner_agent_id == agent_id:
                return s, False
            raise HoldTransferError(403, "Not your call")
        leg = await self._cc.fetch_call(call_sid)
        to = (leg or {}).get("to", "")
        identity = to[len("client:") :].split("?", 1)[0] if to.startswith("client:") else ""
        if leg is None or identity != agent_identity(agent_id):
            raise HoldTransferError(403, "Not your call")
        if leg.get("status") != "in-progress":
            raise HoldTransferError(409, "Call is not in progress")
        parent = leg.get("parent_call_sid")
        if not parent:
            raise HoldTransferError(403, "Not a transferred customer call")
        if await self._store.get(parent) is not None:
            raise HoldTransferError(403, "Not your call")
        try:
            conversation_id = await self._find_ticket(f"phone-{parent}") or ""
        except Exception as e:
            _log.warning("hold_transfer_ticket_lookup_failed", caller_sid=parent, error=str(e))
            conversation_id = ""
        s = CallSession(
            caller_sid=parent,
            room=room_name(parent),
            owner_agent_id=agent_id,
            legs={call_sid: agent_id},
            caller_state="held",
            conversation_id=str(conversation_id),
            caller_from=str(leg.get("from") or ""),
        )
        return s, True

    async def _convert(self, s: CallSession, agent_leg: str) -> None:
        """Move a `<Dial>`-bridged call into the room, caller on hold. The
        session is written BEFORE the redirect: the redirect makes Twilio
        post dial-status, which must find it."""
        self._mark_held(s)
        if not await self._store.save(s):
            raise HoldTransferError(409, "Call state unavailable or changed; try again")
        if not await self._cc.redirect(agent_leg, self._room_twiml(s)):
            await self._store.delete(s)
            raise HoldTransferError(502, "Could not move the call to hold")
        # Caller hang-up while held happens outside the room, so no conference
        # event reports it: watch the caller leg itself. Fail-open (the hold
        # cap still ends the call).
        if not await self._cc.set_status_callback(
            s.caller_sid, self._url("/webhooks/phone/caller-status", s.caller_sid)
        ):
            _log.warning("hold_transfer_caller_status_callback_not_set", caller_sid=s.caller_sid)

    async def _caller_to_hold(self, s: CallSession) -> None:
        prev = (s.caller_state, s.held_since, s.caller_moved_at)
        self._mark_held(s)  # set first: our redirect fires a leave event
        if not await self._store.save(s):
            raise _conflict()
        if not await self._cc.redirect(s.caller_sid, self._hold_twiml(s)):
            s.caller_state, s.held_since, s.caller_moved_at = prev
            await self._store.save(s)
            raise HoldTransferError(502, "Could not put the caller on hold")

    # --- agent actions --------------------------------------------------

    async def hold(self, agent_id: int, call_sid: str) -> dict[str, Any]:
        s, new = await self._authorise(agent_id, call_sid)
        if new:
            await self._convert(s, call_sid)
        elif s.caller_state == "held":
            raise HoldTransferError(409, "Caller is already on hold")
        else:
            await self._caller_to_hold(s)
        _log.info("phone_call_held", caller_sid=s.caller_sid, agent_id=agent_id)
        return {"state": "held"}

    async def resume(self, agent_id: int, call_sid: str) -> dict[str, Any]:
        s, new = await self._authorise(agent_id, call_sid)
        if new or s.caller_state != "held":
            raise HoldTransferError(409, "Caller is not on hold")
        ringing = str((s.transfer or {}).get("target_leg_sid") or "")
        s.transfer = None
        self._mark_in_room(s)
        # CAS first: if the target answered meanwhile, their answer wrote first
        # and this resume must not also act on the call.
        if not await self._store.save(s):
            raise _conflict()
        if ringing:
            await self._cc.end_call(ringing, ringing=True)
        if not await self._cc.redirect(s.caller_sid, self._room_twiml(s)):
            s.caller_state = "held"
            await self._store.save(s)
            raise HoldTransferError(502, "Could not bring the caller back")
        _log.info("phone_call_resumed", caller_sid=s.caller_sid, agent_id=agent_id)
        return {"state": "connected"}

    async def transfer_targets(self, agent_id: int) -> list[dict[str, Any]]:
        """Registered softphone AND Chatwoot `online`, minus the asker -- the
        same eligibility rule as the stage-2 fan-out."""
        try:
            registered = await self._registry.registered_ids()
            agents = await self._presence.fetch_agents()
        except Exception as e:
            _log.error("hold_transfer_targets_lookup_failed", error=str(e))
            return []
        return sorted(
            (
                {"id": a.id, "name": a.name}
                for a in agents
                if a.id in registered and a.availability_status == "online" and a.id != agent_id
            ),
            key=lambda a: (a["name"].lower(), a["id"]),
        )

    async def transfer(
        self, agent_id: int, call_sid: str, target_agent_id: int, mode: str = "cold"
    ) -> dict[str, Any]:
        if mode not in ("cold", "warm"):
            raise HoldTransferError(422, "mode must be cold or warm")
        if target_agent_id == agent_id:
            raise HoldTransferError(422, "Cannot transfer to yourself")
        s, new = await self._authorise(agent_id, call_sid)
        if s.transfer:
            raise HoldTransferError(409, "A transfer is already ringing")
        targets = {t["id"] for t in await self.transfer_targets(agent_id)}
        if target_agent_id not in targets:
            raise HoldTransferError(409, "That agent is not available")
        from_ = (
            self._settings.phone_handoff_caller_id.strip()
            or self._settings.twilio_phone_number.strip()
        )
        if not from_:
            raise HoldTransferError(503, "No caller id configured for transfers")

        if new:
            await self._convert(s, call_sid)
        elif s.caller_state == "conference":
            await self._caller_to_hold(s)

        params = {
            "conversation_id": s.conversation_id,
            "transcript_path": (
                f"/app/accounts/{self._settings.chatwoot_account_id}"
                f"/conversations/{s.conversation_id}"
                if s.conversation_id
                else ""
            ),
            "caller": s.caller_from,
            "transferred_by": str(agent_id),
            "reason": "Transferred call",
        }
        query = urlencode({k: v for k, v in params.items() if v})
        target_sid = await self._cc.create_client_call(
            to=f"client:{agent_identity(target_agent_id)}?{query}",
            from_=from_,
            url=self._url("/webhooks/phone/transfer/answer", s.caller_sid),
            status_callback=self._url("/webhooks/phone/transfer/status", s.caller_sid),
            ring_timeout=self._settings.phone_agent_ring_timeout_seconds,
        )
        if target_sid is None:
            # Caller stays on hold with the owner still on the line: Resume works.
            raise HoldTransferError(502, "Could not ring that agent")
        s.transfer = {
            "target_agent_id": target_agent_id,
            "target_leg_sid": target_sid,
            "mode": mode,
            "from_agent_id": agent_id,
        }
        if not await self._store.save(s):
            await self._cc.end_call(target_sid, ringing=True)
            raise _conflict()
        _log.info(
            "phone_call_transfer_ringing",
            caller_sid=s.caller_sid,
            from_agent=agent_id,
            to_agent=target_agent_id,
            mode=mode,
        )
        return {"state": "transferring", "target_agent_id": target_agent_id, "mode": mode}

    # --- Twilio callbacks -----------------------------------------------

    async def caller_twiml_after_dial(self, caller_sid: str) -> str | None:
        """Called by the dial-status handler before its own logic. None = not
        ours, carry on exactly as today."""
        s = await self._store.get(caller_sid)
        if s is None:
            return None
        return self._hold_twiml(s) if s.caller_state == "held" else self._room_twiml(s)

    async def on_hold_next(self, caller_sid: str) -> tuple[str, list[int]]:
        """End of one hold segment: the next one, or -- past
        PHONE_HOLD_MAX_SECONDS -- release the agents and apologise + hang up.
        No session (agents gone, or the store unreadable) also ends the call:
        bounded, never an endless loop."""
        s = await self._store.get(caller_sid)
        if s is None:
            return hold_expired_twiml(), []
        if s.caller_state != "held":
            return self._room_twiml(s), []
        if self._remaining_hold(s) > 0:
            return self._hold_twiml(s), []
        _log.warning("phone_hold_max_reached", caller_sid=caller_sid)
        released = await self._teardown(s, end_caller=False)
        return hold_expired_twiml(), released

    async def on_caller_status(self, caller_sid: str, status: str) -> list[int]:
        """The caller leg ended (status callback set at conversion): release
        whoever is left. Covers a hang-up while on hold, outside the room."""
        if status not in _ENDED:
            return []
        s = await self._store.get(caller_sid)
        if s is None:
            return []
        _log.info("phone_held_call_caller_ended", caller_sid=caller_sid, status=status)
        return await self._teardown(s, end_caller=False)

    async def _retry(self, fn: Callable[[], Awaitable[Any]], default: Any) -> Any:
        """Webhooks re-read and re-decide on a CAS conflict."""
        for _ in range(_WEBHOOK_RETRIES):
            result = await fn()
            if result is not _RETRY:
                return result
        _log.error("hold_transfer_webhook_gave_up_after_conflicts")
        return default

    async def on_transfer_answer(self, caller_sid: str, leg_sid: str) -> tuple[str, list[int]]:
        """Target agent answered. Returns (TwiML for the target, agent ids who
        left and should enter ACW)."""
        return await self._retry(
            lambda: self._transfer_answer_once(caller_sid, leg_sid), (_HANGUP, [])
        )

    async def _transfer_answer_once(self, caller_sid: str, leg_sid: str) -> Any:
        s = await self._store.get(caller_sid)
        t = s.transfer if s else None
        if s is None or not t or t.get("target_leg_sid") != leg_sid:
            _log.info("phone_transfer_answer_stale", caller_sid=caller_sid, leg_sid=leg_sid)
            return _HANGUP, []
        target = int(t["target_agent_id"])
        from_agent = int(t["from_agent_id"])
        s.legs[leg_sid] = target
        s.owner_agent_id = target
        s.transfer = None
        self._mark_in_room(s)
        dropped: tuple[str, ...] = ()
        if t.get("mode") == "cold":
            dropped = tuple(leg for leg, a in s.legs.items() if a == from_agent)
            for leg in dropped:
                del s.legs[leg]
        if not await self._store.save(s, dropped_legs=dropped):
            return _RETRY  # e.g. the owner pressed Resume at the same moment
        # Old agent off first (cold), so they do not overhear the hand-over.
        for leg in dropped:
            await self._cc.end_call(leg)
        if not await self._cc.redirect(s.caller_sid, self._room_twiml(s)):
            _log.error("phone_transfer_caller_rejoin_failed", caller_sid=caller_sid)
        _log.info("phone_call_transferred", caller_sid=caller_sid, to_agent=target)
        return self._room_twiml(s), ([from_agent] if dropped else [])

    async def on_transfer_status(self, caller_sid: str, leg_sid: str, status: str) -> None:
        if status not in _UNANSWERED:
            return
        await self._retry(lambda: self._transfer_status_once(caller_sid, leg_sid, status), None)

    async def _transfer_status_once(self, caller_sid: str, leg_sid: str, status: str) -> Any:
        s = await self._store.get(caller_sid)
        if s is None or not s.transfer or s.transfer.get("target_leg_sid") != leg_sid:
            return None
        _log.info("phone_transfer_unanswered", caller_sid=caller_sid, status=status)
        s.transfer = None
        if s.legs:
            # Owner still on: they can Resume.
            return None if await self._store.save(s) else _RETRY
        await self._cc.redirect(s.caller_sid, _transfer_failed_twiml())
        await self._store.delete(s)
        return None

    async def on_conference_leave(
        self, caller_sid: str, leaver_sid: str, event_timestamp: str = ""
    ) -> list[int]:
        """Returns agent ids who left (for ACW)."""
        return await self._retry(
            lambda: self._leave_once(caller_sid, leaver_sid, event_timestamp), []
        )

    async def _leave_once(self, caller_sid: str, leaver_sid: str, event_timestamp: str) -> Any:
        s = await self._store.get(caller_sid)
        if s is None:
            return []
        if leaver_sid == s.caller_sid:
            return await self._caller_left(s, event_timestamp)
        if leaver_sid not in s.legs:
            return []
        agent = s.legs.pop(leaver_sid)
        if s.legs or s.transfer:
            # Others still on, or the caller waits for a ringing target.
            if s.legs and s.owner_agent_id == agent:
                s.owner_agent_id = next(iter(s.legs.values()))
            if not await self._store.save(s, dropped_legs=(leaver_sid,)):
                return _RETRY
        else:
            await self._cc.end_call(s.caller_sid)
            await self._store.delete(s, extra_legs=(leaver_sid,))
        return [agent]

    async def _caller_left(self, s: CallSession, event_timestamp: str) -> list[int]:
        """Tear down only for a REAL caller hang-up. Our own moves of the caller
        also produce leave events, and one can arrive late (after a fast
        Resume put the caller back): trusting state at arrival would end a
        live call and leave the caller alone in a silent room."""
        if s.caller_state == "held":
            return []  # our own redirect to hold
        at = _event_epoch(event_timestamp)
        if at is not None and at < math.floor(s.caller_moved_at):
            _log.info("phone_conference_leave_stale", caller_sid=s.caller_sid)
            return []
        leg = await self._cc.fetch_call(s.caller_sid)
        if leg is None or leg.get("status") not in _ENDED:
            # Still live (or unknown): the caller-status callback and the hold
            # cap cover a real hang-up we cannot confirm here.
            _log.info("phone_conference_leave_caller_still_live", caller_sid=s.caller_sid)
            return []
        return await self._teardown(s, end_caller=False)


# --- Twilio webhooks --------------------------------------------------------


def build_hold_transfer_webhooks_router(
    settings: Settings, controller: HoldTransferController
) -> APIRouter:
    """Twilio-signed callbacks. Mounted only when PHONE_HOLD_TRANSFER_ENABLED
    is on. The signature covers the query string, which is where we put
    `caller_sid` ourselves, so it cannot be forged."""
    router = APIRouter(tags=["voice-hold-transfer"])

    async def _verified(request: Request) -> tuple[dict[str, str], str]:
        token = settings.twilio_auth_token
        if not token:
            raise HTTPException(status_code=401, detail="Webhook not configured")
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        base = settings.twilio_webhook_base_url
        query = f"?{request.url.query}" if request.url.query else ""
        url = f"{base.rstrip('/')}{request.url.path}{query}" if base else str(request.url)
        if not verify_twilio_signature(
            token, url, params, request.headers.get("X-Twilio-Signature")
        ):
            _log.warning("hold_transfer_webhook_signature_invalid", path=request.url.path)
            raise HTTPException(status_code=401, detail="Invalid signature")
        return params, request.query_params.get("caller_sid", "")

    def _acw(background_tasks: BackgroundTasks, agents: list[int]) -> None:
        for a in agents:
            background_tasks.add_task(controller.notify_agent_left, a)

    @router.post("/webhooks/phone/conference-status")
    async def conference_status(request: Request, background_tasks: BackgroundTasks) -> dict:
        params, caller_sid = await _verified(request)
        if params.get("StatusCallbackEvent") == "participant-leave" and caller_sid:
            _acw(
                background_tasks,
                await controller.on_conference_leave(
                    caller_sid, params.get("CallSid", ""), params.get("Timestamp", "")
                ),
            )
        return {"status": "ok"}

    @router.post("/webhooks/phone/caller-status")
    async def caller_status(request: Request, background_tasks: BackgroundTasks) -> dict:
        params, caller_sid = await _verified(request)
        if caller_sid:
            _acw(
                background_tasks,
                await controller.on_caller_status(caller_sid, params.get("CallStatus", "")),
            )
        return {"status": "ok"}

    @router.post("/webhooks/phone/hold/next")
    async def hold_next(request: Request, background_tasks: BackgroundTasks) -> Response:
        _params, caller_sid = await _verified(request)
        twiml, released = await controller.on_hold_next(caller_sid)
        _acw(background_tasks, released)
        return Response(content=twiml, media_type="application/xml")

    @router.post("/webhooks/phone/transfer/answer")
    async def transfer_answer(request: Request, background_tasks: BackgroundTasks) -> Response:
        params, caller_sid = await _verified(request)
        twiml, left = await controller.on_transfer_answer(caller_sid, params.get("CallSid", ""))
        _acw(background_tasks, left)
        return Response(content=twiml, media_type="application/xml")

    @router.post("/webhooks/phone/transfer/status")
    async def transfer_status(request: Request) -> dict:
        params, caller_sid = await _verified(request)
        await controller.on_transfer_status(
            caller_sid, params.get("CallSid", ""), params.get("CallStatus", "")
        )
        return {"status": "ok"}

    return router
