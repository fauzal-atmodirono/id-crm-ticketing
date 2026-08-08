"""GET /escalation/departments -- department keys that have a PIC configured.

The agent/ service's AI-suggested-department feature classifies a
conversation against this list rather than a static one, so it can never
suggest a department with no PIC configured (which would silently escalate
to nobody -- the exact failure that feature exists to prevent).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.features.chat.pic_store import PicRecord
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"proton_backend_key": "secret"}
    base.update(kw)
    return Settings(_env_file=None, **base)


class _PicStore:
    async def list_all(self) -> list[PicRecord]:
        return [
            PicRecord(
                department="engineer",
                pic_name="Aduy",
                pic_email="pic@test",
                pic_whatsapp="",
            ),
            PicRecord(
                department="Pre_Sales",  # mixed case -- must normalize
                pic_name="Budi",
                pic_email="budi@test",
                pic_whatsapp="",
            ),
            PicRecord(
                department="engineer",  # duplicate -- must dedupe
                pic_name="Second Engineer",
                pic_email="second@test",
                pic_whatsapp="",
            ),
        ]


class _FailingStore:
    async def list_all(self) -> list[Any]:
        raise RuntimeError("firestore is unreachable")


def _client(pic_store: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier=None,  # type: ignore[arg-type]
            chatwoot_request=None,  # type: ignore[arg-type]
            settings=_settings(),
            pic_store=pic_store,
            dealer_store=None,
        )
    )
    return TestClient(app)


def test_lists_deduped_lowercased_department_keys() -> None:
    res = _client(pic_store=_PicStore()).get(
        "/escalation/departments", headers={"x-api-key": "secret"}
    )
    assert res.status_code == 200
    assert res.json() == {"departments": ["engineer", "pre_sales"]}


def test_requires_api_key() -> None:
    assert (
        _client(pic_store=_PicStore()).get("/escalation/departments").status_code == 401
    )


def test_rejects_wrong_api_key() -> None:
    res = _client(pic_store=_PicStore()).get(
        "/escalation/departments", headers={"x-api-key": "wrong"}
    )
    assert res.status_code == 401


def test_missing_store_yields_empty_list_not_500() -> None:
    res = _client().get("/escalation/departments", headers={"x-api-key": "secret"})
    assert res.status_code == 200
    assert res.json() == {"departments": []}


def test_store_failure_degrades_to_empty_list_not_500() -> None:
    res = _client(pic_store=_FailingStore()).get(
        "/escalation/departments", headers={"x-api-key": "secret"}
    )
    assert res.status_code == 200
    assert res.json() == {"departments": []}
