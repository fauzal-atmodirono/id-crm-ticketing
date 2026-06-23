"""Backfill a Sunshine Conversations user profile + metadata from captured lead data.

Re-syncs an existing session's `lead_details` (stored in the ADK Firestore session
state) to its Sunshine/Zendesk user record using the current adapter logic — so
phone and preferred_model land in top-level user `metadata` instead of being
dropped inside `profile`. Use after fixing a record whose handoff predated the fix.

This performs a LIVE write to the Sunshine/Zendesk integration. Run deliberately.

Usage:
    cd apps/backend
    .venv/bin/python scripts/backfill_sunshine_user.py sim-9714
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from google.cloud import firestore

from chatbot.features.chat.adapters.sunshine_conversations import SunshineConversationsAdapter
from chatbot.features.chat.models import HandoffOpenPayload
from chatbot.platform.config import get_settings


async def backfill(session_id: str) -> None:
    settings = get_settings()

    fc = firestore.Client(
        project=settings.firestore_project_id, database=settings.firestore_database_id
    )
    doc = fc.collection("adk_sessions").document(session_id).get()
    if not doc.exists:
        print(f"session {session_id}: NOT FOUND in adk_sessions")
        return
    state = (doc.to_dict() or {}).get("state", {})
    lead = state.get("lead_details") or {}
    print(f"lead_details: {json.dumps(lead, ensure_ascii=False)}")
    if not lead:
        print("no lead_details captured for this session — nothing to backfill")
        return

    payload = HandoffOpenPayload(
        session_id=session_id,
        customer_name=lead.get("customer_name") or f"Proton AI Customer ({session_id})",
        customer_email=lead.get("customer_email") or f"{session_id}@proton.devoteam.example",
        ai_summary="Backfill sync of captured lead details.",
        transcript=(),
        customer_phone=lead.get("customer_phone"),
        preferred_model=lead.get("preferred_model"),
    )

    adapter = SunshineConversationsAdapter(settings)
    async with httpx.AsyncClient(timeout=15.0) as client:
        await adapter._upsert_user(client, payload, session_id)
        print("upsert done — re-reading stored profile + metadata...")
        res = await client.get(
            f"{adapter.BASE}/apps/{adapter._app_id}/users/{session_id}",
            headers={"Authorization": adapter._auth_header, "Content-Type": "application/json"},
        )
    user = res.json().get("user") or {}
    print(f"profile : {json.dumps(user.get('profile'), ensure_ascii=False)}")
    print(f"metadata: {json.dumps(user.get('metadata'), ensure_ascii=False)}")


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "sim-9714"
    asyncio.run(backfill(sid))
