from __future__ import annotations

import structlog
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from xml.etree import ElementTree as ET

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
            relationships, fact_datasets, semantic_to_kyvos
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
        positions = self._build_node_positions(ordered_kyvos_names)

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
        Keep Kyvos-friendly DRD types.

        Your current working DRDs use ONE_TO_MANY.
        For now:
          many_to_one / one_to_many / auto -> ONE_TO_MANY
          many_to_many -> MANY_TO_MANY
          one_to_one -> ONE_TO_ONE
        """
        value = (rel_type or "").strip().lower()

        if value in {"many_to_many", "manytomany"}:
            return "MANY_TO_MANY"
        if value in {"one_to_one", "onetone"}:
            return "ONE_TO_ONE"

        return "ONE_TO_MANY"

    def _build_node_positions(self, ordered_kyvos_names: list[str]) -> dict[str, tuple[int, int]]:
        """
        Simple grid layout.
        Good enough for DRD generation and multi-fact safe.
        """
        positions: dict[str, tuple[int, int]] = {}
        base_left = 50
        base_top = 50
        x_gap = 260
        y_gap = 220
        cols = 3

        for idx, kyvos_name in enumerate(ordered_kyvos_names):
            col = idx % cols
            row = idx // cols
            positions[kyvos_name] = (base_left + col * x_gap, base_top + row * y_gap)

        return positions

    def _orient_dim_relationships(
        self,
        relationships: list[SimpleRel],
        fact_datasets: set[str],
        semantic_to_kyvos: dict[str, str],
    ) -> list[SimpleRel]:
        """Re-orient dim→dim relationships that point INTO a fact-adjacent dimension.

        Detects: Fact → DimX ← DimY  (wrong: DimY points TO a fact-connected dim)
        Fixes:   Fact → DimX → DimY  (correct: snowflake chain)

        A dimension is "fact-adjacent" when at least one fact has it as the right side
        of a relationship (i.e., a fact directly joins to it).
        """
        fact_adjacent_semantic: set[str] = set()
        fact_adjacent_kyvos: set[str] = set()

        for rel in relationships:
            left_kyvos = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            if left_kyvos in fact_datasets:
                fact_adjacent_semantic.add(rel.right_dataset)
                fact_adjacent_kyvos.add(
                    semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)
                )

        oriented: list[SimpleRel] = []
        for rel in relationships:
            left_kyvos = semantic_to_kyvos.get(rel.left_dataset, rel.left_dataset)
            right_kyvos = semantic_to_kyvos.get(rel.right_dataset, rel.right_dataset)

            is_dim_to_dim = (
                left_kyvos not in fact_datasets and right_kyvos not in fact_datasets
            )
            right_is_fact_adjacent = (
                rel.right_dataset in fact_adjacent_semantic
                or right_kyvos in fact_adjacent_kyvos
            )
            left_is_not_fact_adjacent = (
                rel.left_dataset not in fact_adjacent_semantic
                and left_kyvos not in fact_adjacent_kyvos
            )

            if is_dim_to_dim and right_is_fact_adjacent and left_is_not_fact_adjacent:
                oriented.append(
                    SimpleRel(
                        left_dataset=rel.right_dataset,
                        left_column=rel.right_column,
                        right_dataset=rel.left_dataset,
                        right_column=rel.left_column,
                        relationship_type=rel.relationship_type,
                    )
                )
            else:
                oriented.append(rel)

        return oriented

    def _gen_id(self) -> str:
        import random
        import time

        return f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"
