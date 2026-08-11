"""P10 Task 2 -- non-destructive seeding from env JSON.

Seeds the Firestore taxonomy store from CASE_TYPE_OPTIONS_JSON, CASE_TAXONOMY_JSON,
and CASE_DETAIL_OPTIONS_JSON.

Seeding is non-destructive:
- Never overwrites an operator-edited label.
- Never reactivates a retired node.
- Adds new nodes present in env JSON that do not yet exist in the store.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings
    from chatbot.features.taxonomy.store import TaxonomyStore, TaxonomyNode

_log = structlog.get_logger(__name__)


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "_", cleaned).strip("_")


def parse_json_safely(raw: str, default: Any = None) -> Any:
    if not raw or not raw.strip():
        return default
    try:
        return json.loads(raw)
    except Exception as e:
        _log.warning("taxonomy_seed_json_parse_failed", error=str(e))
        return default


async def seed_taxonomy_from_env(store: TaxonomyStore, settings: Settings) -> int:
    """Non-destructively seed taxonomy from settings env JSON.

    Returns the number of newly created nodes.
    """
    from chatbot.features.taxonomy.store import TaxonomyNode

    created_count = 0

    # Level 1: Case Types
    case_types_data = parse_json_safely(settings.case_type_options_json, {"options": []})
    type_options = case_types_data.get("options", []) if isinstance(case_types_data, dict) else []

    l1_keys: list[str] = []
    for idx, type_label in enumerate(type_options):
        type_key = f"type_{_slugify(type_label)}"
        l1_keys.append(type_key)
        existing = await store.get_node(type_key)
        if existing is None:
            node = TaxonomyNode(
                level=1,
                key=type_key,
                label=type_label,
                parent=None,
                active=True,
                sort_order=idx * 10,
            )
            if await store.create_node(node):
                created_count += 1

    # Level 2: Divisions & Level 3: Subcategories
    taxonomy_data = parse_json_safely(settings.case_taxonomy_json, {})
    if isinstance(taxonomy_data, dict) and l1_keys:
        primary_l1 = l1_keys[0]  # Attach divisions to primary level 1 type
        div_idx = 0
        for div_slug, div_info in taxonomy_data.items():
            if not isinstance(div_info, dict):
                continue
            div_label = div_info.get("label", div_slug.title())
            div_key = f"div_{_slugify(div_slug)}"

            existing_div = await store.get_node(div_key)
            if existing_div is None:
                div_node = TaxonomyNode(
                    level=2,
                    key=div_key,
                    label=div_label,
                    parent=primary_l1,
                    active=True,
                    sort_order=div_idx * 10,
                )
                if await store.create_node(div_node):
                    created_count += 1

            # Level 3: Subcategories under division
            subcats = div_info.get("subcategories", [])
            if isinstance(subcats, list):
                for sub_idx, sub_label in enumerate(subcats):
                    sub_key = f"cat_{_slugify(div_slug)}_{_slugify(sub_label)}"
                    existing_sub = await store.get_node(sub_key)
                    if existing_sub is None:
                        sub_node = TaxonomyNode(
                            level=3,
                            key=sub_key,
                            label=sub_label,
                            parent=div_key,
                            active=True,
                            sort_order=sub_idx * 10,
                        )
                        if await store.create_node(sub_node):
                            created_count += 1
            div_idx += 1

    # Level 4: Case Detail Options
    detail_data = parse_json_safely(settings.case_detail_options_json, {"options": []})
    detail_options = detail_data.get("options", []) if isinstance(detail_data, dict) else []

    for det_idx, det_string in enumerate(detail_options):
        # Format "<Division>: <Subcategory>: <Detail>"
        parts = [p.strip() for p in det_string.split(":")]
        if len(parts) >= 3:
            div_slug = _slugify(parts[0])
            sub_slug = _slugify(parts[1])
            det_label = ": ".join(parts[2:])
            parent_key = f"cat_{div_slug}_{sub_slug}"

            parent_node = await store.get_node(parent_key)
            if parent_node is not None:
                det_key = f"det_{div_slug}_{sub_slug}_{_slugify(det_label)}"
                existing_det = await store.get_node(det_key)
                if existing_det is None:
                    det_node = TaxonomyNode(
                        level=4,
                        key=det_key,
                        label=det_label,
                        parent=parent_key,
                        active=True,
                        sort_order=det_idx * 10,
                    )
                    if await store.create_node(det_node):
                        created_count += 1

    _log.info("taxonomy_seeded", newly_created=created_count)
    return created_count
