"""Tests for `sync.upsert_contact`: Chatwoot contact_created/contact_updated
events map to a Zammad user via search-then-create-or-update, plus an
optional organization lookup, upserted into `contact_links`.
"""

import httpx
import respx
from sqlalchemy import select

from app.db.models import ContactLink
from app.db.session import async_session_maker
from app.services import sync

ZAMMAD = "http://zammad-nginx:8080"


async def _link_for(contact_id: int) -> ContactLink | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(ContactLink).where(ContactLink.chatwoot_contact_id == contact_id)
        )
        return result.scalar_one_or_none()


@respx.mock
async def test_upsert_contact_creates_when_absent():
    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    create_route = respx.post(f"{ZAMMAD}/api/v1/users").mock(
        return_value=httpx.Response(201, json={"id": 501, "email": "new@example.com"})
    )

    payload = {
        "event": "contact_created",
        "id": 1001,
        "name": "New Person",
        "email": "new@example.com",
        "additional_attributes": {},
    }
    await sync.upsert_contact(payload)

    assert create_route.call_count == 1
    link = await _link_for(1001)
    assert link is not None
    assert link.zammad_user_id == 501
    assert link.email == "new@example.com"


@respx.mock
async def test_upsert_contact_updates_when_found():
    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(
        return_value=httpx.Response(
            200, json=[{"id": 77, "email": "existing@example.com"}]
        )
    )
    update_route = respx.put(f"{ZAMMAD}/api/v1/users/77").mock(
        return_value=httpx.Response(200, json={"id": 77, "email": "existing@example.com"})
    )
    create_route = respx.post(f"{ZAMMAD}/api/v1/users")

    payload = {
        "event": "contact_updated",
        "id": 1002,
        "name": "Existing Person",
        "email": "existing@example.com",
        "additional_attributes": {},
    }
    await sync.upsert_contact(payload)

    assert update_route.call_count == 1
    assert not create_route.called
    link = await _link_for(1002)
    assert link is not None
    assert link.zammad_user_id == 77


@respx.mock
async def test_upsert_contact_skips_when_no_email():
    search_route = respx.get(f"{ZAMMAD}/api/v1/users/search")
    create_route = respx.post(f"{ZAMMAD}/api/v1/users")

    payload = {"event": "contact_created", "id": 1003, "name": "No Email", "email": None}
    await sync.upsert_contact(payload)

    assert not search_route.called
    assert not create_route.called
    assert await _link_for(1003) is None


@respx.mock
async def test_upsert_contact_resolves_organization_from_company_name():
    org_search = respx.get(f"{ZAMMAD}/api/v1/organizations/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    org_create = respx.post(f"{ZAMMAD}/api/v1/organizations").mock(
        return_value=httpx.Response(201, json={"id": 9, "name": "Acme Inc"})
    )
    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(return_value=httpx.Response(200, json=[]))
    create_route = respx.post(f"{ZAMMAD}/api/v1/users").mock(
        return_value=httpx.Response(201, json={"id": 502})
    )

    payload = {
        "event": "contact_created",
        "id": 1004,
        "name": "Company Person",
        "email": "cp@example.com",
        "additional_attributes": {"company_name": "Acme Inc"},
    }
    await sync.upsert_contact(payload)

    assert org_search.called
    assert org_create.called
    sent_body = create_route.calls.last.request.content
    assert b"organization_id" in sent_body
