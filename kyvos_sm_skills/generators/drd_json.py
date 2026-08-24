"""Generate Kyvos-compatible Complete JSON for DRD creation (Kyvos 2026.5+).

Emits a JSON dict suitable for ``POST /rest/v2/dataset-relationships/`` with
``Content-Type: application/x-www-form-urlencoded`` and the JSON payload
sent as a form-encoded ``json`` parameter.

Uses the Complete JSON format with ``iro`` wrapper, ``nodes[]`` with proper
IDs and ``relDataset`` objects, and ``relations[]`` with ``sourceId``,
``node1Id``, ``node2Id`` for correct relationship directions.
"""

from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from kyvos_sm_skills.generators.drd_xml import SimpleRel

logger = structlog.get_logger(__name__)


class DrdJsonGenerator:
    """Generate Kyvos DRD Complete JSON definitions from dataset relationships."""

    def __init__(
        self,
        drd_folder_id: str,
        drd_folder_name: str,
        dataset_folder_id: str = "",
        dataset_folder_name: str = "",
    ) -> None:
        self.drd_folder_id = drd_folder_id
        self.drd_folder_name = drd_folder_name
        self.dataset_folder_id = dataset_folder_id
        self.dataset_folder_name = dataset_folder_name

    def generate(
        self,
        *,
        drd_name: str,
        dataset_name_to_id: dict[str, str],
        relationships: list[SimpleRel],
        dataset_aliases: dict[str, str],
        fact_dataset_names: set[str] | None = None,
        bridge_dataset_names: set[str] | None = None,
    ) -> dict[str, Any]:
        """Generate Complete JSON payload for DRD creation.

        Args:
            drd_name: Name of the DRD.
            dataset_name_to_id: Mapping from Kyvos dataset name → dataset ID.
            relationships: List of semantic relationships.
            dataset_aliases: Mapping from semantic display name → Kyvos dataset name.
            fact_dataset_names: Optional set of fact dataset Kyvos names.
            bridge_dataset_names: Optional set of bridge dataset Kyvos names.

        Returns:
            Dict matching the Kyvos 2026.5+ Complete DRD JSON shape.
        """
        if not relationships:
            raise ValueError("relationships is empty; cannot generate DRD JSON")

        fact_set = fact_dataset_names or set()
        bridge_set = bridge_dataset_names or set()

        # ------------------------------------------------------------------
        # Resolve all datasets used by relationships
        # ------------------------------------------------------------------
        used_semantic_names: set[str] = set()
        for rel in relationships:
            used_semantic_names.add(rel.left_dataset)
            used_semantic_names.add(rel.right_dataset)

        # Build case-insensitive lookups
        aliases_ci: dict[str, str] = {k.lower(): v for k, v in dataset_aliases.items()}
        id_map_ci: dict[str, str] = {k.lower(): v for k, v in dataset_name_to_id.items()}

        semantic_to_kyvos: dict[str, str] = {}
        kyvos_to_id: dict[str, str] = {}
        missing_datasets: set[str] = set()

        for semantic_name in sorted(used_semantic_names):
            kyvos_name = (
                dataset_aliases.get(semantic_name)
                or aliases_ci.get(semantic_name.lower())
                or semantic_name
            )
            dataset_id = dataset_name_to_id.get(kyvos_name) or id_map_ci.get(kyvos_name.lower())

            if not dataset_id:
                logger.warning(
                    "drd_json_missing_dataset_id_skipping",
                    semantic_name=semantic_name,
                    kyvos_name=kyvos_name,
                    available=list(dataset_name_to_id.keys())[:10],
                )
                missing_datasets.add(semantic_name)
                continue

            semantic_to_kyvos[semantic_name] = kyvos_name
            kyvos_to_id[kyvos_name] = dataset_id

        # Filter out relationships that reference missing datasets
        if missing_datasets:
            before = len(relationships)
            relationships = [
                r for r in relationships
                if r.left_dataset not in missing_datasets and r.right_dataset not in missing_datasets
            ]
            logger.warning(
                "drd_json_relationships_pruned_missing_datasets",
                missing_datasets=sorted(missing_datasets),
                relationships_before=before,
                relationships_after=len(relationships),
            )

        if not relationships:
            raise ValueError(
                f"No relationships remain after pruning missing datasets: {sorted(missing_datasets)}. "
                f"Available Kyvos dataset names: {list(dataset_name_to_id.keys())[:30]}"
            )

        # Detect fact/bridge datasets
        if fact_set:
            fact_datasets = {n for n in kyvos_to_id.keys() if n in fact_set}
            if not fact_datasets:
                fact_datasets = self._detect_fact_datasets(kyvos_to_id.keys())
        else:
            fact_datasets = self._detect_fact_datasets(kyvos_to_id.keys())

        if bridge_set:
            bridge_datasets = {n for n in kyvos_to_id.keys() if n in bridge_set}
        else:
            bridge_datasets = self._detect_bridge_datasets(kyvos_to_id.keys())

        # Re-orient dim→dim relationships (same logic as XML generator)
        relationships = self._orient_dim_relationships(
            relationships, fact_datasets, semantic_to_kyvos
        )

        # ------------------------------------------------------------------
        # Build nodes
        # ------------------------------------------------------------------
        ordered_kyvos_names = sorted(kyvos_to_id.keys())
        kyvos_name_to_node_id: dict[str, str] = {}

        nodes_json: list[dict[str, Any]] = []
        for idx, kyvos_name in enumerate(ordered_kyvos_names, start=1):
            ds_id = kyvos_to_id[kyvos_name]
            node_id = f"{ds_id}_{idx}"
            kyvos_name_to_node_id[kyvos_name] = node_id

            if kyvos_name in fact_datasets:
                dataset_type = "FACT"
            elif kyvos_name in bridge_datasets:
                dataset_type = "BRIDGE"
            else:
                dataset_type = ""

            nodes_json.append({
                "id": node_id,
                "relDataset": {
                    "aliasName": kyvos_name,
                    "id": ds_id,
                    "type": dataset_type,
                },
            })

        # ------------------------------------------------------------------
        # Build relations
        # ------------------------------------------------------------------
        relations_json: list[dict[str, Any]] = []
        for rel in relationships:
            left_kyvos = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            right_kyvos = semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)

            if left_kyvos not in kyvos_name_to_node_id:
                raise ValueError(f"Left dataset node missing for '{left_kyvos}'")
            if right_kyvos not in kyvos_name_to_node_id:
                raise ValueError(f"Right dataset node missing for '{right_kyvos}'")

            node1_id = kyvos_name_to_node_id[left_kyvos]
            node2_id = kyvos_name_to_node_id[right_kyvos]

            rel_type = self._normalize_relationship_type(rel.relationship_type)

            relations_json.append({
                "sourceId": node1_id,
                "name": "undefined",
                "join": {
                    "type": "INNER",
                    "joinBy": [
                        {
                            "node1Key": {
                                "id": "",
                                "type": "",
                                "content": rel.left_column,
                            },
                            "node1SecondaryKey": {
                                "id": "",
                                "type": "",
                            },
                            "operator": "EQUAL_TO",
                            "node2SecondaryKey": {
                                "id": "",
                                "type": "",
                            },
                            "node2Key": {
                                "id": "",
                                "type": "",
                                "content": rel.right_column,
                            },
                        }
                    ],
                },
                "type": rel_type,
                "node2Id": node2_id,
                "node1Id": node1_id,
            })

        # ------------------------------------------------------------------
        # Build layout
        # ------------------------------------------------------------------
        positions = self._build_node_positions(
            ordered_kyvos_names, relationships, fact_datasets, semantic_to_kyvos
        )
        layout_nodes: list[dict[str, str]] = []
        for kyvos_name in ordered_kyvos_names:
            node_id = kyvos_name_to_node_id[kyvos_name]
            left, top = positions[kyvos_name]
            layout_nodes.append({
                "top": str(top),
                "left": str(left),
                "width": "200",
                "id": node_id,
                "collapse": "false",
                "height": "300",
            })

        # ------------------------------------------------------------------
        # Build complete IRO JSON
        # ------------------------------------------------------------------
        now_str = datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M:%S UTC")
        drd_id = self._gen_id()

        return {
            "iro": {
                "entityRepositDate": "",
                "ownerAppId": "",
                "linkedEntityId": "",
                "type": "DRD_OBJECT",
                "specific": {
                    "drdObject": {
                        "layout": {
                            "nodes": layout_nodes,
                        },
                        "panelProperties": {},
                        "nodes": nodes_json,
                        "lineType": "NOODLE",
                        "viewType": "GRAPHICAL",
                        "relations": relations_json,
                        "layoutProperty": {
                            "panelDetails": {
                                "files": {"style": {}, "id": "files"},
                                "datasets": {"style": {}, "id": "datasets"},
                                "properties": {"style": {}, "id": "properties"},
                            },
                            "columnDetails": [
                                {"panels": [], "style": {}},
                                {"panels": [], "style": {}},
                            ],
                        },
                    },
                },
                "folderId": self.drd_folder_id,
                "designSource": "DESIGNER",
                "ownerAppName": "",
                "common": {
                    "compatibilityVersion": "1",
                    "desc": "",
                    "tags": "",
                },
                "entityState": "",
                "name": drd_name,
                "isPublic": True,
                "subType": "",
                "repositDate": now_str,
                "id": drd_id,
                "folderName": self.drd_folder_name,
                "accessRights": "1",
                "categoryId": self.drd_folder_id,
            },
        }

    # ------------------------------------------------------------------
    # Helpers (mirrors drd_xml.py logic)
    # ------------------------------------------------------------------
    def _detect_fact_datasets(self, dataset_names: Iterable[str]) -> set[str]:
        facts: set[str] = set()
        for name in dataset_names:
            if name.strip().lower().startswith("fact"):
                facts.add(name)
        return facts

    def _detect_bridge_datasets(self, dataset_names: Iterable[str]) -> set[str]:
        bridges: set[str] = set()
        for name in dataset_names:
            if name.strip().lower().startswith("bridge"):
                bridges.add(name)
        return bridges

    def _normalize_relationship_type(self, rel_type: str | None) -> str:
        value = (rel_type or "").strip().lower().replace("_", "")
        if value in {"manytomany"}:
            return "MANY_TO_MANY"
        if value in {"onetoone", "onetone"}:
            return "ONE_TO_ONE"
        if value in {"manytoone"}:
            return "MANY_TO_ONE"
        if value in {"onetomany"}:
            return "ONE_TO_MANY"
        return "ONE_TO_MANY"

    def _orient_dim_relationships(
        self,
        relationships: list[SimpleRel],
        fact_datasets: set[str],
        semantic_to_kyvos: dict[str, str],
    ) -> list[SimpleRel]:
        """Re-orient dim→dim relationships so the parent dimension is the source.

        The semantic parser stores relationships as ``many_to_one`` with the
        FK-holding (child) table on the left and the referenced (parent)
        dimension on the right. In a snowflake this reads child -> parent, so
        swap those relationships so the DRD arrow goes parent -> child.
        """
        oriented: list[SimpleRel] = []
        for rel in relationships:
            left_kyvos = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            right_kyvos = semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)

            is_dim_to_dim = (
                left_kyvos not in fact_datasets and right_kyvos not in fact_datasets
            )
            rel_type = self._normalize_relationship_type(rel.relationship_type)

            if is_dim_to_dim and rel_type == "MANY_TO_ONE":
                oriented.append(
                    SimpleRel(
                        left_dataset=rel.right_dataset,
                        left_column=rel.right_column,
                        right_dataset=rel.left_dataset,
                        right_column=rel.left_column,
                        relationship_type="one_to_many",
                    )
                )
            else:
                oriented.append(rel)

        return oriented

    def _build_node_positions(
        self,
        ordered_kyvos_names: list[str],
        relationships: list[SimpleRel],
        fact_datasets: set[str],
        semantic_to_kyvos: dict[str, str],
        *,
        base_left: int = 50,
        base_top: int = 50,
        x_gap: int = 260,
        y_gap: int = 220,
    ) -> dict[str, tuple[int, int]]:
        """Return ``{kyvos_name: (left, top)}`` using BFS depth from fact nodes.

        Treats the graph as undirected so the visual layout is:
            FACT (depth 0) → DIM (depth 1) → downstream DIM (depth 2) → ...
        """
        positions: dict[str, tuple[int, int]] = {}

        if not fact_datasets:
            cols = 3
            for idx, kyvos_name in enumerate(ordered_kyvos_names):
                col = idx % cols
                row = idx // cols
                positions[kyvos_name] = (base_left + col * x_gap, base_top + row * y_gap)
            return positions

        adj: dict[str, set[str]] = {name: set() for name in ordered_kyvos_names}
        for rel in relationships:
            left = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            right = semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)
            if left in adj and right in adj:
                adj[left].add(right)
                adj[right].add(left)

        fact_names = {n for n in fact_datasets if n in adj}
        depth: dict[str, int] = {}
        queue: deque[str] = deque(sorted(fact_names))
        for fact_name in queue:
            depth[fact_name] = 0

        while queue:
            current = queue.popleft()
            for neighbor in adj[current]:
                if neighbor not in depth:
                    depth[neighbor] = depth[current] + 1
                    queue.append(neighbor)

        max_depth = max(depth.values()) if depth else -1

        by_depth: dict[int, list[str]] = {}
        for name in ordered_kyvos_names:
            d = depth.get(name, max_depth + 1)
            by_depth.setdefault(d, []).append(name)

        for d, names in sorted(by_depth.items()):
            for row, name in enumerate(sorted(names)):
                left = base_left + d * x_gap
                top = base_top + row * y_gap
                positions[name] = (left, top)

        return positions

    def _gen_id(self) -> str:
        return f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"
