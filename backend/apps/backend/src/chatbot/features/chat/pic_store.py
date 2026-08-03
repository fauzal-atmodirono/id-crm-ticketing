"""Firestore-backed stores for PIC (department -> escalation contact) and
dealer (dealer slug -> email) routing config, editable via the Escalation
Routing admin page. Mirrors routing/store.py's ChannelPriorityStore pattern
exactly: one document per key, asyncio.to_thread for I/O, fail-open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_PIC_COLLECTION = "escalation_pics"
_DEALER_COLLECTION = "escalation_dealers"


@dataclass(frozen=True)
class PicRecord:
    department: str
    pic_name: str
    pic_email: str
    pic_whatsapp: str
    cc_emails: list[str] = field(default_factory=list)

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
    email: str


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
            return DealerRecord(dealer=str(data.get("dealer", dealer)), email=str(data.get("email", "")))
        except Exception as e:
            _log.error("dealer_store_get_failed", dealer=dealer, error=str(e))
            return None

    async def set(self, dealer: str, email: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(dealer).set, {"dealer": dealer, "email": email})
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
                results.append(DealerRecord(dealer=str(dealer), email=str(data.get("email", ""))))
            return results
        except Exception as e:
            _log.error("dealer_store_list_failed", error=str(e))
            return []
