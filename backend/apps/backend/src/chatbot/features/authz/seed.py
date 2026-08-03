"""Idempotent default role/permission seeding.

Runs at backend startup (see main.py wiring in Task 5) so a fresh RBAC
database always has 'administrator' (all permissions) and 'agent' (minimal)
roles — an unconfigured/freshly-migrated tenant behaves as if RBAC always
existed: any user assigned 'administrator' has full access, matching today's
"any Chatwoot admin can do anything" behavior once someone is assigned that
role (assignment itself is a separate step — see the /authz roles-admin
endpoint, Task 5).
"""

from __future__ import annotations

from chatbot.features.authz.db import (
    ALL_NATIVE_KEYS,
    NATIVE_BOOLEAN_KEYS,
    NATIVE_CONVERSATION_KEYS,
)
from chatbot.features.authz.repository import AuthzRepository

__all__ = [
    "ALL_NATIVE_KEYS",
    "NATIVE_BOOLEAN_KEYS",
    "NATIVE_CONVERSATION_KEYS",
    "NATIVE_PERMISSION_REGISTRY",
    "PERMISSION_REGISTRY",
    "seed_defaults",
]

PERMISSION_REGISTRY: dict[str, str] = {
    "knowledge.edit": "Edit Knowledge Base content",
    "kb.ingest": "Trigger KB document ingestion",
    "persona.edit": "Edit assistant persona/instructions",
    "sla.manage": "Manage SLA policies",
    "audit.view": "View the audit log",
    "roles.manage": "Manage roles and permission assignments",
    "escalation.manage": "Manage PIC/dealer escalation routing",
    "customer360.view": "View the Customer 360 lookup",
}

_AGENT_PERMISSIONS = {"knowledge.edit"}

# Native Chatwoot conversation/inbox visibility, mirrored into Chatwoot's own
# CustomRole via features/authz/chatwoot_role_mirror.py (Phase 3). Registered
# so they're visible in the permission registry and grantable from the Roles
# & Permissions page, but deliberately NOT auto-granted to any default role —
# a tenant that never explicitly grants one of these stays byte-identical to
# pre-Phase-3 behavior (no CustomRole ever created, no user's custom_role_id
# ever touched).
#
# NATIVE_CONVERSATION_KEYS / NATIVE_BOOLEAN_KEYS / ALL_NATIVE_KEYS live in
# db.py (imported above and re-exported here for backward compatibility) to
# avoid a circular import: this module imports AuthzRepository from
# repository.py, so repository.py cannot import these constants from here.
NATIVE_PERMISSION_REGISTRY: dict[str, str] = {
    "chatwoot.conversation_manage": "Chatwoot: see and reply to all conversations",
    "chatwoot.conversation_unassigned_manage": "Chatwoot: see unassigned conversations + own",
    "chatwoot.conversation_participating_manage": "Chatwoot: see only own/participating conversations",
    "chatwoot.contact_manage": "Chatwoot: manage contacts",
    "chatwoot.report_manage": "Chatwoot: manage reports",
    "chatwoot.knowledge_base_manage": "Chatwoot: manage knowledge base portals",
}


async def seed_defaults(repo: AuthzRepository) -> None:
    for key, description in PERMISSION_REGISTRY.items():
        await repo.create_permission(key, description)

    await repo.create_role("administrator", "Administrator", "Full access to all permissions")
    for key in PERMISSION_REGISTRY:
        await repo.grant_permission("administrator", key)

    await repo.create_role("agent", "Agent", "Minimal default access")
    for key in _AGENT_PERMISSIONS:
        await repo.grant_permission("agent", key)

    # Register-only — see NATIVE_PERMISSION_REGISTRY's docstring above.
    for key, description in NATIVE_PERMISSION_REGISTRY.items():
        await repo.create_permission(key, description)
