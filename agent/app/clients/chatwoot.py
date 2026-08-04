"""Chatwoot API clients.

`ChatwootClient` talks to the account-scoped Application API
(`/api/v1/accounts/{account_id}/...`), authenticated with an agent API
access token sent as the `api_access_token` header.

`ChatwootPlatformClient` talks to the super-admin-scoped Platform API
(`/platform/api/v1/...`), authenticated with the platform token, and is
used only for one-time setup (registering the agent bot).
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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

    async def get_conversation(self, conversation_id: int) -> Any:
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}"
        )
        response.raise_for_status()
        return response.json()

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
        content_attributes: dict | None = None,
    ) -> Any:
        body: dict[str, Any] = {"content": content, "private": private}
        if content_attributes:
            body["content_attributes"] = content_attributes
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages",
            json=body,
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

    async def list_conversations(
        self, status: str | None = None, assignee_type: str | None = None
    ) -> Any:
        """List account conversations. `status` is one of
        open/pending/resolved/snoozed; `assignee_type` one of me/unassigned/all.
        Returns the raw JSON — the payload list is at `data.payload`."""
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status
        if assignee_type is not None:
            params["assignee_type"] = assignee_type
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/conversations", params=params
        )
        response.raise_for_status()
        return response.json()

    async def get_inbox(self, inbox_id: int) -> Any:
        """Fetch one inbox, including native `working_hours`, `timezone`,
        `working_hours_enabled`, and `channel_type`."""
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/inboxes/{inbox_id}"
        )
        response.raise_for_status()
        return response.json()

    async def set_custom_attributes(
        self, conversation_id: int, attributes: dict
    ) -> Any:
        """Merge-set conversation custom attributes without clobbering
        existing ones (used to mirror lifecycle_state into the Chatwoot
        right-panel for agent visibility, to write case_category /
        case_subcategory / case_type / vehicle_model, and to stamp
        dealer_escalated_at).

        Chatwoot's custom-attributes endpoint REPLACES the whole object —
        `ConversationsController#custom_attributes` does
        `@conversation.custom_attributes = params.permit(custom_attributes:
        {})[:custom_attributes]`, an assignment, not a merge. So we GET the
        conversation and POST the union, exactly as `add_labels` below does
        for the same reason on the labels endpoint. Without this, any caller
        writing one key silently erases every other key on the conversation:
        `lifecycle._mirror_state` writing `lifecycle_state` would drop a real
        customer's `case_category`/`vehicle_model`, and
        `sync.maybe_stamp_dealer_escalation` writing `dealer_escalated_at`
        would drop everything else on the row.

        `attributes` wins on conflict — the caller is asking for these values
        now, and every caller in this codebase already checks the existing
        value first when it wants to preserve one (see
        `categorize.maybe_categorize` and `maybe_stamp_dealer_escalation`).

        If the GET fails, we do NOT fall back to `add_labels`' fail-open
        posture of posting just `attributes`. Unlike the labels endpoint,
        this one assigns the whole object of record, so posting a partial
        `attributes` dict on a failed read would silently wipe every other
        key — the exact clobber this merge was written to prevent, just
        narrowed to the read-failure window. Losing this one write is
        recoverable (a later call re-establishes it); clearing a real
        conversation's `demo_seed`/`case_category`/`vehicle_model` is not.
        So: log and return without writing. The caller (a background task)
        must still never see an exception out of this.
        """
        try:
            resp = await self._client.get(
                f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}"
            )
            resp.raise_for_status()
            data = resp.json()
            existing = data.get("custom_attributes") if isinstance(data, dict) else None
            current = existing if isinstance(existing, dict) else {}
        except Exception:
            logger.warning(
                "set_custom_attributes: GET failed for conversation %s, skipping write "
                "to avoid clobbering existing custom_attributes (endpoint replaces, "
                "not merges)",
                conversation_id,
            )
            return None
        merged = {**current, **attributes}
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/custom_attributes",
            json={"custom_attributes": merged},
        )
        response.raise_for_status()
        return response.json() if response.content else None

    async def add_labels(self, conversation_id: int, labels: list[str]) -> Any:
        """Add labels without clobbering existing ones. Chatwoot's labels
        endpoint REPLACES the whole set, so we GET the current labels and POST
        the union. If the GET fails we fall back to posting just `labels`
        (adding at least the new ones rather than nothing)."""
        current: list[str] = []
        try:
            resp = await self._client.get(
                f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/labels"
            )
            resp.raise_for_status()
            data = resp.json()
            payload = data.get("payload") if isinstance(data, dict) else None
            if isinstance(payload, list):
                current = [str(x) for x in payload]
        except Exception:
            current = []
        union = list(dict.fromkeys([*current, *labels]))  # preserve order, dedup
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/labels",
            json={"labels": union},
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
