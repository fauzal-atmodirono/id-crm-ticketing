"""Live smoke test for the Chatwoot+Zammad CRM integration (Proton demo).

Exercises the REAL ChatwootAdapter against whatever CHATWOOT_*/ZAMMAD_*
values are in .env, so it validates the exact code path the escalation flow
uses — not a reimplementation.

Run from apps/backend/:
    .venv/bin/python scripts/smoke_chatwoot_zammad.py

Read-only for Chatwoot (token check). Creates ONE throwaway Zammad ticket to
prove the escalation path; note the printed ticket id if you want to delete it.
"""

from __future__ import annotations

import asyncio

import httpx

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.platform.config import Settings


async def check_chatwoot(s: Settings) -> bool:
    url = f"{s.chatwoot_api_url.rstrip('/')}/api/v1/accounts/{s.chatwoot_account_id}/inboxes"
    headers = {"api_access_token": s.chatwoot_api_token}
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=headers, timeout=15.0)
    ok = r.status_code == 200
    print(f"[chatwoot] GET inboxes -> HTTP {r.status_code} {'OK' if ok else 'FAIL'}")
    if ok:
        inboxes = r.json().get("payload", r.json())
        names = [i.get("name") for i in inboxes] if isinstance(inboxes, list) else inboxes
        print(f"[chatwoot] inboxes: {names}")
    else:
        print(f"[chatwoot] body: {r.text[:200]}")
    return ok


async def check_zammad(s: Settings) -> bool:
    headers = {"Authorization": f"Token token={s.zammad_api_token}"}
    async with httpx.AsyncClient() as c:
        me = await c.get(
            f"{s.zammad_api_url.rstrip('/')}/api/v1/users/me", headers=headers, timeout=15.0
        )
    ok = me.status_code == 200
    print(f"[zammad] GET users/me -> HTTP {me.status_code} {'OK' if ok else 'FAIL'}")
    if ok:
        print(f"[zammad] authed as: {me.json().get('login')} ({me.json().get('email')})")
    return ok


async def create_ticket_via_adapter(s: Settings) -> None:
    adapter = ChatwootAdapter(s)
    ticket_id = await adapter.create_ticket(
        session_id="chatwoot-conv-SMOKE",
        title="SMOKE TEST — AI escalation path",
        body="Automated smoke test of the ChatwootAdapter. Safe to delete.",
        urgency="high",
    )
    print(f"[zammad] create_ticket via adapter -> ticket id: {ticket_id}")
    if ticket_id and ticket_id != "MOCK-ZAM-TKT":
        await adapter.add_private_note(ticket_id, "Smoke-test private note (internal).")
        print(f"[zammad] add_private_note OK — view: {s.zammad_api_url}/#ticket/zoom/{ticket_id}")
    else:
        print("[zammad] WARNING: ticket not created (adapter returned mock id).")


async def main() -> None:
    s = Settings()
    print(f"CRM_PROVIDER={s.crm_provider}")
    print(f"chatwoot_api_url={s.chatwoot_api_url}  account_id={s.chatwoot_account_id}")
    print(f"zammad_api_url={s.zammad_api_url}\n")

    cw_ok = await check_chatwoot(s)
    zm_ok = await check_zammad(s)
    print()
    if zm_ok:
        await create_ticket_via_adapter(s)
    print()
    print(
        f"SUMMARY: chatwoot={'OK' if cw_ok else 'FAIL (fix token)'}  zammad={'OK' if zm_ok else 'FAIL'}"
    )


if __name__ == "__main__":
    asyncio.run(main())
