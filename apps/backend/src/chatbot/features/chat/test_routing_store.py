from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _store() -> ChannelPriorityStore:
    return ChannelPriorityStore(
        Settings(
            firestore_project_id="proj",
            firestore_database_id="db",
        )
    )


def _make_snap(exists: bool, data: dict[str, Any] | None = None) -> MagicMock:
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    return snap


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found() -> None:
    snap = _make_snap(exists=False)
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _store().get(42)
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_priority_when_found() -> None:
    snap = _make_snap(
        exists=True, data={"agent_id": 7, "channel_priorities": ["WhatsApp", "email"]}
    )
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _store().get(7)
    assert result == AgentPriority(agent_id=7, channel_priorities=["WhatsApp", "email"])


@pytest.mark.asyncio
async def test_set_writes_document() -> None:
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _store().set(5, ["web", "email"])
    doc.set.assert_called_once_with({"agent_id": 5, "channel_priorities": ["web", "email"]})


@pytest.mark.asyncio
async def test_delete_removes_document() -> None:
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _store().delete(3)
    doc.delete.assert_called_once()


@pytest.mark.asyncio
async def test_list_all_returns_all_priorities() -> None:
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        snap_a = MagicMock()
        snap_a.to_dict.return_value = {"agent_id": 1, "channel_priorities": ["WhatsApp"]}
        snap_b = MagicMock()
        snap_b.to_dict.return_value = {"agent_id": 2, "channel_priorities": ["email", "web"]}
        col = MagicMock()
        col.stream.return_value = [snap_a, snap_b]
        MockClient.return_value.collection.return_value = col
        results = await _store().list_all()
    assert len(results) == 2
    assert AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]) in results
    assert AgentPriority(agent_id=2, channel_priorities=["email", "web"]) in results
