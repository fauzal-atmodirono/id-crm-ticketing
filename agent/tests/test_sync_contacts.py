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
async def test_upsert_contact_survives_contact_link_unique_collision():
    """A `contact_links` insert can hit a unique-constraint violation at
    commit time — e.g. two Chatwoot contacts sharing an email resolve to the
    same Zammad user (unique zammad_user_id). Running as a background task,
    upsert_contact must swallow that IntegrityError and not raise."""
    # Existing link already claims Zammad user 77.
    async with async_session_maker() as session:
        session.add(
            ContactLink(
                chatwoot_contact_id=2000,
                zammad_user_id=77,
                email="shared@example.com",
            )
        )
        await session.commit()

    # A different Chatwoot contact resolves (via email search) to the same
    # Zammad user 77 -> insert of (2001, 77) violates unique zammad_user_id.
    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(
        return_value=httpx.Response(
            200, json=[{"id": 77, "email": "shared@example.com"}]
        )
    )
    respx.put(f"{ZAMMAD}/api/v1/users/77").mock(
        return_value=httpx.Response(200, json={"id": 77})
    )

    payload = {
        "event": "contact_updated",
        "id": 2001,
        "name": "Second Persona",
        "email": "shared@example.com",
        "additional_attributes": {},
    }
    await sync.upsert_contact(payload)  # must not raise

    # The original link row is untouched.
    link = await _link_for(2000)
    assert link is not None
    assert link.zammad_user_id == 77


@respx.mock
async def test_upsert_contact_recovers_when_link_appears_between_precheck_and_commit(
    monkeypatch,
):
    """Simulates the concurrent-duplicate-delivery race: the pre-check sees
    no link, but by commit time another task has inserted a row whose unique
    key collides with ours. The IntegrityError recovery path must re-fetch
    and continue instead of raising out of the background task."""

    async def find_none_and_inject(chatwoot_contact_id):
        # Pre-check reports "no link", then the "other task" wins the race.
        # A row for the same chatwoot_contact_id would be seen by the final
        # re-select (taking the update path), so to land exactly in the
        # insert-then-IntegrityError window we plant a row the final select
        # (keyed on chatwoot_contact_id) does NOT find but whose unique
        # zammad_user_id our insert will collide with.
        async with async_session_maker() as session:
            session.add(
                ContactLink(
                    chatwoot_contact_id=3999,
                    zammad_user_id=501,
                    email="race@example.com",
                )
            )
            await session.commit()
        return None

    monkeypatch.setattr(sync, "_find_contact_link", find_none_and_inject)

    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"{ZAMMAD}/api/v1/users").mock(
        return_value=httpx.Response(201, json={"id": 501})
    )

    payload = {
        "event": "contact_created",
        "id": 3001,
        "name": "Race Loser",
        "email": "race@example.com",
        "additional_attributes": {},
    }
    await sync.upsert_contact(payload)  # must not raise


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
