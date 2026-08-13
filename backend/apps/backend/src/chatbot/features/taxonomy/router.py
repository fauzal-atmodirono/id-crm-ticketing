"""P10 Task 4 & Task 5 -- Admin taxonomy router & coverage report.

Mounts /admin/taxonomy endpoints for managing taxonomy tree nodes, category-department
mappings, and viewing taxonomy coverage reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
import structlog

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.pic_store import PicStore
from chatbot.features.taxonomy.chatwoot_sync import sync_taxonomy_to_chatwoot
from chatbot.features.taxonomy.store import TaxonomyNode, TaxonomyStore, build_taxonomy_store

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


_SYNC_SKIPPED_DETAIL = (
    "Chatwoot custom-attribute sync is disabled; the store was updated but the "
    "Chatwoot pickers were not. Update them with "
    "chatwoot-config/provision_case_taxonomy.py."
)


async def _maybe_sync_to_chatwoot(store: TaxonomyStore, settings: Settings, reason: str) -> None:
    """Push the store into Chatwoot's pickers -- only when explicitly enabled.

    TAXONOMY_CHATWOOT_SYNC_ENABLED is default-off because `chatwoot_sync.py`
    derives `case_category` from level-1 nodes (the case types plus the neutral
    divisions root) and `case_detail` from bare level-4 labels, while the live
    definitions provisioned by `chatwoot-config/provision_case_taxonomy.py`
    hold the 8 division labels and full "Division: Subcategory: Detail"
    strings. Firing it would overwrite both and break fork patch 0050's
    conversation-sidebar cascade for every agent. The sync was harmless only
    while the store was empty; startup seeding removed that accident.

    The skip is logged rather than silent: an operator who saves a category and
    then finds the agent picker unchanged needs to see WHY in the backend log
    instead of guessing at a broken sync.
    """
    if not settings.taxonomy_chatwoot_sync_enabled:
        _log.info(
            "taxonomy_chatwoot_sync_skipped",
            reason=reason,
            setting="TAXONOMY_CHATWOOT_SYNC_ENABLED",
            detail=_SYNC_SKIPPED_DETAIL,
        )
        return
    await sync_taxonomy_to_chatwoot(store, settings)


async def _unreferenced_departments(
    pic_store: PicStore, mapped_departments: set[str]
) -> tuple[list[str], str]:
    """Departments with a configured PIC that no active category maps to.

    `PicStore.list_all()` (features/chat/pic_store.py:160-190) never actually
    raises -- it catches every exception internally, logs
    `pic_store_list_failed` at error level itself, and returns `[]`. The
    try/except below is belt-and-braces only, in case that contract ever
    changes; it is **not** the path a real Firestore failure takes today, so
    its presence here is not evidence this endpoint observes PicStore
    failures -- it doesn't, and can't tell "no PICs configured" apart from
    "read failed and was swallowed upstream" (see the returned source below).

    Returns `(sorted_department_slugs, source)` where `source` is:
    - `"pic_store"` -- at least one PIC record came back, which is
      unambiguous proof the read actually happened.
    - `"unknown"` -- the read returned nothing at all. That is genuinely
      ambiguous (no PICs are configured vs. the read silently failed
      upstream), so this never claims the stronger `"pic_store"` answer.
    """
    try:
        pics = await pic_store.list_all()
    except Exception as exc:  # pragma: no cover -- list_all() swallows internally today
        _log.error("taxonomy_coverage_pic_store_failed", error=str(exc))
        return [], "unknown"

    known_departments = {
        f"dept_{key}" for rec in pics if (key := (rec.department or "").strip().lower())
    }
    unreferenced = sorted(known_departments - mapped_departments)
    source = "pic_store" if pics else "unknown"
    return unreferenced, source


def build_taxonomy_admin_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/admin/taxonomy", tags=["taxonomy-admin"])
    store = build_taxonomy_store(settings)
    pic_store = PicStore(settings)

    def _check_enabled() -> None:
        if not settings.taxonomy_admin_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Taxonomy admin feature is disabled on this tenant (TAXONOMY_ADMIN_ENABLED=false)",
            )

    @router.get("/tree")
    async def get_taxonomy_tree() -> dict[str, Any]:
        _check_enabled()
        tree_data = await store.tree()
        return {"tree": tree_data}

    @router.post(
        "/node",
        dependencies=[Depends(require_permission("taxonomy.manage", settings=settings))],
    )
    async def create_or_update_node(payload: dict[str, Any]) -> dict[str, Any]:
        _check_enabled()
        try:
            node = TaxonomyNode(
                level=int(payload["level"]),
                key=str(payload["key"]),
                label=str(payload["label"]),
                parent=payload.get("parent"),
                active=bool(payload.get("active", True)),
                department=payload.get("department"),
                sort_order=int(payload.get("sort_order", 0)),
            )
            success = await store.create_node(node)
            if success:
                # Trigger downstream sync (gated -- see _maybe_sync_to_chatwoot)
                await _maybe_sync_to_chatwoot(store, settings, "node_saved")
                return {"status": "ok", "node": asdict(node)}
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save node to store",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @router.post(
        "/node/{key}/retire",
        dependencies=[Depends(require_permission("taxonomy.manage", settings=settings))],
    )
    async def retire_taxonomy_node(key: str) -> dict[str, Any]:
        _check_enabled()
        try:
            active_children = await store.retire_node(key)
            await _maybe_sync_to_chatwoot(store, settings, "node_retired")
            return {
                "status": "retired",
                "key": key,
                "active_children": [asdict(child) for child in active_children],
            }
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    _COVERAGE_DISABLED = (
        "Category-to-department coverage report is disabled on this tenant "
        "(CATEGORY_DEPARTMENT_MAPPING_ENABLED=false)"
    )

    @router.get("/coverage")
    async def get_taxonomy_coverage() -> dict[str, Any]:
        """Coverage report: active categories with no department, and departments not mapped.

        `retired_department_categories` is always `[]`: flagging a category whose
        mapped department has been retired needs a retired/active distinction --
        active vs. retired PICs -- that `PicStore.list_all()` does not expose (it
        returns every PIC record, with no notion of retirement at all). The key
        is present so the shape is stable, and it is empty because nothing
        measured it -- not because nothing was found.

        `departments_source` is `"unknown"` rather than `"pic_store"` whenever
        the PIC read comes back empty: an empty result is ambiguous -- it means
        either "no PICs are configured" or "the read failed and was swallowed
        upstream" (see `_unreferenced_departments`) -- and nothing distinguishes
        the two, so this never claims the stronger answer it can't back up.
        """
        # Two gates, both required. The taxonomy admin has to be on for the store
        # to be the source of anything, and CATEGORY_DEPARTMENT_MAPPING_ENABLED is
        # what example.env documents as mounting this report. Before this second
        # check that flag had no consumer anywhere in the codebase -- an operator
        # flipping the documented switch got exactly nothing.
        _check_enabled()
        if not settings.category_department_mapping_enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_COVERAGE_DISABLED)
        all_active = await store.list_nodes(active_only=True)

        unmapped_categories: list[dict[str, Any]] = []
        mapped_departments: set[str] = set()

        for node in all_active:
            if node.level in (2, 3):  # Division or subcategory level
                if not node.department:
                    unmapped_categories.append({"key": node.key, "label": node.label, "level": node.level})
                else:
                    # Normalized the same way `_unreferenced_departments` normalizes
                    # the PIC side below -- without this, a category saved with
                    # stray case/whitespace (e.g. " Dept_Sales") fails to cancel
                    # its PIC and falsely shows up as unreferenced.
                    mapped_departments.add(node.department.strip().lower())

        # Departments that actually have a PIC configured, straight from the
        # same store /escalation/departments reads (escalation_router.py:226).
        # No hardcoded fallback: a guessed department list presented to an
        # operator as "unmapped" is worse than none, and is exactly what made
        # this report's previous ImportError invisible for a live deploy.
        unreferenced_departments, departments_source = await _unreferenced_departments(
            pic_store, mapped_departments
        )

        retired_dept_categories: list[str] = []

        return {
            "unmapped_categories": unmapped_categories,
            "unreferenced_departments": unreferenced_departments,
            "retired_department_categories": retired_dept_categories,
            "departments_source": departments_source,
        }

    return router
