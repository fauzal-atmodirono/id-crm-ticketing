"""P10 Task 3 -- Chatwoot custom-attribute definition sync.

Pushes active taxonomy nodes into Chatwoot's custom-attribute definitions
(case_category, case_subcategory, case_detail), removing the requirement for a
service restart when a category is added or updated.

The store is authoritative; the Chatwoot sync is downstream. A sync failure
leaves the store updated and surfaces an out_of_sync state for retry, rather
than rolling back an operator's edit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.features.taxonomy.store import TaxonomyStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Tracks in-memory out-of-sync status for retry/surfacing
_SYNC_STATE: dict[str, Any] = {"out_of_sync": False, "last_error": None}


def get_sync_state() -> dict[str, Any]:
    return dict(_SYNC_STATE)


def reset_sync_state() -> None:
    _SYNC_STATE["out_of_sync"] = False
    _SYNC_STATE["last_error"] = None


class ChatwootAttributeSyncError(Exception):
    pass


class ChatwootTaxonomySyncer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base_url(self) -> str:
        return f"{self._settings.chatwoot_api_url.rstrip('/')}/api/v1/accounts/{self._settings.chatwoot_account_id}"

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        import httpx  # deferred import

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base_url()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(
                    method, url, json=payload, headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("chatwoot_attribute_sync_failed", method=method, path=path, error=str(e))
            raise ChatwootAttributeSyncError(f"Chatwoot sync {method} {path} failed: {e}") from e

    async def sync_custom_attribute(
        self, attribute_key: str, option_values: list[str]
    ) -> bool:
        """Update a custom attribute's allowed option values list in Chatwoot."""
        path = f"/custom_attribute_definitions/{attribute_key}"
        payload = {"attribute_display_name": attribute_key, "attribute_values": option_values}
        try:
            await self._request("PATCH", path, payload)
            _SYNC_STATE["out_of_sync"] = False
            _SYNC_STATE["last_error"] = None
            return True
        except Exception as exc:
            _log.warning("chatwoot_custom_attribute_patch_failed", key=attribute_key, error=str(exc))
            # Surface out_of_sync state
            _SYNC_STATE["out_of_sync"] = True
            _SYNC_STATE["last_error"] = str(exc)
            return False


async def sync_taxonomy_to_chatwoot(store: TaxonomyStore, settings: Settings) -> bool:
    """Sync the store's active and historical taxonomy values into Chatwoot custom attributes."""
    syncer = ChatwootTaxonomySyncer(settings)

    # Fetch all nodes (including inactive for historical preservation)
    all_nodes = await store.list_nodes(active_only=False)

    l1_options = [n.label for n in all_nodes if n.level == 1]
    l2_options = [n.label for n in all_nodes if n.level == 2]

    # Format L3 options as "<Division Label>: <Subcategory Label>"
    nodes_by_key = {n.key: n for n in all_nodes}
    l3_options: list[str] = []
    for n in all_nodes:
        if n.level == 3 and n.parent in nodes_by_key:
            parent = nodes_by_key[n.parent]
            l3_options.append(f"{parent.label}: {n.label}")

    l4_options = [n.label for n in all_nodes if n.level == 4]

    s1 = await syncer.sync_custom_attribute("case_category", l1_options or l2_options)
    s2 = await syncer.sync_custom_attribute("case_subcategory", l3_options)
    s3 = await syncer.sync_custom_attribute("case_detail", l4_options)

    success = s1 and s2 and s3
    if success:
        _SYNC_STATE["out_of_sync"] = False
        _SYNC_STATE["last_error"] = None
    return success
