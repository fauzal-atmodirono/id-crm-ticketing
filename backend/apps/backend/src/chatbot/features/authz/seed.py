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

from chatbot.features.authz.repository import AuthzRepository

PERMISSION_REGISTRY: dict[str, str] = {
    "knowledge.edit": "Edit Knowledge Base content",
    "kb.ingest": "Trigger KB document ingestion",
    "persona.edit": "Edit assistant persona/instructions",
    "sla.manage": "Manage SLA policies",
    "audit.view": "View the audit log",
    "roles.manage": "Manage roles and permission assignments",
}

_AGENT_PERMISSIONS = {"knowledge.edit"}


async def seed_defaults(repo: AuthzRepository) -> None:
    for key, description in PERMISSION_REGISTRY.items():
        await repo.create_permission(key, description)

    await repo.create_role("administrator", "Administrator", "Full access to all permissions")
    for key in PERMISSION_REGISTRY:
        await repo.grant_permission("administrator", key)

    await repo.create_role("agent", "Agent", "Minimal default access")
    for key in _AGENT_PERMISSIONS:
        await repo.grant_permission("agent", key)
