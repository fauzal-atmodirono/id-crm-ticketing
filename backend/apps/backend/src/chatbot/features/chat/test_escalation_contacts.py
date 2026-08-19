"""GET /escalation/contacts -- the escalation sender allowlist.

A later step in the reply loop links a dealer/PIC email reply onto the
original conversation using the correlation token in the subject/Reply-To.
Without an allowlist of who is actually entitled to reply, anyone who
guesses (or brute-forces) a conversation id could inject a private note
into a customer's case just by emailing the tenant's inbox. This endpoint
is that allowlist: every address the escalation mail could have reached,
so the agent/ service can check "is this sender someone we actually sent
an escalation to" before trusting the reply.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.features.chat.pic_store import DealerRecord, PicRecord
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"proton_backend_key": "secret"}
    base.update(kw)
    return Settings(_env_file=None, **base)


class _PicStore:
    async def list_all(self) -> list[PicRecord]:
        return [
            PicRecord(
                department="sales",
                pic_name="Aduy",
                pic_email="pic@test",
                pic_whatsapp="",
                cc_emails=["cc@test"],
            )
        ]


class _DealerStore:
    async def list_all(self) -> list[DealerRecord]:
        return [DealerRecord(dealer="komang", emails=["a@test", "b@test"])]


class _FailingStore:
    async def list_all(self) -> list[Any]:
        raise RuntimeError("firestore is unreachable")


def _client(pic_store: Any = None, dealer_store: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier=None,  # type: ignore[arg-type]
            chatwoot_request=None,  # type: ignore[arg-type]
            settings=_settings(),
            pic_store=pic_store,
            dealer_store=dealer_store,
        )
    )
    return TestClient(app)


def test_lists_pic_cc_and_dealer_addresses() -> None:
    res = _client(pic_store=_PicStore(), dealer_store=_DealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "secret"}
    )
    assert res.status_code == 200
    by_email = {c["email"]: c for c in res.json()["contacts"]}
    assert by_email["pic@test"]["kind"] == "pic"
    assert by_email["cc@test"]["kind"] == "pic"
    assert by_email["a@test"]["kind"] == "dealer"
    assert by_email["b@test"]["kind"] == "dealer"


def test_requires_api_key() -> None:
    assert (
        _client(pic_store=_PicStore(), dealer_store=_DealerStore())
        .get("/escalation/contacts")
        .status_code
        == 401
    )


def test_rejects_wrong_api_key() -> None:
    res = _client(pic_store=_PicStore(), dealer_store=_DealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "wrong"}
    )
    assert res.status_code == 401


def test_emails_are_lowercased_and_deduped() -> None:
    res = _client(pic_store=_PicStore(), dealer_store=_DealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "secret"}
    )
    emails = [c["email"] for c in res.json()["contacts"]]
    assert emails == [e.lower() for e in emails]
    assert len(emails) == len(set(emails))


def test_missing_stores_yield_empty_list_not_500() -> None:
    res = _client().get("/escalation/contacts", headers={"x-api-key": "secret"})
    assert res.status_code == 200
    assert res.json() == {"contacts": []}


def test_store_failure_degrades_to_partial_list_not_500() -> None:
    res = _client(pic_store=_FailingStore(), dealer_store=_DealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "secret"}
    )
    assert res.status_code == 200
    by_email = {c["email"]: c for c in res.json()["contacts"]}
    assert "a@test" in by_email
    assert "b@test" in by_email


class _LadderDealerStore:
    async def list_all(self) -> list[DealerRecord]:
        return [
            DealerRecord(
                dealer="petaling_jaya",
                emails=["desk@pj.my"],
                cc_emails=["ops@pj.my"],
                contacts={
                    "cre": "cre@pj.my",
                    "sales_aftersales_mgr": "sam@pj.my",
                    "principal": "dp@pj.my",
                    "owner": "owner@pj.my",
                },
                region="central",
            )
        ]


def test_the_ladder_roles_are_on_the_allowlist() -> None:
    """Steps 3 and 4 mail a Dealer Principal and a Dealer Owner who may appear
    NOWHERE in the flat group. If their addresses are missing here, their
    replies are refused as unknown senders -- so the ladder never learns the
    dealer engaged and keeps climbing to the next person, which is precisely
    the duplicate-escalation failure it exists to avoid."""
    res = _client(dealer_store=_LadderDealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "secret"}
    )

    by_email = {c["email"]: c for c in res.json()["contacts"]}
    for address in ("cre@pj.my", "sam@pj.my", "dp@pj.my", "owner@pj.my"):
        assert address in by_email, f"{address} can escalate but cannot reply"
        assert by_email[address]["kind"] == "dealer"


def test_the_dealer_cc_list_is_on_the_allowlist_too() -> None:
    """A CC'd manager who hits reply-all is answering the escalation."""
    res = _client(dealer_store=_LadderDealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "secret"}
    )

    assert "ops@pj.my" in {c["email"] for c in res.json()["contacts"]}


def test_the_role_name_travels_with_the_address() -> None:
    """The agent writes this name onto the private note, so 'Reply from
    Dealer Principal' beats 'Reply from petaling_jaya'."""
    res = _client(dealer_store=_LadderDealerStore()).get(
        "/escalation/contacts", headers={"x-api-key": "secret"}
    )

    by_email = {c["email"]: c for c in res.json()["contacts"]}
    assert "principal" in by_email["dp@pj.my"]["name"].lower()
