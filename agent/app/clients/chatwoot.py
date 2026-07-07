"""Chatwoot API clients.

`ChatwootClient` talks to the account-scoped Application API
(`/api/v1/accounts/{account_id}/...`), authenticated with an agent API
access token sent as the `api_access_token` header.

`ChatwootPlatformClient` talks to the super-admin-scoped Platform API
(`/platform/api/v1/...`), authenticated with the platform token, and is
used only for one-time setup (registering the agent bot).
"""

from typing import Any

import httpx


class ChatwootClient:
    def __init__(
        self,
        base_url: str,
        api_access_token: str,
        account_id: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.account_id = account_id
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"api_access_token": api_access_token},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _override_headers(token_override: str | None) -> dict[str, str] | None:
        if token_override is None:
            return None
        return {"api_access_token": token_override}

    async def get_messages(self, conversation_id: int) -> Any:
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages"
        )
        response.raise_for_status()
        return response.json()

    async def create_message(
        self,
        conversation_id: int,
        content: str,
        private: bool = True,
        token_override: str | None = None,
    ) -> Any:
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages",
            json={"content": content, "private": private},
            headers=self._override_headers(token_override),
        )
        response.raise_for_status()
        return response.json()

    async def toggle_status(self, conversation_id: int, status: str) -> Any:
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/toggle_status",
            json={"status": status},
        )
        response.raise_for_status()
        return response.json()

    async def get_contact(self, contact_id: int) -> Any:
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/contacts/{contact_id}"
        )
        response.raise_for_status()
        return response.json()

    async def get_agent_bot(self, agent_bot_id: int) -> Any:
        """Account-scoped agent bot lookup — used only by
        `scripts.register_bot` to read back the bot's `secret`, which the
        Platform API's create/show response doesn't include (see
        `crm/chatwoot/app/views/api/v1/models/_agent_bot.json.jbuilder`:
        `secret` is only serialized here, gated on the caller being an
        account administrator)."""
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/agent_bots/{agent_bot_id}"
        )
        response.raise_for_status()
        return response.json()

    async def set_agent_bot(self, inbox_id: int, agent_bot_id: int) -> Any:
        """Assign an agent bot to an inbox (`scripts.register_bot`'s last
        step) — see `crm/chatwoot/config/routes.rb:259`
        (`post :set_agent_bot, on: :member` under `resources :inboxes`)."""
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/inboxes/{inbox_id}/set_agent_bot",
            json={"agent_bot": agent_bot_id},
        )
        response.raise_for_status()
        return response.json() if response.content else None


class ChatwootPlatformClient:
    def __init__(
        self,
        base_url: str,
        platform_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"api_access_token": platform_token},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_agent_bot(self, name: str, outgoing_url: str) -> Any:
        response = await self._client.post(
            "/platform/api/v1/agent_bots",
            json={"name": name, "outgoing_url": outgoing_url},
        )
        response.raise_for_status()
        return response.json()
