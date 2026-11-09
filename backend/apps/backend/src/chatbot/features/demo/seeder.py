"""Seed, reset and describe the [DEMO] personas in the CRM.

Rehearsals and the 17 Nov demo need the phone personas (personas.json next
to this file -- same ids/numbers as the Phone tab's personas.ts) to exist in
a known state: their ``[DEMO]`` Chatwoot contact with the fake number and
the vehicle attributes the caller lookup reads (``vehicle_no`` /
``vehicle_model`` on the contact; ``vehicle_plate`` / ``vehicle_model`` and
the dealer as ``caller_dealer`` on each conversation -- never a
``dealer_<slug>`` label, ruling R29), their history, and Farid's past RSA
incident for Customer 360.

**Seed** is idempotent: a contact this seeder owns is reused (attributes
merged), and its history is only created when it has no marker-stamped
conversation yet. A persona whose number is held by a contact we do not
own is SKIPPED and reported -- Chatwoot keeps phone numbers unique, and
that contact is not ours to change.

**Reset** removes only what this seeder owns (``safety`` has the rules;
other "[DEMO]" data on the tenant -- seed_demo_data contacts, renamed
handsets -- is never ours). Contacts are found by the persona numbers only,
never by a tenant-wide "[DEMO]" search. Each owned conversation is
resolved, then deleted; then each owned contact whose conversations are
ALL ours (otherwise it is kept and reported); then the demo RSA incidents;
then it seeds again. It runs on Proton's live tenant, so:

* it refuses unless the target inbox is an API inbox (``Channel::Api``),
  checked BEFORE anything is written or deleted: never the WhatsApp inbox,
  whose outgoing messages reach real people;
* every write goes through ``DemoCrmClient``, which re-reads the target and
  refuses a record it does not own (``DemoSafetyError``); a refused or
  failed delete is reported and the run carries on;
* contacts that are NOT demo records but hold a persona number (typically
  the "New caller" contact a rehearsal call created) are reported, never
  touched.

**History without the bot answering it.** Chatwoot forces ``pending`` on a
new conversation when the inbox has an agent bot, and the agent bot answers
an incoming customer message on a pending conversation. So a conversation
is moved to ``open`` before its ``[DEMO]`` incoming message is posted, and
only then to its final status. No ``escalate`` or ``dealer_*`` label is
ever applied (they drive the escalation email / dealer routing on a live
tenant).

**Live-tenant side effects of seeding** (same as any conversation on that
inbox): auto-assignment may assign the open conversation to an agent; the
agent lifecycle may post its disclaimer, and on resolve the CSAT / rating
survey (outgoing on the API inbox, never relayed: the source id is
``demo-...``); department suggestion and duplicate detection may write
their own labels/attributes; SLA scans may alert on Nurul's open case.
Seeded cases count in native Chatwoot reports until Reset; only the
BigQuery sync drops them, and only with METRICS_EXCLUDE_DEMO_SEED=true.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import structlog

from chatbot.features.chat.customer360_router import _phones_match, _plate_key
from chatbot.features.demo.crm import ChatwootError, DemoCrmClient
from chatbot.features.demo.personas import load_personas, seeded_numbers, seeded_plates
from chatbot.features.demo.safety import (
    AUDIT_LABEL,
    CREATABLE_LABELS,
    DEMO_LABEL,
    DEMO_SEED_MARKER,
    DEMO_SOURCE_PREFIX,
    RSA_CREATED_BY,
    DemoSafetyError,
    assert_demo_rsa,
    assert_demo_text,
    has_seeder_markers,
    is_demo_rsa,
)

if TYPE_CHECKING:
    from chatbot.features.rsa.rsa_repository import RsaRepositoryPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_API_CHANNEL = "Channel::Api"

__all__ = ["DemoSeeder", "build_seeder_factory", "load_personas"]


def _conv_sort_key(conv: dict[str, Any]) -> tuple[int, int]:
    def num(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return (num(conv.get("last_activity_at") or conv.get("created_at")), num(conv.get("id")))


class DemoSeeder:
    def __init__(
        self,
        settings: Settings,
        crm: DemoCrmClient,
        *,
        rsa_repo: RsaRepositoryPort | None = None,
        personas: list[dict[str, Any]] | None = None,
    ) -> None:
        self._crm = crm
        self._rsa = rsa_repo
        self._personas = personas if personas is not None else load_personas()
        self._inbox_id = int(settings.demo_inbox_id or settings.chatwoot_inbox_id or 0)
        self._account_id = settings.chatwoot_account_id
        self._crm_base = settings.demo_crm_base_url.rstrip("/")
        self._numbers = seeded_numbers(self._personas)
        self._plates = {_plate_key(p) for p in seeded_plates(self._personas)}
        self._plates_by_number = {
            p["number"]: {_plate_key(x) for x in seeded_plates([p])}
            for p in self._personas
            if p.get("number")
        }

    # --- small helpers --------------------------------------------------------------

    def target(self) -> dict[str, int]:
        return {"account_id": self._account_id, "inbox_id": self._inbox_id}

    def _link(self, path: str) -> str:
        return f"{self._crm_base}/app/accounts/{self._account_id}{path}"

    async def _check_inbox(self) -> None:
        if not self._inbox_id:
            raise DemoSafetyError("no inbox configured (DEMO_INBOX_ID / CHATWOOT_INBOX_ID)")
        channel = (await self._crm.get_inbox(self._inbox_id)).get("channel_type")
        if channel != _API_CHANNEL:
            raise DemoSafetyError(
                f"inbox {self._inbox_id} is {channel!r}, not an API inbox ({_API_CHANNEL}); "
                "refusing to write demo data there"
            )

    async def _ensure_labels(self) -> None:
        existing = await self._crm.list_label_titles()
        for title in sorted(CREATABLE_LABELS - existing):
            await self._crm.create_label(title)

    async def _contacts_for_number(self, number: str) -> list[dict[str, Any]]:
        if not number:
            return []
        return [
            c
            for c in await self._crm.search_contacts(number)
            if c.get("id") is not None and _phones_match(number, c.get("phone_number"))
        ]

    def _seedable(self) -> list[dict[str, Any]]:
        return [p for p in self._personas if p.get("crm_seed") and p.get("number")]

    # --- seed ----------------------------------------------------------------------

    async def seed(self) -> dict[str, Any]:
        await self._check_inbox()
        await self._ensure_labels()
        report: dict[str, Any] = {
            "target": self.target(),
            "created_contacts": [],
            "updated_contacts": [],
            "created_conversations": 0,
            "skipped_history": [],
            "rsa_created": 0,
            "conflicts": [],
            "errors": [],
        }
        for persona in self._seedable():
            await self._seed_persona(persona, report)
        _log.info(
            "demo_seed_done",
            created_contacts=len(report["created_contacts"]),
            created_conversations=report["created_conversations"],
            conflicts=len(report["conflicts"]),
        )
        return report

    async def _seed_persona(self, persona: dict[str, Any], report: dict[str, Any]) -> None:
        pid = persona["id"]
        spec = persona["crm_seed"]
        contact_spec = spec["contact"]
        number = persona["number"]
        matches = await self._contacts_for_number(number)
        real = [c for c in matches if not self._crm.owns_contact(c)]
        if real:
            report["conflicts"].append(
                {
                    "persona": pid,
                    "contact_id": real[0]["id"],
                    "reason": "number held by a contact this seeder does not own; not seeded",
                }
            )
            return
        attrs = {**contact_spec.get("custom_attributes", {}), "demo_seed": DEMO_SEED_MARKER}
        if matches:
            contact = matches[0]
            await self._crm.update_contact_attributes(int(contact["id"]), attrs)
            report["updated_contacts"].append(pid)
        else:
            contact = await self._crm.create_contact(
                contact_spec["name"], number, attrs, inbox_id=self._inbox_id
            )
            report["created_contacts"].append(pid)
        contact_id = int(contact["id"])

        existing = await self._crm.contact_conversations(contact_id)
        if any(has_seeder_markers(c) for c in existing):
            report["skipped_history"].append(pid)
        else:
            for index, conv_spec in enumerate(spec.get("conversations") or [], start=1):
                await self._create_conversation(pid, index, contact_id, conv_spec)
                report["created_conversations"] += 1
        await self._seed_rsa(persona, report)

    async def _create_conversation(
        self, pid: str, index: int, contact_id: int, spec: dict[str, Any]
    ) -> int:
        assert_demo_text(spec["message"])
        conv = await self._crm.create_conversation(
            contact_id,
            inbox_id=self._inbox_id,
            source_id=f"{DEMO_SOURCE_PREFIX}{pid}-{index}-{uuid.uuid4().hex[:8]}",
            custom_attributes={**spec.get("custom_attributes", {}), "demo_seed": DEMO_SEED_MARKER},
            additional_attributes={"demo_persona": pid},
        )
        vid = int(conv["id"])
        await self._crm.add_labels(vid, [AUDIT_LABEL, DEMO_LABEL, *spec.get("labels", [])])
        # Off `pending` BEFORE the incoming message (see module docstring).
        await self._crm.set_status(vid, "open")
        await self._crm.post_incoming_message(vid, spec["message"])
        status = spec.get("status", "open")
        if status != "open":
            await self._crm.set_status(vid, status)
        return vid

    async def _seed_rsa(self, persona: dict[str, Any], report: dict[str, Any]) -> None:
        incidents = persona["crm_seed"].get("rsa_incidents") or []
        if not incidents:
            return
        if self._rsa is None:
            report["errors"].append(
                f"{persona['id']}: RSA store not configured (RSA_ENABLED / RSA_DATABASE_URL); "
                "RSA incident not seeded"
            )
            return
        number = persona["number"]
        if any(self._rsa_is_personas(r, number) for r in await self._rsa.list_incidents()):
            return
        for spec in incidents:
            assert_demo_text(spec.get("remarks"))
            await self._rsa.create_incident(
                **spec, customer_phone=number, created_by=RSA_CREATED_BY
            )
            report["rsa_created"] += 1

    # --- reset ---------------------------------------------------------------------

    def _rsa_is_personas(self, row: Any, number: str | None = None) -> bool:
        """Demo markers AND the persona's phone -- or, when the row has no
        phone (a tenant whose table lacks the column), a seeded plate."""
        if not is_demo_rsa(row):
            return False
        phone = getattr(row, "customer_phone", None)
        numbers = [number] if number else list(self._numbers)
        if phone:
            return any(_phones_match(n, phone) for n in numbers)
        plates = self._plates_by_number.get(number, set()) if number else self._plates
        return _plate_key(str(getattr(row, "vehicle_no", "") or "")) in plates

    async def _find_demo_records(
        self,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        # Persona numbers ONLY -- never a tenant-wide "[DEMO]" search, which
        # would also return seed_demo_data contacts and renamed handsets.
        contacts: dict[int, dict[str, Any]] = {}
        for number in self._numbers:
            for c in await self._contacts_for_number(number):
                if self._crm.owns_contact(c):
                    contacts[int(c["id"])] = c

        conversations: dict[int, dict[str, Any]] = {}
        for conv in await self._crm.conversations_with_label(DEMO_LABEL):
            if conv.get("id") is not None and has_seeder_markers(conv):
                conversations[int(conv["id"])] = conv
        for contact_id in contacts:
            # Includes the calls a rehearsal made on a persona on the demo
            # inbox; anything on another inbox is not ours. The client's
            # fresh re-read still decides.
            for conv in await self._crm.contact_conversations(contact_id):
                if conv.get("id") is not None and self._crm.owns_conversation(conv):
                    conversations[int(conv["id"])] = conv
        return contacts, conversations

    async def _remove_demo_rsa(self) -> tuple[int, list[str]]:
        if self._rsa is None:
            return 0, []
        removed, errors = 0, []
        for row in await self._rsa.list_incidents():
            if not self._rsa_is_personas(row):
                continue
            try:
                fresh = await self._rsa.get_incident(row.id)
                assert_demo_rsa(fresh, row.id)
                if await self._rsa.delete_incident(row.id):
                    removed += 1
            except DemoSafetyError as exc:
                errors.append(str(exc))
        return removed, errors

    async def _non_demo_holders(self) -> list[dict[str, Any]]:
        out = []
        for persona in self._personas:
            for c in await self._contacts_for_number(persona.get("number") or ""):
                if not self._crm.owns_contact(c):
                    out.append({"persona": persona["id"], "contact_id": c["id"]})
        return out

    async def reset(self) -> dict[str, Any]:
        await self._check_inbox()
        contacts, conversations = await self._find_demo_records()
        removed = {"contacts": 0, "conversations": 0, "rsa_incidents": 0}
        errors: list[str] = []
        for vid in sorted(conversations):
            try:
                await self._crm.set_status(vid, "resolved")
                await self._crm.delete_conversation(vid)
                removed["conversations"] += 1
            except (ChatwootError, DemoSafetyError) as exc:
                errors.append(f"conversation {vid}: {exc}")
        for cid in sorted(contacts):
            try:
                await self._crm.delete_contact(cid)
                removed["contacts"] += 1
            except (ChatwootError, DemoSafetyError) as exc:
                errors.append(f"contact {cid}: {exc}")
        removed["rsa_incidents"], rsa_errors = await self._remove_demo_rsa()
        errors.extend(rsa_errors)
        _log.info("demo_reset_removed", errors=len(errors), **removed)
        return {
            "removed": removed,
            "errors": errors,
            "not_demo_records": await self._non_demo_holders(),
            "seed": await self.seed(),
        }

    # --- status (read-only) ------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        inbox = await self._crm.get_inbox(self._inbox_id) if self._inbox_id else {}
        rsa_rows = await self._rsa.list_incidents() if self._rsa is not None else None
        personas = [await self._persona_status(p, rsa_rows) for p in self._personas]
        return {
            "target": self.target(),
            "inbox": {
                "channel_type": inbox.get("channel_type"),
                "ok": inbox.get("channel_type") == _API_CHANNEL,
            },
            "rsa_configured": self._rsa is not None,
            "personas": personas,
        }

    async def _persona_status(
        self, persona: dict[str, Any], rsa_rows: list[Any] | None
    ) -> dict[str, Any]:
        number = persona.get("number") or ""
        matches = await self._contacts_for_number(number)
        demo = [c for c in matches if self._crm.owns_contact(c)]
        others = [c for c in matches if not self._crm.owns_contact(c)]
        contact = demo[0] if demo else None
        convs = await self._crm.contact_conversations(int(contact["id"])) if contact else []
        latest = max(convs, key=_conv_sort_key) if convs else None
        warning = None
        if others:
            if persona.get("crm_seed"):
                warning = (
                    f"This number is held by contact #{others[0]['id']}, which this seeder "
                    "does not own: the persona is not seeded, and Seed/Reset never touch it."
                )
            else:
                warning = (
                    f"Contact #{others[0]['id']} holds this number (likely made by an "
                    "earlier rehearsal call). Reset does not touch it: delete it in the CRM to "
                    "rehearse a brand-new caller again."
                )
        rsa_count = None
        if rsa_rows is not None and number:
            rsa_count = sum(1 for r in rsa_rows if self._rsa_is_personas(r, number))
        return {
            "id": persona["id"],
            "label": persona.get("label", ""),
            "number": number,
            "language": persona.get("language", ""),
            "description": persona.get("description", ""),
            "scriptHint": persona.get("scriptHint", ""),
            "shows": list(persona.get("shows") or []),
            "seedable": bool(persona.get("crm_seed")),
            "seeded": contact is not None,
            "contact": (
                {
                    "id": contact["id"],
                    "name": contact.get("name"),
                    "url": self._link(f"/contacts/{contact['id']}"),
                }
                if contact
                else None
            ),
            "conversation_count": len(convs),
            "latest_conversation": (
                {
                    "id": latest["id"],
                    "status": latest.get("status"),
                    "url": self._link(f"/conversations/{latest['id']}"),
                }
                if latest
                else None
            ),
            "customer360_url": (
                self._link(f"/proton/customer360?q={quote(number, safe='')}") if number else None
            ),
            "rsa_incidents": rsa_count,
            "warning": warning,
        }


def build_seeder_factory(settings: Settings, rsa_repo: RsaRepositoryPort | None = None) -> Any:
    """An async context manager factory: a fresh ``DemoCrmClient`` per use,
    closed afterwards (main.py's /demo/* routes and the CLI both use it)."""

    @asynccontextmanager
    async def factory() -> Any:
        crm = DemoCrmClient(settings)
        try:
            yield DemoSeeder(settings, crm, rsa_repo=rsa_repo)
        finally:
            await crm.aclose()

    return factory
