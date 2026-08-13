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

# Appendix A's Case Category (Complaint / Inquiry / Compliment & Feedback) is
# orthogonal to its Division -- any division can carry any type, and the fork's
# cascade chain (patch 0050) is case_category -> case_subcategory -> case_detail
# with case_type deliberately absent. The store cannot express that: every node
# above level 1 requires a parent. Divisions therefore hang off a neutral root
# that claims nothing, rather than off whichever case type happens to sort first.
_DIVISIONS_ROOT_KEY = "type_case_divisions"
_DIVISIONS_ROOT_LABEL = "Case divisions"


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

    Existing keys are read once, up front, rather than probed per node: the
    store builds a fresh `firestore.Client` per operation and `create_node`
    issues an existence check plus a parent check before writing, so probing
    made every boot after the first cost ~700 round trips to create nothing.
    One read now, and a populated store writes nothing at all.

    THE EMPTY PRE-READ IS NOT TRUSTED. `TaxonomyStore.list_nodes` catches every
    exception and returns `[]`, so a genuinely empty store and a transient
    `DeadlineExceeded` (or one malformed document making `_from_dict` raise)
    are indistinguishable from here -- and `create_node` writes with an
    unconditional `.set()`, so membership in `existing_keys` is the ONLY thing
    standing between a failed read and re-`set()`ting all 346 nodes back to
    `department=None, active=True` and the env label: every operator
    department mapping erased, every retired category resurrected into the
    agent picker, every edited label reverted, and `taxonomy_seeded
    newly_created=346` logged as though it were a first boot. So when the
    pre-read comes back empty we fall back to the per-node `get_node` probe for
    that run. A genuine first boot pays ~346 extra reads exactly once; a failed
    read can no longer overwrite anything. Do not "optimise" this back into
    trusting the empty set.
    """
    from chatbot.features.taxonomy.store import TaxonomyNode

    existing_keys = {node.key for node in await store.list_nodes(active_only=False)}
    # Empty could mean "empty store" or "read failed" -- see the docstring.
    probe_before_create = not existing_keys
    created_count = 0

    async def _create(node: TaxonomyNode) -> None:
        """Create `node` unless its key is already known. Never raises.

        A retired parent makes `create_node` raise, and this runs as a
        background task where an escaping exception would abandon the rest of
        the seed and log nothing useful. Skip and record instead.
        """
        nonlocal created_count
        if node.key in existing_keys:
            return
        if probe_before_create and await store.get_node(node.key) is not None:
            # The pre-read said "nothing exists" and this node disproves it:
            # the read failed. Record the key so later lookups stay cheap.
            existing_keys.add(node.key)
            return
        try:
            if await store.create_node(node):
                existing_keys.add(node.key)
                created_count += 1
        except ValueError as exc:
            _log.warning("taxonomy_seed_node_skipped", key=node.key, error=str(exc))

    # Level 1: Case Types
    case_types_data = parse_json_safely(settings.case_type_options_json, {"options": []})
    type_options = case_types_data.get("options", []) if isinstance(case_types_data, dict) else []

    for idx, type_label in enumerate(type_options):
        await _create(
            TaxonomyNode(
                level=1,
                key=f"type_{_slugify(type_label)}",
                label=type_label,
                parent=None,
                active=True,
                sort_order=idx * 10,
            )
        )

    # Level 2: Divisions & Level 3: Subcategories
    taxonomy_data = parse_json_safely(settings.case_taxonomy_json, {})
    label_to_div_slug: dict[str, str] = {}

    if isinstance(taxonomy_data, dict) and taxonomy_data:
        await _create(
            TaxonomyNode(
                level=1,
                key=_DIVISIONS_ROOT_KEY,
                label=_DIVISIONS_ROOT_LABEL,
                parent=None,
                active=True,
                sort_order=len(type_options) * 10,
            )
        )

        for div_idx, (div_slug, div_info) in enumerate(taxonomy_data.items()):
            if not isinstance(div_info, dict):
                continue
            div_label = div_info.get("label", div_slug.title())
            # A detail option is prefixed with the division LABEL; the division
            # node is keyed from the JSON KEY. "After Sales" -> "aftersales" is
            # only knowable from here. Store the SLUGIFIED key: level-3 keys are
            # built as f"cat_{_slugify(div_slug)}_{...}", and a non-slug-safe
            # JSON key (e.g. "after-sales") would otherwise let this map and
            # that key-building diverge again -- the exact bug class this task
            # exists to close.
            label_to_div_slug[_slugify(str(div_label))] = _slugify(div_slug)

            await _create(
                TaxonomyNode(
                    level=2,
                    key=f"div_{_slugify(div_slug)}",
                    label=div_label,
                    parent=_DIVISIONS_ROOT_KEY,
                    active=True,
                    sort_order=div_idx * 10,
                )
            )

            subcats = div_info.get("subcategories", [])
            if isinstance(subcats, list):
                for sub_idx, sub_label in enumerate(subcats):
                    await _create(
                        TaxonomyNode(
                            level=3,
                            key=f"cat_{_slugify(div_slug)}_{_slugify(sub_label)}",
                            label=sub_label,
                            parent=f"div_{_slugify(div_slug)}",
                            active=True,
                            sort_order=sub_idx * 10,
                        )
                    )

    # Level 4: Case Detail Options
    detail_data = parse_json_safely(settings.case_detail_options_json, {"options": []})
    detail_options = detail_data.get("options", []) if isinstance(detail_data, dict) else []
    unresolved: dict[str, int] = {}

    for det_idx, det_string in enumerate(detail_options):
        # Format "<Division>: <Subcategory>: <Detail>"
        parts = [p.strip() for p in det_string.split(":")]
        if len(parts) < 3:
            continue

        prefix_slug = _slugify(parts[0])
        div_slug = label_to_div_slug.get(prefix_slug, prefix_slug)
        sub_slug = _slugify(parts[1])
        det_label = ": ".join(parts[2:])
        parent_key = f"cat_{div_slug}_{sub_slug}"

        if parent_key not in existing_keys and await store.get_node(parent_key) is None:
            unresolved[parent_key] = unresolved.get(parent_key, 0) + 1
            continue

        await _create(
            TaxonomyNode(
                level=4,
                key=f"det_{div_slug}_{sub_slug}_{_slugify(det_label)}",
                label=det_label,
                parent=parent_key,
                active=True,
                sort_order=det_idx * 10,
            )
        )

    if unresolved:
        # Silence here is what hid the After Sales mismatch: 100 of 246 details
        # vanished with no log line at all.
        _log.warning(
            "taxonomy_seed_details_unresolved",
            dropped=sum(unresolved.values()),
            parents=sorted(unresolved),
        )

    _log.info("taxonomy_seeded", newly_created=created_count)
    return created_count
