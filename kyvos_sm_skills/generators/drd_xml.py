from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from xml.etree import ElementTree as ET

import structlog

_logger = structlog.get_logger(__name__)


@dataclass
class SimpleRel:
    left_dataset: str
    left_column: str
    right_dataset: str
    right_column: str
    relationship_type: str = "many_to_one"


class DrdXmlGenerator:
    """
    Generates Kyvos DRD_OBJECT XML.

    Inputs:
      - drd_folder_id/drd_folder_name: DRD folder created with folderType=DATASET_RELATIONSHIP
      - dataset_name_to_id: mapping from Kyvos dataset name -> Kyvos dataset id
            e.g. {"DimDate": "1773...", "FactLoanPortfolio": "1773..."}
      - relationships: list of semantic relationships
      - dataset_aliases: mapping from semantic display name -> Kyvos dataset name
            e.g. {"Loan Portfolio": "FactLoanPortfolio", "Date": "DimDate"}

    Multi-fact readiness:
      - Does NOT assume only one fact.
      - Builds nodes for every dataset participating in relationships.
      - Preserves relationship direction exactly as provided:
            left_dataset/left_column -> right_dataset/right_column
      - SOURCE_ID is set to NODE1_ID, which matches your current working pattern.
    """

    def __init__(self, drd_folder_id: str, drd_folder_name: str) -> None:
        self.drd_folder_id = drd_folder_id
        self.drd_folder_name = drd_folder_name

    def generate(
        self,
        *,
        drd_name: str,
        dataset_name_to_id: dict[str, str],
        relationships: list[SimpleRel],
        dataset_aliases: dict[str, str],
        fact_dataset_names: set[str] | None = None,
        bridge_dataset_names: set[str] | None = None,
    ) -> str:
        now_str = datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M:%S UTC")

        if not relationships:
            raise ValueError("relationships is empty; cannot generate DRD XML")

        # ------------------------------------------------------------------
        # Resolve all datasets used by relationships
        # semantic display name -> kyvos dataset name -> kyvos dataset id
        # ------------------------------------------------------------------
        used_semantic_names = self._collect_used_dataset_names(relationships)

        semantic_to_kyvos: dict[str, str] = {}
        kyvos_to_id: dict[str, str] = {}

        # Build case-insensitive alias / id lookups once so that spec names
        # like ASST_CLASS_DETAIL_TBL match lowercase alias keys and PascalCase id keys.
        aliases_ci: dict[str, str] = {k.lower(): v for k, v in dataset_aliases.items()}
        id_map_ci: dict[str, str] = {k.lower(): v for k, v in dataset_name_to_id.items()}

        missing_datasets: set[str] = set()
        for semantic_name in sorted(used_semantic_names):
            kyvos_name = (
                dataset_aliases.get(semantic_name)
                or aliases_ci.get(semantic_name.lower())
                or semantic_name
            )
            dataset_id = dataset_name_to_id.get(kyvos_name) or id_map_ci.get(kyvos_name.lower())

            if not dataset_id:
                _logger.warning(
                    "drd_xml_missing_dataset_id_skipping",
                    extra={
                        "semantic_name": semantic_name,
                        "kyvos_name": kyvos_name,
                        "available": list(dataset_name_to_id.keys())[:10],
                    },
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
            _logger.warning(
                "drd_xml_relationships_pruned_missing_datasets",
                extra={
                    "missing_datasets": sorted(missing_datasets),
                    "relationships_before": before,
                    "relationships_after": len(relationships),
                },
            )

        if not relationships:
            raise ValueError(
                f"No relationships remain after pruning missing datasets: {sorted(missing_datasets)}. "
                f"Available Kyvos dataset names: {list(dataset_name_to_id.keys())[:30]}"
            )

        if fact_dataset_names:
            fact_datasets = {n for n in kyvos_to_id.keys() if n in fact_dataset_names}
            if not fact_datasets:
                fact_datasets = self._detect_fact_datasets(kyvos_to_id.keys())
        else:
            fact_datasets = self._detect_fact_datasets(kyvos_to_id.keys())

        if bridge_dataset_names:
            bridge_datasets: set[str] = {n for n in kyvos_to_id.keys() if n in bridge_dataset_names}
        else:
            bridge_datasets = self._detect_bridge_datasets(kyvos_to_id.keys())

        # Re-orient any dim→dim relationship that points INTO a fact-adjacent dim
        relationships = self._orient_dim_relationships(
            relationships, fact_datasets, semantic_to_kyvos, bridge_datasets
        )

        # ------------------------------------------------------------------
        # Root IRO
        # ------------------------------------------------------------------
        iro = ET.Element(
            "IRO",
            {
                "ID": self._gen_id(),
                "NAME": drd_name,
                "TYPE": "DRD_OBJECT",
                "SUBTYPE": "",
                "CATEGORY_ID": self.drd_folder_id,
                "FOLDER_NAME": self.drd_folder_name,
                "FOLDER_ID": self.drd_folder_id,
                "ACCESSRIGHTS": "1",
                "OWNERAPPID": "Admin",
                "OWNERAPPNAME": "Admin",
                "REPOSITDATE": now_str,
                "LINKED_ENTITY_ID": "",
                "ISPUBLIC": "true",
                "ENTITY_STATE": "",
                "DESIGN_SOURCE": "DESIGNER",
            },
        )

        common = ET.SubElement(iro, "COMMON")
        ET.SubElement(common, "DESC").text = ""
        ET.SubElement(common, "TAGS").text = ""
        ET.SubElement(common, "COMPATIBILITY_VERSION").text = "1"

        specific = ET.SubElement(iro, "SPECIFIC")
        drdobj = ET.SubElement(
            specific,
            "DRDOBJECT",
            {"VIEW_TYPE": "TABULAR", "LINE_TYPE": "NOODLE"},
        )

        # ------------------------------------------------------------------
        # Layout/property sections
        # ------------------------------------------------------------------
        layout_prop = ET.SubElement(drdobj, "LAYOUT_PROPERTY")
        col_details = ET.SubElement(layout_prop, "COLUMN_DETAILS")
        col_details.text = '[{"panels":[],"style":{}},{"panels":[],"style":{"width":343.944}}]'

        panel_details = ET.SubElement(layout_prop, "PANEL_DETAILS")
        panel_details.text = (
            '{"files":{"style":{"height":451},"id":"files"},'
            '"datasets":{"style":{"height":451},"id":"datasets"},'
            '"properties":{"style":{"height":903},"id":"properties"}}'
        )

        panel_props = ET.SubElement(drdobj, "PANEL_PROPERTIES")
        ET.SubElement(panel_props, "NODE_PANEL_SORT_DETAILS").text = ""

        # ------------------------------------------------------------------
        # Nodes
        # ------------------------------------------------------------------
        nodes_el = ET.SubElement(drdobj, "NODES")
        kyvos_name_to_node_id: dict[str, str] = {}

        ordered_kyvos_names = sorted(kyvos_to_id.keys())
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

            node_el = ET.SubElement(nodes_el, "NODE", {"ID": node_id})
            rel_ds = ET.SubElement(
                node_el,
                "REL_DATASET",
                {
                    "ID": ds_id,
                    "TYPE": dataset_type,
                },
            )
            ET.SubElement(rel_ds, "ALIAS_NAME").text = kyvos_name

        # ------------------------------------------------------------------
        # Relations
        # ------------------------------------------------------------------
        relations_el = ET.SubElement(drdobj, "RELATIONS")

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

            rel_el = ET.SubElement(
                relations_el,
                "RELATION",
                {
                    "TYPE": rel_type,
                    "NODE1_ID": node1_id,
                    "NODE2_ID": node2_id,
                    "SOURCE_ID": node1_id,
                },
            )

            ET.SubElement(rel_el, "NAME").text = "undefined"

            join_el = ET.SubElement(rel_el, "JOIN", {"TYPE": "INNER"})
            join_by = ET.SubElement(join_el, "JOIN_BY", {"OPERATOR": "EQUAL_TO"})

            ET.SubElement(join_by, "NODE1_KEY", {"ID": "", "TYPE": ""}).text = rel.left_column
            ET.SubElement(join_by, "NODE2_KEY", {"ID": "", "TYPE": ""}).text = rel.right_column
            ET.SubElement(join_by, "NODE1_SECONDARY_KEY", {"ID": "", "TYPE": ""}).text = ""
            ET.SubElement(join_by, "NODE2_SECONDARY_KEY", {"ID": "", "TYPE": ""}).text = ""

        # ------------------------------------------------------------------
        # Layout positions
        # ------------------------------------------------------------------
        layout_el = ET.SubElement(drdobj, "LAYOUT")
        positions = self._build_node_positions(
            ordered_kyvos_names, relationships, fact_datasets, semantic_to_kyvos
        )

        for kyvos_name in ordered_kyvos_names:
            node_id = kyvos_name_to_node_id[kyvos_name]
            left, top = positions[kyvos_name]

            ET.SubElement(
                layout_el,
                "NODE",
                {
                    "ID": node_id,
                    "LEFT": str(left),
                    "TOP": str(top),
                    "HEIGHT": "300",
                    "WIDTH": "200",
                    "COLLAPSE": "false",
                },
            )

        return ET.tostring(iro, encoding="unicode")

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------
    def _collect_used_dataset_names(self, relationships: Iterable[SimpleRel]) -> set[str]:
        used: set[str] = set()
        for rel in relationships:
            used.add(rel.left_dataset)
            used.add(rel.right_dataset)
        return used

    def _detect_fact_datasets(self, dataset_names: Iterable[str]) -> set[str]:
        """
        Explicitly mark fact datasets.

        Current rule:
          - dataset name starts with 'Fact' (case-insensitive)

        Examples:
          FactLoanPortfolio -> FACT
          FactCollections   -> FACT
          DimDate           -> not FACT
        """
        facts: set[str] = set()

        for name in dataset_names:
            if name.strip().lower().startswith("fact"):
                facts.add(name)

        return facts

    def _detect_bridge_datasets(self, dataset_names: Iterable[str]) -> set[str]:
        """
        Heuristically identify bridge/junction datasets.

        Current rule:
          - dataset name starts with 'Bridge' (case-insensitive)

        Examples:
          Bridge_Product_Client  -> BRIDGE
          BridgeProductCategory  -> BRIDGE
          DimProduct             -> not BRIDGE
        """
        bridges: set[str] = set()
        for name in dataset_names:
            if name.strip().lower().startswith("bridge"):
                bridges.add(name)
        return bridges

    def _normalize_relationship_type(self, rel_type: str | None) -> str:
        """
        Kyvos DRD convention (verified against a real Kyvos DRD export):
        NODE1/SOURCE is the FK-holding "many" side (fact or child dim) and
        every resolved FK join is labeled ``ONE_TO_MANY``. Only genuine
        M:M edges (fact -> bridge) keep ``MANY_TO_MANY``.
        """
        value = (rel_type or "").strip().lower().replace("_", "")

        if value in {"manytomany"}:
            return "MANY_TO_MANY"
        if value in {"onetoone", "onetone"}:
            return "ONE_TO_ONE"
        if value in {"manytoone", "onetomany"}:
            return "ONE_TO_MANY"

        return "ONE_TO_MANY"

    def _orient_dim_relationships(
        self,
        relationships: list[SimpleRel],
        fact_datasets: set[str],
        semantic_to_kyvos: dict[str, str],
        bridge_datasets: set[str] | None = None,
    ) -> list[SimpleRel]:
        """Ensure the FK-holding ("many") side is the left/node1 side.

        Kyvos DRDs put the many side (fact or snowflake child dim) on
        node1/source and the referenced one side on node2 — displayed as
        ``fact -> dim`` and ``fact -> dim -> dim``. Relationships declared
        ``one_to_many`` carry the one side on the left, so swap those so
        the many side stays first.

        For dim -> dim edges the endpoint closer to a fact (BFS depth over
        the undirected graph) is kept on node1, so a dim that joins facts
        directly always precedes a deeper snowflake dim.
        """
        oriented: list[SimpleRel] = []
        for rel in relationships:
            raw_type = (rel.relationship_type or "").strip().lower().replace("_", "")
            if raw_type == "onetomany":
                oriented.append(
                    SimpleRel(
                        left_dataset=rel.right_dataset,
                        left_column=rel.right_column,
                        right_dataset=rel.left_dataset,
                        right_column=rel.left_column,
                        relationship_type="many_to_one",
                    )
                )
            else:
                oriented.append(rel)

        bridge_datasets = bridge_datasets or set()
        adj: dict[str, set[str]] = {}
        for rel in oriented:
            left = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            right = semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)
            adj.setdefault(left, set()).add(right)
            adj.setdefault(right, set()).add(left)
        depth: dict[str, int] = {}
        queue: deque[str] = deque(sorted(n for n in adj if n in fact_datasets))
        for n in queue:
            depth[n] = 0
        while queue:
            cur = queue.popleft()
            for nb in adj.get(cur, ()):
                if nb not in depth:
                    depth[nb] = depth[cur] + 1
                    queue.append(nb)

        result: list[SimpleRel] = []
        for rel in oriented:
            raw_type = (rel.relationship_type or "").strip().lower().replace("_", "")
            left = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            right = semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)
            is_dim_dim = (
                left not in fact_datasets and right not in fact_datasets
                and left not in bridge_datasets and right not in bridge_datasets
            )
            if (
                is_dim_dim
                and raw_type != "manytomany"
                and depth.get(right, 1 << 30) < depth.get(left, 1 << 30)
            ):
                result.append(
                    SimpleRel(
                        left_dataset=rel.right_dataset,
                        left_column=rel.right_column,
                        right_dataset=rel.left_dataset,
                        right_column=rel.left_column,
                        relationship_type=rel.relationship_type,
                    )
                )
            else:
                result.append(rel)

        return result

    def _build_node_positions(
        self,
        ordered_kyvos_names: list[str],
        relationships: list[SimpleRel] | None = None,
        fact_datasets: set[str] | None = None,
        semantic_to_kyvos: dict[str, str] | None = None,
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
        relationships = relationships or []
        fact_datasets = fact_datasets or set()
        semantic_to_kyvos = semantic_to_kyvos or {}

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
        import random
        import time

        return f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"
