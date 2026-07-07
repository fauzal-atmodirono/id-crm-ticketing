"""Zammad REST API client, authenticated as a token-holding integration user
(`Authorization: Token token=<...>`).
"""

from typing import Any

import httpx


class ZammadClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Token token={api_token}"},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_users(self, query: str) -> Any:
        response = await self._client.get(
            "/api/v1/users/search", params={"query": query}
        )
        response.raise_for_status()
        return response.json()

    async def create_user(self, **fields: Any) -> Any:
        response = await self._client.post("/api/v1/users", json=fields)
        response.raise_for_status()
        return response.json()

    async def update_user(self, user_id: int, **fields: Any) -> Any:
        response = await self._client.put(f"/api/v1/users/{user_id}", json=fields)
        response.raise_for_status()
        return response.json()

    async def create_ticket(
        self,
        title: str,
        group: str,
        customer_id: int,
        article: dict[str, Any],
        tags: list[str] | None = None,
        priority: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "title": title,
            "group": group,
            "customer_id": customer_id,
            "article": article,
        }
        if tags:
            # Zammad's tickets_controller does `params[:tags].split(',')` --
            # it expects a comma-separated string, not a JSON array. Sending
            # an array 500s every ticket create.
            payload["tags"] = ",".join(tags)
        if priority:
            # Zammad accepts ticket priority by name (e.g. "2 normal").
            payload["priority"] = priority
        response = await self._client.post("/api/v1/tickets", json=payload)
        response.raise_for_status()
        return response.json()

    async def add_article(
        self, ticket_id: int, body: str, internal: bool = True
    ) -> Any:
        response = await self._client.post(
            "/api/v1/ticket_articles",
            json={"ticket_id": ticket_id, "body": body, "internal": internal},
        )
        response.raise_for_status()
        return response.json()

    async def get_articles(self, ticket_id: int) -> Any:
        response = await self._client.get(
            f"/api/v1/ticket_articles/by_ticket/{ticket_id}"
        )
        response.raise_for_status()
        return response.json()

    async def find_or_create_organization(self, name: str) -> Any:
        search_response = await self._client.get(
            "/api/v1/organizations/search", params={"query": name}
        )
        search_response.raise_for_status()
        for org in search_response.json():
            if org.get("name") == name:
                return org

        create_response = await self._client.post(
            "/api/v1/organizations", json={"name": name}
        )
        create_response.raise_for_status()
        return create_response.json()
