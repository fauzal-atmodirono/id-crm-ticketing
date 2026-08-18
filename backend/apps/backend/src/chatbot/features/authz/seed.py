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
    # Package C Task 5: recordings are customer voice data. Registered here,
    # same as escalation.manage/customer360.view above, so it is auto-granted
    # to "administrator" (full access) but NOT to the minimal "agent" role,
    # and is ready for whatever endpoint eventually exposes a call recording
    # to gate on via require_permission -- see features/chat/phone/bridge.py
    # / router.py for where recordings are captured. No such retrieval
    # endpoint exists in this codebase yet; this entry exists so retrieval
    # can never ship un-gated by omission.
    "call_recording.listen": "Listen to / retrieve a call recording",
    # Agent softphone (see docs/superpowers/specs/
    # 2026-08-18-agent-softphone-design.md): granted per-agent to whoever
    # should receive transferred calls in the browser. A Voice grant with
    # incoming_allow=True is a BILLABLE, call-receiving capability on the
    # tenant's Twilio account, not just a UI affordance, so it is deliberately
    # withheld from the default AGENT role. An operator grants it per-agent via
    # the Roles admin UI; it auto-grants to "administrator" like all other
    # PERMISSION_REGISTRY keys.
    "voice.answer": "Answer transferred phone calls in the browser softphone",
    # Package F: the DMS/TSP integration shell's admin CRUD + connection
    # test (features/chat/dms_admin_router.py). Same shape as
    # escalation.manage/sla.manage above -- auto-granted to "administrator",
    # withheld from "agent".
    "integration.manage": "Manage DMS/TSP integration settings",
    # P3: the case-record panel in the conversation sidebar. Unlike every
    # other entry here, these are granted to "agent" as well -- filling in the
    # WIP and vehicle fields IS the agent's job, and an admin-only panel would
    # simply never be used.
    "cases.view": "View the case record panel",
    "cases.manage": "Edit the case record panel",
    # P6: supervisor-facing reassignment (POST /routing/assign with an
    # explicit agent_id) and the workforce/presence dashboard (GET
    # /admin/workforce). Same shape as escalation.manage/customer360.view
    # above -- auto-granted to "administrator", withheld from "agent".
    "routing.reassign": "Reassign a conversation to a chosen agent",
    "workforce.view": "View the workforce/presence dashboard",
    # P6 C1 fix: the status-selection write path (features/routing/
    # status_router.py). Two keys, because the two actions are not the same
    # kind of act.
    #
    # presence.set_own_status is granted to "agent" below -- one of only four
    # keys that is. Choosing your own availability IS the agent's job, and
    # gating it behind an admin permission would leave the eight-status
    # catalogue selectable by nobody, which is the mistake ruling D5 had to
    # correct on the reassignment path.
    #
    # workforce.manage is the admin counterpart, withheld from "agent" like
    # escalation.manage/workforce.view: it covers editing the catalogue AND
    # setting a DIFFERENT agent's status, which removes that agent from
    # routing and starts an absence-alert clock against their name.
    "presence.set_own_status": "Set your own availability status",
    "workforce.manage": "Edit the status catalogue and set other agents' statuses",
    # P7 task 3: the agent-facing translate action (POST /assist/translate).
    # Granted to "agent" below, like presence.set_own_status and
    # cases.view/cases.manage above and unlike escalation.manage/
    # customer360.view -- reading a customer's own message in translation is
    # an agent's ordinary job on every conversation they handle, not an
    # administrative act, so gating it admin-only would make the feature
    # unusable by the people it exists for (the same mistake ruling D5 and
    # the presence.set_own_status decision both had to correct).
    "translation.use": "Translate a customer message for reading",
    # P9 task 1/6: the alert-rule store's two write paths
    # (features/alerts/rules_router.py). Same two-key split as
    # presence.set_own_status/workforce.manage above, for the same reason.
    #
    # alerts.set_own_preferences is granted to "agent" below -- choosing how
    # loudly YOUR OWN new-inbound/SLA/escalation alerts fire is the agent's
    # own call (tolerance for interruption genuinely varies by person), and
    # gating it admin-only would leave the per-agent override half of this
    # store unreachable by anyone, the same mistake ruling D5 and the
    # presence.set_own_status/translation.use decisions already corrected.
    #
    # alerts.manage is the admin counterpart: editing the ACCOUNT-level
    # defaults every agent inherits from. Withheld from "agent" like
    # workforce.manage -- one agent turning down the account-wide
    # sla_breach default would go unnoticed until the whole team missed one.
    "alerts.set_own_preferences": "Set your own alert-rule preferences",
    "alerts.manage": "Manage account-level alert-rule defaults",
    "taxonomy.manage": "Manage case taxonomy tree and category mappings",
}

_AGENT_PERMISSIONS = {
    "knowledge.edit",
    "cases.view",
    "cases.manage",
    "presence.set_own_status",
    "translation.use",
    "alerts.set_own_preferences",
}

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
