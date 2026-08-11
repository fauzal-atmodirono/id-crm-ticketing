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
from chatbot.features.taxonomy.chatwoot_sync import sync_taxonomy_to_chatwoot
from chatbot.features.taxonomy.store import TaxonomyNode, build_taxonomy_store

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def build_taxonomy_admin_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/admin/taxonomy", tags=["taxonomy-admin"])
    store = build_taxonomy_store(settings)

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
                # Trigger downstream sync
                await sync_taxonomy_to_chatwoot(store, settings)
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
            await sync_taxonomy_to_chatwoot(store, settings)
            return {
                "status": "retired",
                "key": key,
                "active_children": [asdict(child) for child in active_children],
            }
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @router.get("/coverage")
    async def get_taxonomy_coverage() -> dict[str, Any]:
        """Coverage report: active categories with no department, and departments not mapped."""
        _check_enabled()
        all_active = await store.list_nodes(active_only=True)

        unmapped_categories: list[dict[str, Any]] = []
        mapped_departments: set[str] = set()

        for node in all_active:
            if node.level in (2, 3):  # Division or subcategory level
                if not node.department:
                    unmapped_categories.append({"key": node.key, "label": node.label, "level": node.level})
                else:
                    mapped_departments.add(node.department)

        # Retrieve active escalation departments from PicStore if available
        known_departments: set[str] = {"dept_sales", "dept_aftersales", "dept_network", "dept_charging"}
        try:
            from chatbot.features.routing.store import build_pic_store
            pic_store = build_pic_store(settings)
            active_pics = await pic_store.list_active()
            if active_pics:
                known_departments = {f"dept_{pic.dept_slug}" for pic in active_pics}
        except Exception:
            pass

        unreferenced_departments = list(known_departments - mapped_departments)
        retired_dept_categories: list[str] = []

        return {
            "unmapped_categories": unmapped_categories,
            "unreferenced_departments": sorted(unreferenced_departments),
            "retired_department_categories": retired_dept_categories,
        }

    return router
