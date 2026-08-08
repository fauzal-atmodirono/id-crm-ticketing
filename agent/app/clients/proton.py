"""Client for the proton-conversational-ai backend's configuration endpoints.

Provides per-inbox agent mode and tenant-level debounce settings fetched from:
  GET {base_url}/kb/inboxes   → list of inbox configs (mode per inbox)
  GET {base_url}/kb/settings  → tenant-level settings (debounce_seconds etc.)

Both are cached in-process with a configurable TTL (default 60 s) to avoid
hammering the backend on every bot event. The cache key is the URL itself; the
value is (data, monotonic_fetch_time). Staleness is checked on each access.

All public methods return None on any failure (network error, non-2xx, missing
key, bad shape) — never raise. This keeps the orchestrator's fail-open pattern:
if the proton backend is unreachable, the agent falls back to global settings.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_DEBOUNCE_SECONDS = 300.0

_LIFECYCLE_TIMING_KEYS = (
    "idle_warn_minutes",
    "idle_close_grace_minutes",
    "idle_close_out_of_hours_grace_minutes",
    "confirm_grace_minutes",
)

_LIFECYCLE_MESSAGE_KEYS = (
    "idle_warning_message", "idle_close_message", "resolution_prompt_message",
    "assign_agent_message", "survey_ai_message", "survey_agent_message", "thanks_message",
)


class ProtonConfigClient:
    """Thin cached client for the proton-conversational-ai config API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        ttl: float = 60.0,
    ) -> None:
        self._ttl = ttl
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"x-api-key": api_key},
            timeout=10.0,
        )
        # Cache entries: path → (data, fetch_monotonic_time)
        self._cache: dict[str, tuple[Any, float]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_cached(self, path: str) -> Any | None:
        """Return cached JSON for *path*, fetching from backend when stale."""
        entry = self._cache.get(path)
        if entry is not None:
            data, fetched_at = entry
            if time.monotonic() - fetched_at < self._ttl:
                return data

        try:
            response = await self._client.get(path)
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: failed to fetch %s", path, exc_info=True)
            return None

        self._cache[path] = (data, time.monotonic())
        return data

    async def effective_inbox_mode(self, inbox_id: int) -> str | None:
        """Return the mode string for *inbox_id* from /kb/inboxes, or None.

        None means the backend is unconfigured, unreachable, or has no row for
        this inbox — caller should fall back to the global agent_mode setting.
        """
        try:
            data = await self._fetch_cached("/kb/inboxes")
            if not isinstance(data, dict):
                return None
            inboxes = data.get("inboxes")
            if not isinstance(inboxes, list):
                return None
            for row in inboxes:
                if isinstance(row, dict) and row.get("inbox_id") == inbox_id:
                    mode = row.get("mode")
                    return str(mode).strip().lower() if mode is not None else None
            return None
        except Exception:
            logger.debug(
                "proton_config: error resolving inbox mode for inbox %s", inbox_id, exc_info=True
            )
            return None

    async def effective_debounce_seconds(self) -> float | None:
        """Return the tenant debounce_seconds from /kb/settings, or None.

        None means the backend is unreachable or the value is missing/invalid
        — caller should fall back to the module-level DEBOUNCE_SECONDS constant.
        """
        try:
            data = await self._fetch_cached("/kb/settings")
            if not isinstance(data, dict):
                return None
            settings = data.get("settings")
            if not isinstance(settings, dict):
                return None
            debounce = settings.get("debounce_seconds")
            if not isinstance(debounce, dict):
                return None
            value = debounce.get("value")
            if value is None:
                return None
            parsed = float(value)
            if not (0 <= parsed <= MAX_DEBOUNCE_SECONDS):
                return None
            return parsed
        except Exception:
            logger.debug("proton_config: error resolving debounce_seconds", exc_info=True)
            return None

    async def get_email_autoack_template(self) -> str | None:
        """Operator-configured email auto-ack body from /kb/settings, or None.

        None means unset/blank, missing, or the backend is unreachable —
        caller (lifecycle.py) should fall back to Settings.email_autoack_template
        (the env-configured default). Shares the same cached /kb/settings fetch
        as effective_debounce_seconds, so no extra HTTP round-trip.
        """
        try:
            data = await self._fetch_cached("/kb/settings")
            if not isinstance(data, dict):
                return None
            settings = data.get("settings")
            if not isinstance(settings, dict):
                return None
            entry = settings.get("email_autoack_template")
            if not isinstance(entry, dict):
                return None
            value = entry.get("value")
            if isinstance(value, str) and value.strip():
                return value
            return None
        except Exception:
            logger.debug("proton_config: error resolving email_autoack_template", exc_info=True)
            return None

    async def _resolve_assistant(self, inbox_id: int | None) -> dict | None:
        """Resolve inbox_id → assistant dict (cached), or None on any failure.

        Fetches /kb/inboxes (cached), finds the row for inbox_id, then fetches
        /kb/assistants/{assistant_id} (cached). Returns the full assistant dict
        or None — never raises.  Both callers (get_assistant_messages and
        get_assistant_persona) share this so the HTTP fetch is cached once.
        """
        try:
            data = await self._fetch_cached("/kb/inboxes")
            if not isinstance(data, dict):
                return None
            inboxes = data.get("inboxes")
            if not isinstance(inboxes, list):
                return None
            assistant_id: str | None = None
            for row in inboxes:
                if isinstance(row, dict) and row.get("inbox_id") == inbox_id:
                    raw = row.get("assistant_id")
                    assistant_id = str(raw) if raw is not None else None
                    break
            if assistant_id is None:
                return None

            assistant_data = await self._fetch_cached(f"/kb/assistants/{assistant_id}")
            if not isinstance(assistant_data, dict):
                return None
            return assistant_data
        except Exception:
            logger.debug(
                "proton_config: error resolving assistant for inbox %s",
                inbox_id,
                exc_info=True,
            )
            return None

    async def get_assistant_messages(self, inbox_id: int) -> dict | None:
        """Return the assistant persona messages for *inbox_id*, or None.

        Steps:
          1. Use the cached /kb/inboxes response to find the row for inbox_id
             and its assistant_id. If no matching row → None.
          2. Fetch GET /kb/assistants/{assistant_id} (cached per assistant_id
             with the same TTL) and extract persona message fields from the
             nested ``config`` dict.

        Returns a dict with keys ``welcome``, ``handoff``, ``resolution``,
        ``idle_warning``, ``idle_close``, ``resolution_prompt``, ``survey_ai``,
        ``survey_agent``, ``thanks``, and ``assign_agent`` (all strings, empty
        string when the field is absent). Returns None on ANY exception,
        non-2xx response, or missing data — never raises.

        Note: ``welcome`` overrides ``DISCLAIMER_DEFAULT`` at conversation
        creation (see lifecycle.py's ``_welcome_text``) — not a separate
        greeting from the AI disclaimer.
        """
        try:
            assistant = await self._resolve_assistant(inbox_id)
            if assistant is None:
                return None
            config = assistant.get("config")
            if not isinstance(config, dict):
                return None

            return {
                "welcome": config.get("welcome_message", "") or "",
                "handoff": config.get("handoff_message", "") or "",
                "resolution": config.get("resolution_message", "") or "",
                "idle_warning": config.get("idle_warning_message", "") or "",
                "idle_close": config.get("idle_close_message", "") or "",
                "resolution_prompt": config.get("resolution_prompt_message", "") or "",
                "survey_ai": config.get("survey_ai_message", "") or "",
                "survey_agent": config.get("survey_agent_message", "") or "",
                "thanks": config.get("thanks_message", "") or "",
                "assign_agent": config.get("assign_agent_message", "") or "",
            }
        except Exception:
            logger.debug(
                "proton_config: error fetching assistant messages for inbox %s",
                inbox_id,
                exc_info=True,
            )
            return None

    async def get_assistant_persona(self, inbox_id: int | None) -> dict | None:
        """Persona fields for shaping the agent-bot decision prompt. Fail-open None.

        Returns a dict with keys ``instructions`` (str), ``guardrails``
        (list[str]), and ``language`` (str) from the resolved assistant config.
        Returns None on ANY exception, non-2xx response, or missing data —
        never raises.  Shares the same cached assistant fetch as
        get_assistant_messages so the two calls together produce only one HTTP
        round-trip per TTL window.
        """
        try:
            assistant = await self._resolve_assistant(inbox_id)
            if assistant is None:
                return None
            config = assistant.get("config", {}) or {}
            return {
                "instructions": config.get("instructions", "") or "",
                "guardrails": list(config.get("guardrails", []) or []),
                "language": config.get("language", "") or "",
            }
        except Exception:
            logger.debug(
                "proton_config: error fetching assistant persona for inbox %s",
                inbox_id,
                exc_info=True,
            )
            return None

    async def get_assistant_lifecycle_timing(
        self, inbox_id: int
    ) -> dict[str, Any] | None:
        """Per-inbox lifecycle timing overrides, or None. Fail-open.

        Reads the four timing keys from the row for *inbox_id* in the cached
        GET /kb/inboxes response (shares the same fetch/TTL as the mode + message
        resolvers, so no extra HTTP round-trip). Each value is an int when set,
        else None (inherit the agent's env default). Returns None when no row
        matches or on any error — never raises.
        """
        try:
            data = await self._fetch_cached("/kb/inboxes")
            if not isinstance(data, dict):
                return None
            inboxes = data.get("inboxes")
            if not isinstance(inboxes, list):
                return None
            row = next(
                (r for r in inboxes if isinstance(r, dict) and r.get("inbox_id") == inbox_id),
                None,
            )
            if row is None:
                return None
            result: dict[str, Any] = {}
            for key in _LIFECYCLE_TIMING_KEYS:
                v = row.get(key)
                result[key] = v if isinstance(v, int) and not isinstance(v, bool) else None
            for mk in _LIFECYCLE_MESSAGE_KEYS:
                mv = row.get(mk)
                result[mk] = mv if isinstance(mv, str) else None
            en = row.get("inactivity_enabled")
            result["inactivity_enabled"] = en if isinstance(en, bool) else None
            return result
        except Exception:
            logger.debug(
                "proton_config: error fetching lifecycle timing for inbox %s",
                inbox_id,
                exc_info=True,
            )
            return None

    async def copilot_answer(
        self, conversation_id: str, thread: list[dict], inbox_id: int | None
    ) -> str | None:
        """KB-grounded answer from the backend copilot (POST /assist/copilot).

        Not cached (per-turn). Fail-open: returns None on any error, non-2xx,
        or empty answer, so the caller can fall back to the local draft."""
        try:
            response = await self._client.post(
                "/assist/copilot",
                json={
                    "conversation_id": conversation_id,
                    "thread": thread,
                    "inbox_id": inbox_id,
                    "assistant_id": None,
                },
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: copilot_answer failed", exc_info=True)
            return None
        if not isinstance(data, dict):
            return None
        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer
        return None

    async def has_pending_kb_documents(self) -> bool:
        """True if GET /kb/knowledge lists at least one document with
        status == "pending". Fail-open: any error, non-2xx, or bad shape
        (already handled inside _fetch_cached) returns False, so the caller
        just falls back to today's behavior instead of blocking on this."""
        data = await self._fetch_cached("/kb/knowledge")
        if not isinstance(data, dict):
            return False
        documents = data.get("documents")
        if not isinstance(documents, list):
            return False
        return any(
            isinstance(doc, dict) and doc.get("status") == "pending"
            for doc in documents
        )

    async def chat_turn(
        self,
        session_id: str,
        text: str,
        inbox_id: int | None = None,
        audio_base64: str | None = None,
        audio_mime_type: str | None = None,
        image_base64: str | None = None,
        image_mime_type: str | None = None,
        video_base64: str | None = None,
        video_mime_type: str | None = None,
    ) -> dict | None:
        """Full conversational turn via the backend ADK agent (POST /chat/turn).

        This is the same agent the Proton website uses: it answers KB/spec
        questions with tools and only signals a handoff on genuine intent.
        Returns the parsed response dict — keys ``reply`` (str|None),
        ``handoff`` (dict|None), ``products`` (list), ``forwarded_to_agent``
        (bool) — or None on any error, non-2xx, or non-dict body (fail-open, so
        the caller can degrade to a Chatwoot handoff). The endpoint is
        unauthenticated; the client's x-api-key header is harmless."""
        try:
            # /chat/turn runs the full ADK agent (KB + Gemini + tools) and
            # routinely takes 10-15s — far longer than the client's default 10s
            # (sized for the fast /kb config endpoints). Override per-request so
            # a normal slow turn isn't mistaken for a failure and fail-opened to
            # a handoff.
            payload: dict = {"session_id": session_id, "text": text}
            if inbox_id is not None:
                payload["inbox_id"] = inbox_id
            if audio_base64 is not None:
                payload["audio_base64"] = audio_base64
            if audio_mime_type is not None:
                payload["audio_mime_type"] = audio_mime_type
            if image_base64 is not None:
                payload["image_base64"] = image_base64
            if image_mime_type is not None:
                payload["image_mime_type"] = image_mime_type
            if video_base64 is not None:
                payload["video_base64"] = video_base64
            if video_mime_type is not None:
                payload["video_mime_type"] = video_mime_type
            response = await self._client.post(
                "/chat/turn",
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: chat_turn failed", exc_info=True)
            return None
        if not isinstance(data, dict):
            return None
        return data

    async def assign_agent(self, conversation_id: int) -> None:
        """Ask the backend to assign the priority agent for this conversation
        (POST /routing/assign). Fail-open: any error is logged and swallowed so
        the handoff (already reopened) is never blocked."""
        try:
            response = await self._client.post(
                "/routing/assign", json={"conversation_id": conversation_id}
            )
            response.raise_for_status()
        except Exception:
            logger.debug("proton_config: assign_agent failed", exc_info=True)

    async def get_escalation_contacts(self) -> dict[str, str] | None:
        """Lower-cased email -> display name for every escalation contact
        (PIC, PIC CC, dealer group members).

        Deliberately NOT cached: this is a security allowlist, and an
        operator adding a dealer in the admin UI must take effect on the
        next reply, not up to a TTL later. Returns None on any failure so
        the caller can tell "unknown sender" from "could not check".
        """
        try:
            response = await self._client.get("/escalation/contacts")
            response.raise_for_status()
            data = response.json()
            contacts = (data or {}).get("contacts") if isinstance(data, dict) else None
            if not isinstance(contacts, list):
                return None
            out: dict[str, str] = {}
            for entry in contacts:
                if not isinstance(entry, dict):
                    continue
                email = str(entry.get("email") or "").strip().lower()
                if email:
                    out[email] = str(entry.get("name") or "")
            return out
        except Exception:
            logger.debug("proton_config: get_escalation_contacts failed", exc_info=True)
            return None

    async def get_escalation_departments(self) -> list[str] | None:
        """Department keys that currently have a PIC configured (GET
        /escalation/departments), for the AI-suggested-department
        classifier's candidate list (`services.dept_suggestion`).

        Deliberately NOT cached, mirroring `get_escalation_contacts`: an
        operator editing PIC routing should be reflected on the very next
        inbound message, not up to a TTL later. Returns None on any failure,
        non-2xx, or bad shape -- never raises -- so the caller can fail-open
        and post no suggestion.
        """
        try:
            response = await self._client.get("/escalation/departments")
            response.raise_for_status()
            data = response.json()
            departments = (data or {}).get("departments") if isinstance(data, dict) else None
            if not isinstance(departments, list):
                return None
            return [str(d) for d in departments if d]
        except Exception:
            logger.debug("proton_config: get_escalation_departments failed", exc_info=True)
            return None

    async def suggest_reply(
        self, conversation_id: str, messages: list[str]
    ) -> str | None:
        """KB-grounded customer-facing draft (POST /assist/suggest). None on
        any failure -- the reply note is posted regardless; only the draft
        is lost."""
        try:
            response = await self._client.post(
                "/assist/suggest",
                json={"conversation_id": conversation_id, "messages": messages},
            )
            response.raise_for_status()
            draft = (response.json() or {}).get("draft")
        except Exception:
            logger.debug("proton_config: suggest_reply failed", exc_info=True)
            return None
        text = str(draft or "").strip()
        return text or None

    async def record_acknowledgement(
        self, conversation_id: int | str, actor: str, remark: str = ""
    ) -> bool:
        """Record that the customer has been acknowledged on this case
        (POST /escalation/acknowledge).

        Fire-and-forget by design: the acknowledgement is an SLA-reporting
        signal, not part of linking the reply. A backend that is down must
        cost the operator a metric, never the note that tells them a dealer
        replied. Returns True only when the backend accepted it, so callers
        can log the difference.
        """
        try:
            response = await self._client.post(
                "/escalation/acknowledge",
                json={
                    "conversation_id": str(conversation_id),
                    "actor": actor,
                    "remark": remark,
                },
            )
            response.raise_for_status()
        except Exception:
            logger.debug("proton_config: record_acknowledgement failed", exc_info=True)
            return False
        return True

    async def notify_email_escalation(
        self,
        conversation_id: int,
        title: str,
        body: str,
        department: str | None,
        dealer: str | None,
        channel_type: str | None = None,
    ) -> bool:
        """Ask the backend to send the EM-7 two-thread email escalation for a
        natively-escalated Email-channel conversation (POST
        /escalation/notify). Never raises: any error is logged and swallowed,
        matching assign_agent's pattern.

        Returns True only when the backend accepted the request. The caller
        (`sync._maybe_notify_escalation`) stamps its once-per-escalation
        guard on that answer, so a send that never happened does not leave a
        stamp behind that permanently suppresses the escalation -- which is
        why this reports success rather than being purely fire-and-forget.
        """
        try:
            response = await self._client.post(
                "/escalation/notify",
                json={
                    "conversation_id": str(conversation_id),
                    "title": title,
                    "body": body,
                    "department": department,
                    "dealer": dealer,
                    # The backend resolves this to a customer-ack transport
                    # (features/chat/escalation_ack.py). Sent as the raw
                    # channel so the mapping lives in exactly one service.
                    "channel_type": channel_type,
                },
            )
            response.raise_for_status()
            return True
        except Exception:
            logger.debug("proton_config: notify_email_escalation failed", exc_info=True)
            return False
