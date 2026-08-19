"""Firestore-backed stores for PIC (department -> escalation contact) and
dealer (dealer slug -> email) routing config, editable via the Escalation
Routing admin page. Mirrors routing/store.py's ChannelPriorityStore pattern
exactly: one document per key, asyncio.to_thread for I/O, fail-open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_PIC_COLLECTION = "escalation_pics"
_DEALER_COLLECTION = "escalation_dealers"
_PRONET_COLLECTION = "escalation_pronet"

# The four dealer-side roles Proton's escalation matrix addresses, in the
# order the ladder climbs them: steps 1-2 reach the CRE and the Sales/
# Aftersales Manager, step 3 the Principal, step 4 the Owner. Kept as data
# here rather than as fields so escalation_policy.py can name a role in a
# step table and this module stays the only place that knows how a record is
# stored.
DEALER_ROLES = ("cre", "sales_aftersales_mgr", "principal", "owner")

# PRO-NET's own people, CC'd on every internal leg from step 1 onward.
PRONET_ROLES = ("area_regional_mgr", "hod")


@dataclass(frozen=True)
class PicRecord:
    department: str
    pic_name: str
    pic_email: str
    pic_whatsapp: str
    cc_emails: list[str] = field(default_factory=list)
    # P2 task 7: who tier-2 wakes up when the first alert went unanswered.
    # Empty (every record written before P2) falls back to the PIC themselves
    # -- better the same people twice than nobody.
    escalation_manager_email: str = ""
    escalation_manager_whatsapp: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PicRecord):
            return NotImplemented
        return (
            self.department == other.department
            and self.pic_name == other.pic_name
            and self.pic_email == other.pic_email
            and self.pic_whatsapp == other.pic_whatsapp
            and list(self.cc_emails) == list(other.cc_emails)
        )

    def __hash__(self) -> int:
        return hash(
            (self.department, self.pic_name, self.pic_email, self.pic_whatsapp, tuple(self.cc_emails))
        )


@dataclass(frozen=True)
class DealerRecord:
    dealer: str
    emails: list[str] = field(default_factory=list)
    # P2: "relevant personnel" kept in the loop on the dealer forward (e.g. a
    # service manager). Empty = To-the-group only, which is every record
    # written before P2. Gated by escalation_cc_dealer.
    cc_emails: list[str] = field(default_factory=list)
    # The escalation ladder's named roles (DEALER_ROLES -> email). Steps 3 and
    # 4 exist to reach a DIFFERENT person than step 1 did, which a flat group
    # cannot express. Partial by design: an incomplete contact matrix is the
    # expected state for months, and a step whose role is missing skips.
    contacts: dict[str, str] = field(default_factory=dict)
    # Which PRO-NET region CCs this dealer's escalations. Empty = no regional
    # CC, never a lookup failure.
    region: str = ""

    def contact(self, role: str) -> str:
        """The address for *role*, or "" when it is not configured.

        Falls back to the legacy flat group for ``cre`` only: a dealer record
        written before the ladder holds one address that was used exactly as
        the CRE is used today (first contact on escalation), so reading it as
        the CRE keeps every live tenant working unchanged. The senior roles
        deliberately do NOT fall back -- silently mailing a Dealer Owner an
        address that was only ever meant for the service desk is worse than
        skipping the step and logging it.
        """
        configured = (self.contacts or {}).get(role, "")
        if configured:
            return str(configured)
        if role == "cre" and self.emails:
            return str(self.emails[0])
        return ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DealerRecord):
            return NotImplemented
        return (
            self.dealer == other.dealer
            and list(self.emails) == list(other.emails)
            and list(self.cc_emails) == list(other.cc_emails)
            and dict(self.contacts) == dict(other.contacts)
            and self.region == other.region
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.dealer,
                tuple(self.emails),
                tuple(self.cc_emails),
                tuple(sorted((self.contacts or {}).items())),
                self.region,
            )
        )


@dataclass(frozen=True)
class ProtonNetRecord:
    """PRO-NET's own escalation contacts for one region.

    Separate from DealerRecord because these are Proton staff, not dealer
    staff: the same Area/Regional Manager and HOD are CC'd on escalations for
    every dealer in their region, so storing them per dealer would mean
    updating N records when one person changes jobs.
    """

    region: str
    area_regional_mgr: str = ""
    hod: str = ""

    def contact(self, role: str) -> str:
        return str(getattr(self, role, "") or "") if role in PRONET_ROLES else ""


def _dealer_record_from_dict(data: dict[str, Any], fallback_key: str) -> DealerRecord:
    """Build a DealerRecord from a Firestore document body.

    Accepts BOTH shapes so no migration is needed: the new `emails` list and
    the original single `email` string written before dealers became groups.
    """
    emails = data.get("emails")
    if isinstance(emails, list):
        members = [str(e) for e in emails if e]
    else:
        legacy = data.get("email")
        members = [str(legacy)] if legacy else []
    raw_cc = data.get("cc_emails")
    cc = [str(e) for e in raw_cc if e] if isinstance(raw_cc, list) else []
    raw_contacts = data.get("contacts")
    contacts = (
        {k: str(v) for k, v in raw_contacts.items() if k in DEALER_ROLES and v}
        if isinstance(raw_contacts, dict)
        else {}
    )
    return DealerRecord(
        dealer=str(data.get("dealer", fallback_key)),
        emails=members,
        cc_emails=cc,
        contacts=contacts,
        region=str(data.get("region", "") or ""),
    )


class PicStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, department: str) -> firestore.DocumentReference:
        return self._client().collection(_PIC_COLLECTION).document(department.lower())

    async def get(self, department: str) -> PicRecord | None:
        try:
            snap = await asyncio.to_thread(self._doc_ref(department).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return PicRecord(
                department=str(data.get("department", department)),
                pic_name=str(data.get("pic_name", "")),
                pic_email=str(data.get("pic_email", "")),
                pic_whatsapp=str(data.get("pic_whatsapp", "")),
                cc_emails=list(data.get("cc_emails") or []),
                escalation_manager_email=str(data.get("escalation_manager_email", "")),
                escalation_manager_whatsapp=str(data.get("escalation_manager_whatsapp", "")),
            )
        except Exception as e:
            _log.error("pic_store_get_failed", department=department, error=str(e))
            return None

    async def set(
        self,
        department: str,
        pic_name: str,
        pic_email: str,
        pic_whatsapp: str,
        cc_emails: list[str] | None = None,
        escalation_manager_email: str = "",
        escalation_manager_whatsapp: str = "",
    ) -> None:
        try:
            await asyncio.to_thread(
                self._doc_ref(department).set,
                {
                    "department": department,
                    "pic_name": pic_name,
                    "pic_email": pic_email,
                    "pic_whatsapp": pic_whatsapp,
                    "cc_emails": cc_emails or [],
                    "escalation_manager_email": escalation_manager_email,
                    "escalation_manager_whatsapp": escalation_manager_whatsapp,
                },
            )
        except Exception as e:
            _log.error("pic_store_set_failed", department=department, error=str(e))

    async def delete(self, department: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(department).delete)
        except Exception as e:
            _log.error("pic_store_delete_failed", department=department, error=str(e))

    async def list_all(self) -> list[PicRecord]:
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_PIC_COLLECTION).stream())
            )
            results: list[PicRecord] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                department = data.get("department")
                if department is None:
                    continue
                results.append(
                    PicRecord(
                        department=str(department),
                        pic_name=str(data.get("pic_name", "")),
                        pic_email=str(data.get("pic_email", "")),
                        pic_whatsapp=str(data.get("pic_whatsapp", "")),
                        cc_emails=list(data.get("cc_emails") or []),
                        escalation_manager_email=str(
                            data.get("escalation_manager_email", "")
                        ),
                        escalation_manager_whatsapp=str(
                            data.get("escalation_manager_whatsapp", "")
                        ),
                    )
                )
            return results
        except Exception as e:
            _log.error("pic_store_list_failed", error=str(e))
            return []


class DealerStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, dealer: str) -> firestore.DocumentReference:
        return self._client().collection(_DEALER_COLLECTION).document(dealer.lower())

    async def get(self, dealer: str) -> DealerRecord | None:
        try:
            snap = await asyncio.to_thread(self._doc_ref(dealer).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return _dealer_record_from_dict(data, dealer)
        except Exception as e:
            _log.error("dealer_store_get_failed", dealer=dealer, error=str(e))
            return None

    async def set(
        self,
        dealer: str,
        emails: list[str],
        cc_emails: list[str] | None = None,
        contacts: dict[str, str] | None = None,
        region: str = "",
    ) -> None:
        """Write a dealer record.

        ``contacts``/``region``/``cc_emails`` are optional so the pre-ladder
        two-argument call still writes exactly what it wrote before -- the
        Escalation Routing page's group editor keeps working while the role
        editor ships. Unknown role keys are dropped rather than stored: a
        typo'd role would otherwise sit in Firestore looking configured while
        no step ever reads it.
        """
        body: dict[str, Any] = {"dealer": dealer, "emails": list(emails)}
        if cc_emails is not None:
            body["cc_emails"] = list(cc_emails)
        if contacts is not None:
            body["contacts"] = {
                role: str(email)
                for role, email in contacts.items()
                if role in DEALER_ROLES and email
            }
        if region:
            body["region"] = region
        try:
            await asyncio.to_thread(self._doc_ref(dealer).set, body)
        except Exception as e:
            _log.error("dealer_store_set_failed", dealer=dealer, error=str(e))

    async def delete(self, dealer: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(dealer).delete)
        except Exception as e:
            _log.error("dealer_store_delete_failed", dealer=dealer, error=str(e))

    async def list_all(self) -> list[DealerRecord]:
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_DEALER_COLLECTION).stream())
            )
            results: list[DealerRecord] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                dealer = data.get("dealer")
                if dealer is None:
                    continue
                results.append(_dealer_record_from_dict(data, str(dealer)))
            return results
        except Exception as e:
            _log.error("dealer_store_list_failed", error=str(e))
            return []


class ProtonNetStore:
    """Region -> PRO-NET Area/Regional Manager + HOD.

    Same one-document-per-key, to_thread, fail-open shape as the two stores
    above. Its own collection rather than fields on DealerRecord because
    these people are CC'd on every dealer in their region: one job change
    should edit one document, not every dealer under them.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, region: str) -> firestore.DocumentReference:
        return self._client().collection(_PRONET_COLLECTION).document(region.lower())

    async def get(self, region: str) -> ProtonNetRecord | None:
        if not region:
            return None
        try:
            snap = await asyncio.to_thread(self._doc_ref(region).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return ProtonNetRecord(
                region=str(data.get("region", region)),
                area_regional_mgr=str(data.get("area_regional_mgr", "") or ""),
                hod=str(data.get("hod", "") or ""),
            )
        except Exception as e:
            _log.error("pronet_store_get_failed", region=region, error=str(e))
            return None

    async def set(self, region: str, area_regional_mgr: str = "", hod: str = "") -> None:
        try:
            await asyncio.to_thread(
                self._doc_ref(region).set,
                {
                    "region": region,
                    "area_regional_mgr": area_regional_mgr,
                    "hod": hod,
                },
            )
        except Exception as e:
            _log.error("pronet_store_set_failed", region=region, error=str(e))

    async def delete(self, region: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(region).delete)
        except Exception as e:
            _log.error("pronet_store_delete_failed", region=region, error=str(e))

    async def list_all(self) -> list[ProtonNetRecord]:
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_PRONET_COLLECTION).stream())
            )
            results: list[ProtonNetRecord] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                region = data.get("region")
                if region is None:
                    continue
                results.append(
                    ProtonNetRecord(
                        region=str(region),
                        area_regional_mgr=str(data.get("area_regional_mgr", "") or ""),
                        hod=str(data.get("hod", "") or ""),
                    )
                )
            return results
        except Exception as e:
            _log.error("pronet_store_list_failed", error=str(e))
            return []
