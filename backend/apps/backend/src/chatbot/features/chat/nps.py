"""Shared NPS recording: the channel-agnostic Zendesk write (comment + tag).

Mirrors csat.py. Posts the identical `nps_<score>` tag + comment that the
metrics dashboard's Phase-1 sync reads (parsed into the conversations.nps_score
column and surfaced via the v_nps view).

P8 task 5 additions (NPS wiring):

- ``should_survey_nps`` -- the sampling gate. **The sampling unit is the
  CONVERSATION, not the message or the customer's individual reply.** It is a
  pure hash of a stable per-conversation key (the WhatsApp/email session id;
  for phone, the caller picks a per-call key before the call is answered --
  see ``phone/bridge.py``), so asking the SAME question twice for the SAME
  conversation -- e.g. a re-nudge after an invalid reply, or a retried
  webhook delivery -- always gets the same answer, deterministically, with no
  new persisted state. Sampling per MESSAGE would risk asking a customer to
  rate the same interaction more than once, which (per the design doc) is a
  worse outcome than not asking at all -- it would also make "NPS replaces
  CSAT rather than being appended to it" impossible to guarantee, since two
  independent per-message coin flips could pick different questions on
  successive nudges of the one survey. ``sample_rate <= 0.0`` always returns
  False and ``>= 1.0`` always returns True without hashing anything, so the
  documented "0.0 means nobody is ever surveyed" guarantee holds by
  construction, not by the hash happening to land outside the bucket.
- ``parse_nps`` -- mirrors ``OrchestratorService.parse_csat``'s shape (first
  standalone integer in free text) but validated against the 0-10 NPS scale.
  An out-of-range reply (e.g. "15") is rejected (returns None, triggering the
  existing nudge-then-give-up flow), never clamped into range -- a clamped
  15-to-10 is a fabricated data point, not the customer's actual answer.
- ``record_nps_agent_attribution`` -- stamps the id of the agent assigned to
  the conversation AT THE MOMENT the customer answers, as its own
  ``nps_agent_<id>`` tag, SEPARATE from the ``nps_<score>`` tag this module's
  ``record_nps_on_ticket`` writes. ``features.metrics.mapping`` prefers this
  tag over the conversation's live (current) assignee when populating
  ``ConversationRow.agent_id`` for a row that carries an NPS score -- see its
  ``_NPS_AGENT_TAG``. The caller resolves the assignee and calls this
  exactly once, at survey-answer time; nothing here (or in mapping.py) ever
  re-derives it from a later "who owns this conversation now" lookup, so a
  reassignment after the survey cannot silently re-attribute an
  already-recorded score.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort

_log = structlog.get_logger(__name__)

_NPS_MIN = 0
_NPS_MAX = 10


def should_survey_nps(key: str, sample_rate: float) -> bool:
    """Deterministically decide whether ONE conversation is sampled for NPS
    (True) or stays on the existing CSAT question (False).

    ``key`` should be a stable identifier for the conversation being
    surveyed (e.g. a WhatsApp/email session id, or ``phone-<CallSid>``) so
    the SAME conversation always gets the SAME answer, however many times
    this is called for it (a re-nudge, a retried webhook delivery, ...) --
    see the module docstring for why the sampling unit must be the
    conversation, not the message.

    ``sample_rate`` is expected to be ``NPS_SAMPLE_RATE`` (0.0-1.0). Values
    at or below 0.0 always return False and values at or above 1.0 always
    return True, without hashing anything -- the "0.0 means nobody is ever
    surveyed" guarantee does not depend on hash behaviour at the boundary.
    """
    if sample_rate <= 0.0:
        return False
    if sample_rate >= 1.0:
        return True
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    # First 4 bytes as an unsigned int, normalised to [0.0, 1.0).
    bucket = int.from_bytes(digest[:4], "big") / 0x1_0000_0000
    return bucket < sample_rate


def parse_nps(text: str) -> int | None:
    """Return the first standalone 0-10 rating in ``text``, else None.

    Mirrors ``OrchestratorService.parse_csat``'s shape for the wider 0-10 NPS
    scale. An out-of-range number (e.g. "15") is rejected, not clamped -- see
    the module docstring.
    """
    for token in re.findall(r"\d+", text or ""):
        n = int(token)
        if _NPS_MIN <= n <= _NPS_MAX:
            return n
    return None


async def record_nps_on_ticket(
    port: ConversationLogPort, ticket_id: str, score: int, channel: str
) -> None:
    """Post the NPS comment + `nps_<score>` tag to a ticket. Best-effort."""
    try:
        await port.append_conversation_comment(
            ticket_id, f"📣 Net Promoter Score: {score}/10 (via {channel})"
        )
        await port.add_ticket_tag(ticket_id, f"nps_{score}")
    except Exception as e:
        _log.error("record_nps_on_ticket_failed", ticket_id=ticket_id, error=str(e))


async def record_nps_agent_attribution(
    port: ConversationLogPort, ticket_id: str, agent_id: str
) -> None:
    """Stamp the id of the agent assigned to this conversation AT THE MOMENT
    the NPS survey is answered, as an `nps_agent_<id>` tag. Best-effort;
    must never raise. See the module docstring for why this is a separate
    write from ``record_nps_on_ticket`` and why it must only ever be called
    once, at answer time.
    """
    try:
        await port.add_ticket_tag(ticket_id, f"nps_agent_{agent_id}")
    except Exception as e:
        _log.error("record_nps_agent_attribution_failed", ticket_id=ticket_id, error=str(e))
