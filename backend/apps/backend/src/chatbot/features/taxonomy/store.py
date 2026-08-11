"""P10 Task 1 -- the taxonomy store: Firestore-backed multi-level taxonomy.

Provides a 4-level taxonomy (Type -> Division -> Category -> Detail) stored in
Firestore. Nodes are retired, never deleted, ensuring historical cases remain
resolvable forever.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "taxonomy_nodes"


@dataclass
class TaxonomyNode:
    level: int
    key: str
    label: str
    parent: str | None = None
    active: bool = True
    department: str | None = None
    sort_order: int = 0

    def validate(self) -> TaxonomyNode:
        if self.level not in (1, 2, 3, 4):
            raise ValueError(f"level must be between 1 and 4; got {self.level}.")
        if not self.key or not self.key.strip():
            raise ValueError("key must not be empty.")
        if not self.label or not self.label.strip():
            raise ValueError("label must not be empty.")
        if self.level > 1 and not self.parent:
            raise ValueError(f"parent is required for node at level {self.level}.")
        if self.level == 1 and self.parent is not None:
            raise ValueError("level 1 node cannot have a parent.")
        return self


def _from_dict(data: dict[str, Any]) -> TaxonomyNode:
    return TaxonomyNode(
        level=int(data["level"]),
        key=str(data["key"]),
        label=str(data["label"]),
        parent=data.get("parent"),
        active=bool(data.get("active", True)),
        department=data.get("department"),
        sort_order=int(data.get("sort_order", 0)),
    )


class TaxonomyStore:
    """Firestore-backed taxonomy store following the established store pattern.

    Nodes are retired rather than deleted, so historical cases referencing older
    categories always resolve. There is deliberately NO delete method on this store.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _collection(self) -> firestore.CollectionReference:
        return self._client().collection(_COLLECTION)

    def _doc(self, key: str) -> firestore.DocumentReference:
        return self._collection().document(key)  # type: ignore[return-value]

    async def create_node(self, node: TaxonomyNode) -> bool:
        """Create or update a node. If parent is specified, parent must exist and be active."""
        node.validate()

        if node.level > 1 and node.parent:
            parent_node = await self.get_node(node.parent)
            if parent_node is None:
                raise ValueError(f"parent node {node.parent!r} does not exist.")
            if not parent_node.active:
                raise ValueError(f"parent node {node.parent!r} is retired.")

        try:
            await asyncio.to_thread(self._doc(node.key).set, asdict(node))
            return True
        except Exception as e:
            _log.error("taxonomy_create_node_failed", key=node.key, error=str(e))
            return False

    async def get_node(self, key: str) -> TaxonomyNode | None:
        """Get a taxonomy node by key (active or retired). Returns None if absent."""
        try:
            snap = await asyncio.to_thread(self._doc(key).get)
            if not snap.exists:
                return None
            return _from_dict(snap.to_dict() or {})
        except Exception as e:
            _log.error("taxonomy_get_node_failed", key=key, error=str(e))
            return None

    async def list_nodes(self, active_only: bool = True) -> list[TaxonomyNode]:
        """List taxonomy nodes. Sorted by level, sort_order, then key."""
        try:
            snaps = await asyncio.to_thread(self._collection().get)
            nodes: list[TaxonomyNode] = []
            for snap in snaps:
                data = snap.to_dict()
                if data:
                    node = _from_dict(data)
                    if not active_only or node.active:
                        nodes.append(node)
            nodes.sort(key=lambda n: (n.level, n.sort_order, n.key))
            return nodes
        except Exception as e:
            _log.error("taxonomy_list_nodes_failed", error=str(e))
            return []

    async def retire_node(self, key: str) -> list[TaxonomyNode]:
        """Retire a node by setting active=False. Returns list of active child nodes."""
        node = await self.get_node(key)
        if node is None:
            raise ValueError(f"node {key!r} does not exist.")

        node.active = False
        await asyncio.to_thread(self._doc(key).set, asdict(node))

        all_active = await self.list_nodes(active_only=True)
        active_children = [n for n in all_active if n.parent == key]
        return active_children

    async def tree(self) -> list[dict[str, Any]]:
        """Build nested active taxonomy tree matching cascading picker requirements."""
        active_nodes = await self.list_nodes(active_only=True)

        nodes_by_key: dict[str, dict[str, Any]] = {}
        for n in active_nodes:
            nodes_by_key[n.key] = {
                "key": n.key,
                "label": n.label,
                "level": n.level,
                "active": n.active,
                "department": n.department,
                "sort_order": n.sort_order,
                "children": [],
            }

        root_nodes: list[dict[str, Any]] = []

        # Sort nodes by level to ensure parents exist before attaching children
        sorted_active = sorted(active_nodes, key=lambda n: (n.level, n.sort_order, n.key))

        for n in sorted_active:
            node_dict = nodes_by_key[n.key]
            if n.parent and n.parent in nodes_by_key:
                nodes_by_key[n.parent]["children"].append(node_dict)
            elif n.level == 1:
                root_nodes.append(node_dict)

        # Sort children arrays by sort_order
        def _sort_tree(items: list[dict[str, Any]]) -> None:
            items.sort(key=lambda item: (item["sort_order"], item["key"]))
            for item in items:
                _sort_tree(item["children"])

        _sort_tree(root_nodes)
        return root_nodes


def build_taxonomy_store(settings: Settings) -> TaxonomyStore:
    return TaxonomyStore(settings)
