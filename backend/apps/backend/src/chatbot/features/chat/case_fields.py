"""The case fields PRO-NET's report decks print, defined once.

Every one of these is a Chatwoot conversation custom attribute -- not a new
table -- because every consumer in this system already reads custom attributes,
and because an agent has to be able to fill them in from the conversation
sidebar without leaving the case.

This module is the single source of truth for the field set: the warehouse
mapper and the entry panel both read `CASE_FIELDS`, so adding a field is one
edit rather than three that can drift.

Two validators earn their keep:

* **Plate normalisation.** `WXY 1234`, `wxy1234` and `WXY-1234` are one car.
  Left alone they enter the warehouse as three, and the vehicle dimension the
  client's monthly report is grouped by becomes worthless. Nothing downstream
  can recover it, so it is fixed at the point of entry.
* **Dealer slugs, not free text.** `purchased_from_dealer` has to join to the
  same dealer dimension the escalation routing keys on. Free text fragments it
  within days.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

# Long enough for a paragraph of context, short enough that nobody pastes a
# transcript into a report column. Over-length is REJECTED, never truncated:
# a half-sentence in front of the client reads as something the agent wrote.
MAX_TEXT = 2000

_PLATE_STRIP = re.compile(r"[^A-Z0-9]")


class InvalidCaseField(ValueError):
    """A value the operator must fix. The message is shown to them, so it
    names what was wrong rather than merely saying it was."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str  # "string" | "enum" | "slug"
    normalise: Callable[[str], str]
    choices: tuple[str, ...] = ()


def _text(value: str) -> str:
    return " ".join(value.split())


def _upper_alnum(value: str) -> str:
    return _PLATE_STRIP.sub("", value.upper())


def _slug(value: str) -> str:
    return value.strip().lower()


CASE_FIELDS: dict[str, FieldSpec] = {
    "case_detail": FieldSpec("case_detail", "string", _text),
    "case_state": FieldSpec("case_state", "string", _text),
    # `hq` is deliberately absent -- see validate().
    "escalated_to": FieldSpec(
        "escalated_to", "enum", _slug, choices=("dealer", "none")
    ),
    "vehicle_plate": FieldSpec("vehicle_plate", "string", _upper_alnum),
    "vehicle_chassis": FieldSpec("vehicle_chassis", "string", _upper_alnum),
    "purchased_from_dealer": FieldSpec("purchased_from_dealer", "slug", _slug),
    "delay_reason": FieldSpec("delay_reason", "string", _text),
    "wip_issue": FieldSpec("wip_issue", "string", _text),
    "wip_action_taken": FieldSpec("wip_action_taken", "string", _text),
    "wip_next_action": FieldSpec("wip_next_action", "string", _text),
}

# Every attribute this package writes, for the mapper and the schema to agree
# on without either importing the other's list.
CASE_FIELD_NAMES: tuple[str, ...] = tuple(CASE_FIELDS)


def validate(name: str, value: Any) -> str | None:
    """Normalise a single field value, or raise `InvalidCaseField`.

    Returns None for a blank value: clearing a field is a legitimate edit, and
    an agent who deletes the text meant to delete it.
    """
    spec = CASE_FIELDS.get(name)
    if spec is None:
        raise InvalidCaseField(f"Unknown case field: {name}")

    text = "" if value is None else str(value)
    if not text.strip():
        return None

    normalised = spec.normalise(text)
    if not normalised:
        return None

    if len(normalised) > MAX_TEXT:
        raise InvalidCaseField(
            f"{name} is {len(normalised)} characters; the limit is {MAX_TEXT}. "
            f"Please shorten it -- it is not truncated automatically."
        )

    if spec.choices and normalised not in spec.choices:
        if name == "escalated_to" and normalised == "hq":
            # Deliberate and temporary. Nothing in this system can yet tell an
            # HQ escalation from any other, so accepting the value would put a
            # plausible wrong number on a client slide.
            raise InvalidCaseField(
                "escalated_to='hq' is not available yet: what counts as an HQ "
                "escalation is client question Q5, still unanswered. Use "
                "'dealer' or 'none'."
            )
        raise InvalidCaseField(
            f"{name} must be one of {', '.join(spec.choices)}; got '{normalised}'."
        )

    return normalised


async def validate_dealer_slug(value: Any, dealer_store: Any | None) -> str | None:
    """Normalise a dealer slug and check it against the dealer store.

    Fail-open on a store outage: an agent must not be blocked from recording
    which dealer sold the car because Firestore hiccuped. The dimension
    tolerates one unvalidated slug far better than it tolerates agents giving
    up on the field.
    """
    normalised = validate("purchased_from_dealer", value)
    if normalised is None or dealer_store is None:
        return normalised
    try:
        record = await dealer_store.get(normalised)
    except Exception as exc:
        _log.warning("case_fields_dealer_lookup_failed", slug=normalised, error=str(exc))
        return normalised
    if record is None:
        raise InvalidCaseField(
            f"'{normalised}' is not a configured dealer. Add it under "
            f"Escalation Routing first, or correct the spelling."
        )
    return normalised
