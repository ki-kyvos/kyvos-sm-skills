from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from typing import Any
import xml.etree.ElementTree as ET
from xml.dom import minidom


# ---------------------------------------------------------
# helpers
# ---------------------------------------------------------

def _utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M:%S UTC")


def _safe_id(prefix: str, seed: str, length: int = 6) -> str:
    # Use decimal digits only — Kyvos parses the suffix after the prefix as a
    # Long (Java) and rejects any hex characters (a-f).
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % (10 ** length)
    return f"{prefix}_{h:0{length}d}"


def _cdata(text: str) -> ET.Element:
    el = ET.Element("_CDATA")
    el.text = "" if text is None else str(text)
    return el


def _append_cdata(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.append(_cdata(text))
    return el


def _append_empty_cdata(
    parent: ET.Element,
    tag: str,
    attrib: dict[str, str] | None = None,
) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib or {})
    el.append(_cdata(""))
    return el


def _prettify_with_cdata_no_decl(elem: ET.Element) -> str:
    raw = ET.tostring(elem, encoding="utf-8")
    dom = minidom.parseString(raw)

    def replace_cdata_nodes(node):
        for child in list(node.childNodes):
            replace_cdata_nodes(child)

        if node.nodeType == node.ELEMENT_NODE:
            cdata_children = [c for c in node.childNodes if getattr(c, "tagName", None) == "_CDATA"]
            if cdata_children:
                text_val = ""
                first = cdata_children[0]
                if first.firstChild:
                    text_val = first.firstChild.nodeValue or ""

                for c in list(node.childNodes):
                    node.removeChild(c)

                node.appendChild(dom.createCDATASection(text_val))

    replace_cdata_nodes(dom)
    xml = dom.documentElement.toprettyxml(indent="    ")
    xml = "\n".join(line for line in xml.splitlines() if line.strip())
    return xml


def _datatype_map(kyvos_datatype: str) -> str:
    dt = (kyvos_datatype or "").upper().split("(")[0].strip()
    if dt in ("CHAR", "VARCHAR", "STRING", "TEXT"):
        return "CHAR"
    if dt in ("NUMBER", "INT", "INTEGER", "BIGINT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL", "BOOLEAN", "BOOL"):
        return "NUMBER"
    if dt in ("DATE", "TIMESTAMP", "DATETIME"):
        return "DATE"
    return "CHAR"


def _format_type_for_datatype(datatype: str) -> str:
    if datatype == "DATE":
        return "4"
    if datatype == "NUMBER":
        return "2"
    return ""


def _measure_format_for_column(col_name: str) -> tuple[str, str]:
    name = (col_name or "").lower()
    if any(x in name for x in ("rate", "ratio", "pct", "percent", "probability", "pd", "lgd", "ltv")):
        return "#,##0.00", "2"
    if any(x in name for x in ("amount", "balance", "value", "loss", "payment", "provision", "exposure", "cost")):
        return "#,##0.00", "2"
    return "#,##0", "2"


def _format_type_for_measure_format(format_string: str) -> str:
    fmt = (format_string or "").strip()
    if not fmt:
        return ""
    return "2"


_AGGREGATION_TYPE_TO_SUMMARYFUNCTION: dict[str, str] = {
    "sum": "0",
    "average": "1",
    "count": "2",
    "minimum": "3",
    "maximum": "4",
    # 5 = Variance  (unsupported for processed semantic models)
    # 6 = StdDev    (unsupported for processed semantic models)
    "first_child": "8",
    "last_child": "9",
    "first_non_empty_child": "10",
    "distinct_count": "11",
}


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def _dedupe_measure_name(name: str, used: set[str]) -> str:
    """Return *name* if unique in *used*, otherwise append _1, _2, … until unique."""
    if name not in used:
        used.add(name)
        return name
    i = 1
    while f"{name}_{i}" in used:
        i += 1
    unique = f"{name}_{i}"
    used.add(unique)
    return unique


# ---------------------------------------------------------
# dataclasses
# ---------------------------------------------------------

@dataclass
class ColumnInfo:
    name: str
    datatype: str


@dataclass
class DrdNode:
    node_id: str
    dataset_id: str
    dataset_name: str
    node_type: str = "dimension"  # fact | dimension | bridge | snowflake_dimension


@dataclass
class DrdRelation:
    node1_id: str
    node2_id: str
    source_id: str
    relation_type: str
    node1_key: str
    node2_key: str


@dataclass
class FactContext:
    fact_node: DrdNode
    connected_dimension_node_ids: set[str] = field(default_factory=set)
    join_keys: set[str] = field(default_factory=set)


# ---------------------------------------------------------
# generator
# ---------------------------------------------------------

class SModelXmlGenerator:
    """
    Multi-fact aware semantic model generator.

    Key points:
    - parses DRD nodes / relations
    - detects fact nodes dynamically
    - creates one measure group per fact
    - builds dimensions for all non-fact nodes
    - creates hierarchies from use-case supplied hierarchy specs
    - removes hierarchy level columns from ATTRS so they are not duplicated
    - supports LLM-generated calculated measures scoped to a fact dataset
    """

    def __init__(
        self,
        *,
        folder_id: str,
        folder_name: str,
        smodel_name: str,
        connection_name: str,
        drd_id: str,
        drd_name: str,
        drd_xml: str,
        dataset_name_to_id: dict[str, str],
        dataset_columns: dict[str, list[dict[str, Any]]],
        hierarchy_specs: list[Any] | None = None,
        semantic_measures: list[Any] | None = None,
        compatibility_version: str = "1",
        owner_appid: str = "Admin",
        owner_appname: str = "Admin",
        is_public: str = "true",
        skipped_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.folder_id = folder_id
        self.folder_name = folder_name
        self.smodel_name = smodel_name
        self.connection_name = connection_name
        self.drd_id = drd_id
        self.drd_name = drd_name
        self.drd_xml = drd_xml
        self.dataset_name_to_id = dataset_name_to_id
        self.dataset_columns = dataset_columns
        self.hierarchy_specs = hierarchy_specs or []
        self.semantic_measures = semantic_measures or []
        self.skipped_items = skipped_items if skipped_items is not None else []
        import logging
        self.logger = logging.getLogger(__name__)

        self.compatibility_version = compatibility_version
        self.owner_appid = owner_appid
        self.owner_appname = owner_appname
        self.is_public = is_public
        self.smodel_id: str | None = None  # set during generate()

    # -----------------------------------------------------
    # public
    # -----------------------------------------------------

    def generate(self) -> str:
        drd_meta = self._parse_drd()

        iro_id = f"SMODEL_OBJECT_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        self.smodel_id = iro_id

        iro = ET.Element(
            "IRO",
            {
                "ID": iro_id,
                "NAME": self.smodel_name,
                "TYPE": "ANALYTICAL",
                "SUBTYPE": "CUBE",
                "CATEGORY_ID": self.folder_id,
                "FOLDER_NAME": self.folder_name,
                "FOLDER_ID": self.folder_id,
                "ACCESSRIGHTS": "1",
                "OWNERAPPID": self.owner_appid,
                "OWNERAPPNAME": self.owner_appname,
                "REPOSITDATE": _utc_str(),
                "LINKED_ENTITY_ID": "",
                "ISPUBLIC": self.is_public,
                "ENTITY_STATE": "",
                "DESIGN_SOURCE": "DESIGNER",
            },
        )

        self._build_common(iro)
        self._build_specific(iro, drd_meta)
        return _prettify_with_cdata_no_decl(iro)

    # -----------------------------------------------------
    # DRD parsing
    # -----------------------------------------------------

    def _parse_drd(self) -> dict[str, Any]:
        root = ET.fromstring(self.drd_xml)
        response_iro = root.find(".//IRO")
        search_root = response_iro if response_iro is not None else root

        nodes: dict[str, DrdNode] = {}

        for node_el in search_root.findall(".//NODES/NODE"):
            node_id = (node_el.get("ID") or "").strip()
            rel_dataset = node_el.find("./REL_DATASET")
            if rel_dataset is None:
                continue

            dataset_id = (rel_dataset.get("ID") or "").strip()
            alias_el = rel_dataset.find("./ALIAS_NAME")
            dataset_name = (alias_el.text or "").strip() if alias_el is not None else ""

            if not (node_id and dataset_id and dataset_name):
                continue

            rel_ds_type = (rel_dataset.get("TYPE") or "").strip().upper()
            if rel_ds_type == "FACT":
                resolved_node_type = "fact"
            elif rel_ds_type in ("BRIDGE", "SNOWFLAKE_DIMENSION"):
                resolved_node_type = rel_ds_type.lower()
            else:
                resolved_node_type = self._infer_node_type(dataset_name)

            nodes[node_id] = DrdNode(
                node_id=node_id,
                dataset_id=dataset_id,
                dataset_name=dataset_name,
                node_type=resolved_node_type,
            )

        if not nodes:
            raise ValueError("No DRD nodes found in DRD XML")

        relations: list[DrdRelation] = []
        outgoing_count: dict[str, int] = {}

        for rel_el in search_root.findall(".//RELATIONS/RELATION"):
            join_by = rel_el.find("./JOIN/JOIN_BY")
            node1_key = ""
            node2_key = ""
            if join_by is not None:
                n1 = join_by.find("./NODE1_KEY")
                n2 = join_by.find("./NODE2_KEY")
                node1_key = (n1.text or "").strip() if n1 is not None else ""
                node2_key = (n2.text or "").strip() if n2 is not None else ""

            rel = DrdRelation(
                node1_id=(rel_el.get("NODE1_ID") or "").strip(),
                node2_id=(rel_el.get("NODE2_ID") or "").strip(),
                source_id=(rel_el.get("SOURCE_ID") or "").strip(),
                relation_type=(rel_el.get("TYPE") or "").strip() or "ONE_TO_MANY",
                node1_key=node1_key,
                node2_key=node2_key,
            )
            relations.append(rel)

            if rel.source_id:
                outgoing_count[rel.source_id] = outgoing_count.get(rel.source_id, 0) + 1

        if not relations:
            raise ValueError("No DRD relations found in DRD XML")

        fact_nodes = [n for n in nodes.values() if n.node_type == "fact"]

        if not fact_nodes:
            if outgoing_count:
                candidate_ids = sorted(outgoing_count.keys(), key=lambda x: outgoing_count[x], reverse=True)
                for cid in candidate_ids:
                    if cid in nodes:
                        nodes[cid].node_type = "fact"
                        fact_nodes.append(nodes[cid])
            if not fact_nodes:
                raise ValueError("Could not identify any fact node from DRD")

        fact_contexts: dict[str, FactContext] = {
            f.node_id: FactContext(fact_node=f) for f in fact_nodes
        }

        for rel in relations:
            left = nodes.get(rel.node1_id)
            right = nodes.get(rel.node2_id)
            if not left or not right:
                continue

            if left.node_type == "fact" and right.node_type != "fact":
                fact_contexts[left.node_id].connected_dimension_node_ids.add(right.node_id)
                if rel.node1_key:
                    fact_contexts[left.node_id].join_keys.add(rel.node1_key)

            elif right.node_type == "fact" and left.node_type != "fact":
                fact_contexts[right.node_id].connected_dimension_node_ids.add(left.node_id)
                if rel.node2_key:
                    fact_contexts[right.node_id].join_keys.add(rel.node2_key)

            elif rel.source_id in fact_contexts:
                other_id = rel.node2_id if rel.node1_id == rel.source_id else rel.node1_id
                if other_id in nodes and nodes[other_id].node_type != "fact":
                    fact_contexts[rel.source_id].connected_dimension_node_ids.add(other_id)
                    if rel.node1_id == rel.source_id and rel.node1_key:
                        fact_contexts[rel.source_id].join_keys.add(rel.node1_key)
                    elif rel.node2_id == rel.source_id and rel.node2_key:
                        fact_contexts[rel.source_id].join_keys.add(rel.node2_key)

        dimension_nodes = [n for n in nodes.values() if n.node_type != "fact"]

        return {
            "nodes": nodes,
            "relations": relations,
            "fact_nodes": fact_nodes,
            "dimension_nodes": dimension_nodes,
            "fact_contexts": fact_contexts,
        }

    def _infer_node_type(self, dataset_name: str) -> str:
        name = (dataset_name or "").strip().lower()

        if name.startswith("fact") or name.startswith("fact_"):
            return "fact"
        if name.startswith("bridge") or name.startswith("bridge_"):
            return "bridge"
        if name.startswith("snowflake") or name.startswith("snowflake_"):
            return "snowflake_dimension"
        if name.startswith("dim") or name.startswith("dim_"):
            return "dimension"

        return "dimension"

    # -----------------------------------------------------
    # root builders
    # -----------------------------------------------------

    def _build_common(self, iro: ET.Element) -> None:
        common = ET.SubElement(iro, "COMMON")
        _append_cdata(common, "DESC", "")
        _append_cdata(common, "TAGS", "")
        _append_cdata(common, "COMPATIBILITY_VERSION", self.compatibility_version)

    def _build_specific(self, iro: ET.Element, drd_meta: dict[str, Any]) -> None:
        specific = ET.SubElement(iro, "SPECIFIC")

        attrs = ET.SubElement(specific, "ATTRS")
        self._add_attr(attrs, "CUBEUNIQUENAME", "")
        self._add_attr(attrs, "CONNECTION_NAME", self.connection_name)
        self._add_attr(attrs, "ISBOUND", "false")
        self._add_attr(attrs, "RAW_DATA_QUERYING", "0")
        self._add_attr(attrs, "RAW_DATA_CONNECTION_MODE", "DEFAULT")
        self._add_attr(attrs, "MODEL_TYPE", "BASE")
        self._add_attr(attrs, "DRD_ID", self.drd_id)
        self._add_attr(attrs, "DRD_NAME", self.drd_name)
        self._add_attr(attrs, "CO_TYPE", "INTERNAL")

        cube = ET.SubElement(specific, "CUBEOBJECT", {"VIEW_TYPE": ""})

        self._build_layout_property(cube)
        self._build_aggregation_strategy(cube)
        self._build_writeback_settings(cube)

        dims_el = ET.SubElement(cube, "DIMENSIONS")
        dims_el.append(self._build_measures_dimension())

        # ── D5: drop orphan dims (no fact connection) ──────────────────────
        fact_contexts = drd_meta["fact_contexts"]
        connected_dim_node_ids: set[str] = set()
        for _ctx in fact_contexts.values():
            connected_dim_node_ids.update(_ctx.connected_dimension_node_ids)

        retained_dims: list[DrdNode] = []
        for dim_node in drd_meta["dimension_nodes"]:
            if dim_node.node_id not in connected_dim_node_ids:
                self.logger.warning(
                    "orphan_dimension_skipped",
                    dim=dim_node.dataset_name,
                    reason="no_fact_relationship",
                )
                self.skipped_items.append({
                    "kind": "dimension",
                    "name": dim_node.dataset_name,
                    "reason": "no_fact_relationship",
                    "detail": (
                        "Dimension has no fact→dim relationship in the DRD; "
                        "Kyvos would fail validation with 'could not find "
                        "relation between dimension and any measure'."
                    ),
                })
                continue
            retained_dims.append(dim_node)

        # ── D2: detect duplicate attribute names across retained dims ──────
        # Kyvos rejects the model when the same attribute name appears in
        # more than one dimension (e.g. birthdate in Employee and Customer).
        attr_name_counts: dict[str, int] = {}
        for dim_node in retained_dims:
            all_cols = self._cols_for(dim_node.dataset_name)
            join_keys = self._dimension_join_keys(
                dim_node.node_id, drd_meta["relations"]
            )
            for col in all_cols:
                if col.name in join_keys:
                    continue
                k = col.name.lower()
                attr_name_counts[k] = attr_name_counts.get(k, 0) + 1
        self._duplicate_attr_names = {
            n for n, c in attr_name_counts.items() if c > 1
        }
        if self._duplicate_attr_names:
            self.logger.info(
                "duplicate_attribute_names_detected",
                names=sorted(self._duplicate_attr_names),
            )

        for dim_node in retained_dims:
            dims_el.append(self._build_regular_dimension(dim_node, drd_meta))

        self._build_measure_groups_and_measures(cube, drd_meta)

        link = ET.SubElement(cube, "LINK_DESIGN_LAYOUT")
        link.text = ""

        delta = ET.SubElement(specific, "DELTA_INFO")
        delta.text = ""

    def _build_layout_property(self, cube: ET.Element) -> None:
        layout = ET.SubElement(cube, "LAYOUT_PROPERTY")
        _append_cdata(
            layout,
            "COLUMN_DETAILS",
            '[{"panels":[],"style":{}},{"panels":[],"style":{"width":272.222}}]',
        )
        _append_cdata(
            layout,
            "PANEL_DETAILS",
            '{"sourceFields":{"style":{"height":539},"id":"sourceFields"},"filters":{"style":{"height":214},"id":"filters"},"properties":{"style":{"height":324},"id":"properties"}}',
        )
        sliding = ET.SubElement(layout, "SLIDING_WINDOWS")
        sliding.text = ""

    def _build_aggregation_strategy(self, cube: ET.Element) -> None:
        agg = ET.SubElement(cube, "AGGREGATION_STRATEGY", {"TYPE": "CONFIGURATION_DRIVEN"})
        cfg = ET.SubElement(agg, "CONFIGURATION_DRIVEN")
        props = ET.SubElement(cfg, "ENTITY_PROPS_VALUES")

        for pname in (
            "kyvos.build.precompute.level.threshold",
            "kyvos.build.dimensions.materialize",
            "kyvos.build.precompute.degree",
            "kyvos.build.precompute.hierarchy.levels",
        ):
            p = ET.SubElement(props, "PROPERTY")
            _append_cdata(p, "NAME", pname)
            _append_cdata(p, "VALUE", "")
            _append_cdata(p, "MODE", "DEFAULT")

    def _build_writeback_settings(self, cube: ET.Element) -> None:
        wb = ET.SubElement(cube, "WRITEBACK_SETTINGS", {"ENABLED": "false"})
        ids = ET.SubElement(wb, "IDENTIFIER_ELEMENTS")
        ids.text = ""

    def _add_attr(self, parent: ET.Element, name: str, value: str) -> None:
        attr = ET.SubElement(parent, "ATTR", {"NAME": name})
        value_el = ET.SubElement(attr, "VALUE")
        value_el.append(_cdata(value))

    # -----------------------------------------------------
    # measures dimension
    # -----------------------------------------------------

    def _build_measures_dimension(self) -> ET.Element:
        dim = ET.Element(
            "DIMENSION",
            {
                "ID": "Dim_Measures",
                "DIM_TO_FACT_MAPPING": "ONE_TO_MANY",
                "RESTRICT": "false",
                "RESTRICTVALUES": "false",
                "IS_ACCESSIBLE": "true",
                "ISVISIBLE": "true",
                "MATERIALIZE": "",
                "CUBE_VIEW_TYPE": "",
            },
        )

        inc = ET.SubElement(dim, "INCREMENTAL_UPDATE_PROPERTIES", {"PROCESSING_MODE": "1"})
        inc.text = ""

        _append_cdata(dim, "UNIQUENAME", "Measures")
        _append_cdata(dim, "NAME", "Measures")
        _append_cdata(dim, "TYPE", "MEASURE")
        _append_cdata(dim, "PREDEF_TIME_HIERARCHY", "")
        _append_empty_cdata(dim, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})
        _append_cdata(dim, "DEFAULTHIERARCHYUNIQUENAME", "Measures")
        _append_cdata(dim, "DESCRIPTION", "")
        _append_cdata(dim, "TAGS", "")

        datasources = ET.SubElement(dim, "DATASOURCES")
        datasources.text = ""

        hierarchies = ET.SubElement(dim, "HIERARCHIES")
        hierarchy = ET.SubElement(
            hierarchies,
            "HIERARCHY",
            {
                "HASALL": "false",
                "ISVISIBLE": "true",
                "ISDEFAULT": "false",
                "IS_CUSTOMCALENDAR": "false",
                "HAS_ALTERNATE_PATH": "false",
                "HAS_PARENTCHILD_RELATION": "false",
                "PC_LEVEL_COUNT": "-1",
                "IS_ACCESSIBLE": "true",
                "CUBE_VIEW_TYPE": "",
                "MATERIALIZE": "",
                "DISPLAY_FOLDER": "",
            },
        )
        _append_cdata(hierarchy, "UNIQUENAME", "Measures")
        _append_cdata(hierarchy, "NAME", "")
        _append_cdata(hierarchy, "DESCRIPTION", "")
        _append_cdata(hierarchy, "TAGS", "")
        _append_cdata(hierarchy, "DEFAULTMEMBERUNIQUENAME", "")
        _append_cdata(hierarchy, "ALLMEMBERUNIQUENAME", "")
        _append_cdata(hierarchy, "PREDEF_TIME_HIERARCHY", "")
        _append_empty_cdata(hierarchy, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})
        q = ET.SubElement(hierarchy, "QUALIFY_MEMBERS", {"TYPE": "ALL_PARENTS"})
        q.append(_cdata(""))

        levels = ET.SubElement(hierarchy, "LEVELS")
        lvl = ET.SubElement(
            levels,
            "LEVEL",
            {
                "DATATYPE": "Regular",
                "ISALL": "false",
                "SHOW_VALUES": "0",
                "MAPTYPE": "",
                "IS_ACCESSIBLE": "true",
                "ISVISIBLE": "true",
                "IS_KEYELEMENT": "false",
                "IS_VISIBLE_CONVERSATIONAL": "true",
                "IS_DELETED": "false",
                "HIDE_MEMBER": "0",
                "MATERIALIZE": "",
                "PROCESS_TYPE": "DATA_AND_METADATA",
                "AGGREGATION_TYPE": "BOTH",
            },
        )
        _append_cdata(lvl, "UNIQUENAME", "MeasuresLevel")
        _append_cdata(lvl, "NAME", "")
        _append_cdata(lvl, "DESCRIPTION", "")
        _append_cdata(lvl, "TAGS", "")
        ET.SubElement(lvl, "MEMBER_PROPERTIES")
        _append_cdata(lvl, "FULLY_QUALIFIED_NAME", "")
        _append_cdata(lvl, "DATEDATATYPE", "")
        _append_cdata(lvl, "FIELDDATATYPE", "")
        _append_cdata(lvl, "SUBDATATYPE", "")
        _append_cdata(lvl, "DISPLAYFIELDDATATYPE", "")
        _append_cdata(lvl, "DISPLAYFIELDSUBDATATYPE", "NONE")
        _append_cdata(lvl, "GEOROLE", "")
        _append_cdata(lvl, "MAPLEVEL", "")
        _append_empty_cdata(lvl, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})
        _append_empty_cdata(lvl, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
        _append_empty_cdata(lvl, "PARENTFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "DATA_TYPE": ""})
        _append_empty_cdata(lvl, "DISPLAYFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
        _append_cdata(lvl, "HASTIMEINDATEFORMAT", "false")
        _append_cdata(lvl, "DATEFORMAT", "")
        _append_cdata(lvl, "FORMATTYPE", "")
        ET.SubElement(lvl, "PROPERTIES")

        attrs = ET.SubElement(dim, "ATTRS")
        attr = ET.SubElement(
            attrs,
            "ATTR",
            {
                "DATATYPE": "",
                "IS_ACCESSIBLE": "true",
                "ISVISIBLE": "true",
                "IS_DELETED": "false",
                "IS_KEYELEMENT": "false",
                "IS_VISIBLE_CONVERSATIONAL": "true",
                "DISPLAY_FOLDER": "",
                "MATERIALIZE": "",
                "PROCESS_TYPE": "DATA_AND_METADATA",
                "AGGREGATION_TYPE": "BOTH",
            },
        )
        _append_cdata(attr, "UNIQUENAME", "Attribute")
        _append_cdata(attr, "NAME", "Attribute")
        _append_cdata(attr, "DESCRIPTION", "")
        _append_cdata(attr, "TAGS", "")
        _append_cdata(attr, "TYPE", "")
        _append_empty_cdata(attr, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
        _append_empty_cdata(attr, "DISPLAYFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
        _append_cdata(attr, "GEOROLE", "")
        _append_cdata(attr, "MAPLEVEL", "")
        _append_cdata(attr, "HASTIMEINDATEFORMAT", "false")
        _append_cdata(attr, "DATEDATATYPE", "")
        _append_cdata(attr, "FIELDDATATYPE", "")
        _append_cdata(attr, "SUBDATATYPE", "")
        _append_cdata(attr, "DISPLAYFIELDDATATYPE", "")
        _append_cdata(attr, "DISPLAYFIELDSUBDATATYPE", "NONE")
        _append_cdata(attr, "DATEFORMAT", "")
        _append_cdata(attr, "FORMATTYPE", "")
        ET.SubElement(attr, "MEMBER_PROPERTIES")

        calc = ET.SubElement(dim, "CALC_MEMBERS")
        calc.text = ""

        ET.SubElement(dim, "SCD", {"TYPE": "2"})
        return dim

    # -----------------------------------------------------
    # dimensions
    # -----------------------------------------------------

    def _build_regular_dimension(self, dim_node: DrdNode, drd_meta: dict[str, Any]) -> ET.Element:
        dataset_name = dim_node.dataset_name
        query_id = dim_node.node_id
        dim_id = _safe_id("DIM", dataset_name, 6)

        all_cols = self._cols_for(dataset_name)
        join_keys = self._dimension_join_keys(dim_node.node_id, drd_meta["relations"])

        dim_cols = [c for c in all_cols if c.name not in join_keys]
        if not dim_cols:
            dim_cols = all_cols[:]

        attr_unique_by_name = {
            col.name: _safe_id("DIMENSION_ATTRIBUTE", f"{dataset_name}:{col.name}", 6)
            for col in dim_cols
        }

        hierarchy_defs = self._hierarchy_definitions(dataset_name, {c.name for c in all_cols})
        default_hierarchy_unique_name = hierarchy_defs[0]["unique_name"] if hierarchy_defs else ""

        hierarchy_column_names: set[str] = set()
        for hierarchy_def in hierarchy_defs:
            hierarchy_column_names.update(hierarchy_def["levels"])
            if hierarchy_def.get("is_parent_child"):
                if hierarchy_def.get("parent_column"):
                    hierarchy_column_names.add(hierarchy_def["parent_column"])
                if hierarchy_def.get("child_column"):
                    hierarchy_column_names.add(hierarchy_def["child_column"])
            if hierarchy_def.get("custom_rollup_weight_column"):
                hierarchy_column_names.add(hierarchy_def["custom_rollup_weight_column"])

        attr_cols = [c for c in dim_cols if c.name not in hierarchy_column_names]

        dim = ET.Element(
            "DIMENSION",
            {
                "ID": dim_id,
                "DIM_TO_FACT_MAPPING": "ONE_TO_MANY",
                "RESTRICT": "false",
                "RESTRICTVALUES": "false",
                "IS_ACCESSIBLE": "true",
                "ISVISIBLE": "true",
                "MATERIALIZE": "",
                "CUBE_VIEW_TYPE": "",
            },
        )

        inc = ET.SubElement(dim, "INCREMENTAL_UPDATE_PROPERTIES", {"PROCESSING_MODE": "1"})
        inc.text = ""

        _append_cdata(dim, "UNIQUENAME", dim_id)
        _append_cdata(dim, "NAME", dataset_name)
        _append_cdata(dim, "TYPE", "Regular")
        _append_cdata(dim, "PREDEF_TIME_HIERARCHY", "")
        _append_empty_cdata(dim, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})
        _append_cdata(dim, "DEFAULTHIERARCHYUNIQUENAME", default_hierarchy_unique_name)
        _append_cdata(dim, "DESCRIPTION", "")
        _append_cdata(dim, "TAGS", "")

        datasources = ET.SubElement(dim, "DATASOURCES")
        ds = ET.SubElement(datasources, "DATASOURCE", {"ID": query_id, "TYPE": "REFERENCED"})
        ds.text = ""

        hier = ET.SubElement(dim, "HIERARCHIES")
        used_level_cols: set[str] = set()  # deduplicate level columns within this dimension
        for hierarchy_def in hierarchy_defs:
            hier.append(
                self._build_hierarchy_element(
                    dataset_name=dataset_name,
                    query_id=query_id,
                    hierarchy_def=hierarchy_def,
                    attr_unique_by_name=attr_unique_by_name,
                    col_datatype_by_name={c.name: c.datatype for c in all_cols},
                    used_level_cols=used_level_cols,
                )
            )

        attrs = ET.SubElement(dim, "ATTRS")
        for col in attr_cols:
            attr = ET.SubElement(
                attrs,
                "ATTR",
                {
                    "DATATYPE": col.datatype,
                    "IS_ACCESSIBLE": "true",
                    "ISVISIBLE": "true",
                    "IS_DELETED": "false",
                    "IS_KEYELEMENT": "false",
                    "IS_VISIBLE_CONVERSATIONAL": "true",
                    "DISPLAY_FOLDER": "",
                    "MATERIALIZE": "YES",
                    "PROCESS_TYPE": "DATA_AND_METADATA",
                    "AGGREGATION_TYPE": "BOTH",
                },
            )
            _append_cdata(
                attr,
                "UNIQUENAME",
                attr_unique_by_name.get(
                    col.name, _safe_id("DIMENSION_ATTRIBUTE", f"{dataset_name}:{col.name}", 6)
                ),
            )
            # D2: if this attribute name appears in another dim too, prefix
            # with the dim display name to keep it globally unique.
            dup_set = getattr(self, "_duplicate_attr_names", set())
            if col.name.lower() in dup_set:
                attr_display_name = f"{dataset_name} {col.name}"
            else:
                attr_display_name = col.name
            _append_cdata(attr, "NAME", attr_display_name)
            _append_cdata(attr, "DESCRIPTION", "")
            _append_cdata(attr, "TAGS", "")
            _append_cdata(attr, "TYPE", "Regular")

            df = ET.SubElement(
                attr,
                "DATAFIELD",
                {"QUERY_NAME": dataset_name, "QUERY_ID": query_id, "QUERY_ID_PRE": ""},
            )
            df.append(_cdata(col.name))

            _append_empty_cdata(attr, "DISPLAYFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
            _append_cdata(attr, "GEOROLE", "")
            _append_cdata(attr, "MAPLEVEL", "")
            _append_cdata(attr, "HASTIMEINDATEFORMAT", "false")
            _append_cdata(attr, "DATEDATATYPE", "")
            _append_cdata(attr, "FIELDDATATYPE", "")
            _append_cdata(attr, "SUBDATATYPE", "")
            _append_cdata(attr, "DISPLAYFIELDDATATYPE", "")
            _append_cdata(attr, "DISPLAYFIELDSUBDATATYPE", "NONE")
            _append_cdata(attr, "DATEFORMAT", "")
            _append_cdata(attr, "FORMATTYPE", _format_type_for_datatype(col.datatype))
            mp = ET.SubElement(attr, "MEMBER_PROPERTIES")
            mp.text = ""

        calc = ET.SubElement(dim, "CALC_MEMBERS")
        calc.text = ""

        ET.SubElement(dim, "SCD", {"TYPE": "2"})
        return dim

    def _build_hierarchy_element(
        self,
        *,
        dataset_name: str,
        query_id: str,
        hierarchy_def: dict[str, Any],
        attr_unique_by_name: dict[str, str],
        col_datatype_by_name: dict[str, str],
        used_level_cols: set[str] | None = None,
    ) -> ET.Element:
        is_pc = hierarchy_def.get("is_parent_child", False)
        has_alt_path = hierarchy_def.get("has_alternate_path", False)

        hierarchy = ET.Element(
            "HIERARCHY",
            {
                "HASALL": "false",
                "ISVISIBLE": "true",
                "ISDEFAULT": "false",
                "IS_CUSTOMCALENDAR": "false",
                "HAS_ALTERNATE_PATH": "true" if has_alt_path else "false",
                "HAS_PARENTCHILD_RELATION": "true" if is_pc else "false",
                "PC_LEVEL_COUNT": "-1",
                "IS_ACCESSIBLE": "true",
                "CUBE_VIEW_TYPE": "",
                "MATERIALIZE": "",
                "DISPLAY_FOLDER": "",
            },
        )

        _append_cdata(hierarchy, "UNIQUENAME", hierarchy_def["unique_name"])
        _append_cdata(hierarchy, "NAME", hierarchy_def["name"])
        _append_cdata(hierarchy, "DESCRIPTION", "")
        _append_cdata(hierarchy, "TAGS", "")
        _append_cdata(hierarchy, "DEFAULTMEMBERUNIQUENAME", "")
        _append_cdata(hierarchy, "ALLMEMBERUNIQUENAME", "")
        _append_cdata(hierarchy, "PREDEF_TIME_HIERARCHY", "")
        _append_empty_cdata(hierarchy, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})

        qualify = ET.SubElement(hierarchy, "QUALIFY_MEMBERS", {"TYPE": "ALL_PARENTS"})
        qualify.append(_cdata(""))

        levels = ET.SubElement(hierarchy, "LEVELS")

        all_level = ET.SubElement(
            levels,
            "LEVEL",
            {
                "DATATYPE": "",
                "ISALL": "true",
                "SHOW_VALUES": "0",
                "MAPTYPE": "",
                "IS_ACCESSIBLE": "true",
                "ISVISIBLE": "true",
                "IS_KEYELEMENT": "false",
                "IS_VISIBLE_CONVERSATIONAL": "true",
                "IS_DELETED": "false",
                "HIDE_MEMBER": "0",
                "MATERIALIZE": "",
                "PROCESS_TYPE": "DATA_AND_METADATA",
                "AGGREGATION_TYPE": "BOTH",
            },
        )
        _append_cdata(all_level, "UNIQUENAME", _safe_id("DIMENSION_LEVEL", f'{hierarchy_def["unique_name"]}:ALL', 6))
        _append_cdata(all_level, "NAME", "Hierarchy.ALL")
        _append_cdata(all_level, "DESCRIPTION", "")
        _append_cdata(all_level, "TAGS", "")
        ET.SubElement(all_level, "MEMBER_PROPERTIES")
        _append_cdata(all_level, "FULLY_QUALIFIED_NAME", "")
        _append_cdata(all_level, "DATEDATATYPE", "")
        _append_cdata(all_level, "FIELDDATATYPE", "")
        _append_cdata(all_level, "SUBDATATYPE", "")
        _append_cdata(all_level, "DISPLAYFIELDDATATYPE", "")
        _append_cdata(all_level, "DISPLAYFIELDSUBDATATYPE", "NONE")
        _append_cdata(all_level, "GEOROLE", "")
        _append_cdata(all_level, "MAPLEVEL", "")

        df1 = ET.SubElement(all_level, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})
        df1.append(_cdata(""))

        df2 = ET.SubElement(all_level, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
        df2.append(_cdata(""))

        _append_empty_cdata(all_level, "PARENTFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "DATA_TYPE": ""})
        _append_empty_cdata(all_level, "DISPLAYFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
        _append_cdata(all_level, "HASTIMEINDATEFORMAT", "false")
        _append_cdata(all_level, "DATEFORMAT", "")
        _append_cdata(all_level, "FORMATTYPE", "")
        ET.SubElement(all_level, "PROPERTIES")

        if is_pc:
            child_col = hierarchy_def.get("child_column") or (hierarchy_def["levels"][0] if hierarchy_def["levels"] else "Child")
            parent_col = hierarchy_def.get("parent_column") or "Parent"
            weight_col = hierarchy_def.get("custom_rollup_weight_column")
            pc_naming = hierarchy_def.get("pc_level_naming_pattern") or "Level_*"
            datatype = col_datatype_by_name.get(child_col, "CHAR")
            child_unique = attr_unique_by_name.get(
                child_col, _safe_id("DIMENSION_LEVEL", f"{dataset_name}:{child_col}", 6)
            )

            lvl = ET.SubElement(
                levels,
                "LEVEL",
                {
                    "DATATYPE": datatype,
                    "ISALL": "false",
                    "SHOW_VALUES": "0",
                    "MAPTYPE": "",
                    "IS_ACCESSIBLE": "true",
                    "ISVISIBLE": "true",
                    "IS_KEYELEMENT": "false",
                    "IS_VISIBLE_CONVERSATIONAL": "true",
                    "IS_DELETED": "false",
                    "HIDE_MEMBER": "0",
                    "MATERIALIZE": "",
                    "PROCESS_TYPE": "DATA_AND_METADATA",
                    "AGGREGATION_TYPE": "BOTH",
                },
            )
            _append_cdata(lvl, "UNIQUENAME", child_unique)
            _append_cdata(lvl, "NAME", child_col)
            _append_cdata(lvl, "DESCRIPTION", "")
            _append_cdata(lvl, "TAGS", "")
            ET.SubElement(lvl, "MEMBER_PROPERTIES")
            _append_cdata(lvl, "FULLY_QUALIFIED_NAME", "")
            _append_cdata(lvl, "DATEDATATYPE", "")
            _append_cdata(lvl, "FIELDDATATYPE", "")
            _append_cdata(lvl, "SUBDATATYPE", "")
            _append_cdata(lvl, "DISPLAYFIELDDATATYPE", "")
            _append_cdata(lvl, "DISPLAYFIELDSUBDATATYPE", "NONE")
            _append_cdata(lvl, "GEOROLE", "")
            _append_cdata(lvl, "MAPLEVEL", "")

            df1 = ET.SubElement(lvl, "DATAFIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id})
            df1.append(_cdata(child_col))

            df2 = ET.SubElement(lvl, "DATAFIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id, "QUERY_ID_PRE": ""})
            df2.append(_cdata(child_col))

            parent_field = ET.SubElement(
                lvl,
                "PARENTFIELD",
                {"QUERY_NAME": dataset_name, "QUERY_ID": query_id, "DATA_TYPE": datatype},
            )
            parent_field.append(_cdata(parent_col))

            _append_empty_cdata(lvl, "DISPLAYFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
            _append_cdata(lvl, "HASTIMEINDATEFORMAT", "false")
            _append_cdata(lvl, "DATEFORMAT", "")
            _append_cdata(lvl, "FORMATTYPE", _format_type_for_datatype(datatype))
            ET.SubElement(lvl, "PROPERTIES")

            if weight_col:
                cr = ET.SubElement(lvl, "CUSTOM_ROLLUP")
                weight_el = ET.SubElement(cr, "WEIGHT", {"TYPE": "field"})
                field_el = ET.SubElement(weight_el, "FIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id})
                field_el.append(_cdata(weight_col))

            # ROOT_MEMBER_IF must be explicit (not empty/Auto) to avoid the
            # 'RootMemberIf is set to Auto/ParentIsBlank ... advanced property
            # kyvos.build.blankvalue.badrow is enabled' validation error.
            # PARENT_IS_SELF is the standard SSAS semantic for root members.
            pc_settings = ET.SubElement(lvl, "PARENT_CHILD_SETTINGS", {"ROOT_MEMBER_IF": "PARENT_IS_SELF"})
            members_with_data = ET.SubElement(pc_settings, "MEMBERS_WITH_DATA", {"VISIBLE": "true"})
            members_with_data.append(_cdata(""))
            levels_naming = ET.SubElement(pc_settings, "LEVELS_NAMING")
            lvl_name_el = ET.SubElement(levels_naming, "LEVEL")
            lvl_name_el.append(_cdata(pc_naming))

        else:
            for col_name in hierarchy_def["levels"]:
                if used_level_cols is not None:
                    if col_name in used_level_cols:
                        continue  # already used as a level in an earlier hierarchy – skip to avoid Kyvos duplicate-name error
                    used_level_cols.add(col_name)
                datatype = col_datatype_by_name.get(col_name, "CHAR")
                lvl = ET.SubElement(
                    levels,
                    "LEVEL",
                    {
                        "DATATYPE": datatype,
                        "ISALL": "false",
                        "SHOW_VALUES": "0",
                        "MAPTYPE": "",
                        "IS_ACCESSIBLE": "true",
                        "ISVISIBLE": "true",
                        "IS_KEYELEMENT": "false",
                        "IS_VISIBLE_CONVERSATIONAL": "true",
                        "IS_DELETED": "false",
                        "HIDE_MEMBER": "0",
                        "MATERIALIZE": "YES",
                        "PROCESS_TYPE": "DATA_AND_METADATA",
                        "AGGREGATION_TYPE": "BOTH",
                    },
                )
                _append_cdata(
                    lvl,
                    "UNIQUENAME",
                    attr_unique_by_name.get(col_name, _safe_id("DIMENSION_LEVEL", f"{dataset_name}:{col_name}", 6)),
                )
                _append_cdata(lvl, "NAME", col_name)
                _append_cdata(lvl, "DESCRIPTION", "")
                _append_cdata(lvl, "TAGS", "")
                ET.SubElement(lvl, "MEMBER_PROPERTIES")
                _append_cdata(lvl, "FULLY_QUALIFIED_NAME", "")
                _append_cdata(lvl, "DATEDATATYPE", "")
                _append_cdata(lvl, "FIELDDATATYPE", "")
                _append_cdata(lvl, "SUBDATATYPE", "")
                _append_cdata(lvl, "DISPLAYFIELDDATATYPE", "")
                _append_cdata(lvl, "DISPLAYFIELDSUBDATATYPE", "NONE")
                _append_cdata(lvl, "GEOROLE", "")
                _append_cdata(lvl, "MAPLEVEL", "")

                df1 = ET.SubElement(lvl, "DATAFIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id})
                df1.append(_cdata(col_name))

                df2 = ET.SubElement(lvl, "DATAFIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id, "QUERY_ID_PRE": ""})
                df2.append(_cdata(col_name))

                _append_empty_cdata(lvl, "PARENTFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "DATA_TYPE": ""})
                _append_empty_cdata(lvl, "DISPLAYFIELD", {"QUERY_NAME": "", "QUERY_ID": "", "QUERY_ID_PRE": ""})
                _append_cdata(lvl, "HASTIMEINDATEFORMAT", "false")
                _append_cdata(lvl, "DATEFORMAT", "")
                _append_cdata(lvl, "FORMATTYPE", _format_type_for_datatype(datatype))
                ET.SubElement(lvl, "PROPERTIES")

        return hierarchy

    def _hierarchy_definitions(self, dataset_name: str, available_columns: set[str]) -> list[dict[str, Any]]:
        hierarchy_defs: list[dict[str, Any]] = []

        for spec in self.hierarchy_specs:
            source_dataset = getattr(spec, "source_dataset", None)
            hierarchy_name = getattr(spec, "name", None)
            levels = list(getattr(spec, "levels", []) or [])
            is_pc = getattr(spec, "is_parent_child", False)
            has_alt = getattr(spec, "has_alternate_path", False)
            child_col_defined = is_pc and bool(getattr(spec, "child_column", None))

            if not source_dataset or not hierarchy_name:
                continue
            if not levels and not child_col_defined:
                continue

            if not self._dataset_name_matches(dataset_name, source_dataset):
                continue

            actual_levels = [lvl for lvl in levels if lvl in available_columns]

            if not is_pc and len(actual_levels) < 2:
                continue

            if is_pc and not child_col_defined:
                continue

            hierarchy_defs.append(
                {
                    "name": hierarchy_name,
                    "unique_name": _safe_id("H", f"{dataset_name}:{hierarchy_name}", 6),
                    "levels": actual_levels,
                    "is_parent_child": is_pc,
                    "has_alternate_path": has_alt,
                    "parent_column": getattr(spec, "parent_column", None),
                    "child_column": getattr(spec, "child_column", None),
                    "custom_rollup_weight_column": getattr(spec, "custom_rollup_weight_column", None),
                    "pc_level_naming_pattern": getattr(spec, "pc_level_naming_pattern", "Level_*"),
                }
            )

        # PCH exclusivity: if ANY hierarchy for this dataset is parent-child,
        # keep only PCH hierarchies – normal/ragged hierarchies are not allowed
        # on the same dataset as a PCH in Kyvos.
        if any(h["is_parent_child"] for h in hierarchy_defs):
            dropped = [h["name"] for h in hierarchy_defs if not h["is_parent_child"]]
            if dropped:
                self.logger.warning(
                    "pch_exclusivity_dropping_non_pc_hierarchies",
                    dropped=dropped,
                )
            hierarchy_defs = [h for h in hierarchy_defs if h["is_parent_child"]]

        return hierarchy_defs

    def _dataset_name_matches(self, dataset_name_from_drd: str, dataset_name_from_spec: str) -> bool:
        left = _normalize_name(dataset_name_from_drd)
        right = _normalize_name(dataset_name_from_spec)

        def strip_known_prefixes(value: str) -> str:
            prefixes = ("fact", "dim", "bridge", "snowflake", "dataset")
            max_iterations = 10  # Prevent infinite loops
            iterations = 0
            
            while iterations < max_iterations and value:
                original_value = value
                for prefix in prefixes:
                    if value.startswith(prefix):
                        value = value[len(prefix):]
                        break  # Only strip one prefix per iteration
                
                # If no change was made, we're done
                if value == original_value:
                    break
                iterations += 1
            
            return value

        left_stripped = strip_known_prefixes(left)
        right_stripped = strip_known_prefixes(right)

        return (
            left == right
            or left_stripped == right_stripped
        )

    def _dimension_join_keys(self, dim_node_id: str, relations: list[DrdRelation]) -> set[str]:
        keys: set[str] = set()
        for rel in relations:
            if rel.node1_id == dim_node_id and rel.node1_key:
                keys.add(rel.node1_key)
            if rel.node2_id == dim_node_id and rel.node2_key:
                keys.add(rel.node2_key)
        return keys

    # -----------------------------------------------------
    # measure groups + measures
    # -----------------------------------------------------

    def _build_measure_groups_and_measures(self, cube: ET.Element, drd_meta: dict[str, Any]) -> None:
        fact_contexts: dict[str, FactContext] = drd_meta["fact_contexts"]
        if not fact_contexts:
            raise ValueError("No fact contexts available to build measure groups")

        measure_groups = ET.SubElement(cube, "MEASURE_GROUPS")
        mg_datasources = ET.SubElement(measure_groups, "DATASOURCES")

        ordered_fact_contexts = sorted(
            fact_contexts.values(),
            key=lambda ctx: ctx.fact_node.dataset_name.lower(),
        )

        measures: list[ET.Element] = []
        default_measure_assigned = False
        _measure_ref_re = re.compile(r"\[Measures\]\.\[([^\]]+)\]")
        _used_names: set[str] = set()  # global dedup across all fact measure groups

        for ctx in ordered_fact_contexts:
            fact_node = ctx.fact_node
            fact_name = fact_node.dataset_name
            query_id = fact_node.node_id

            # D1: if this fact has no dimension connections, its measure group
            # will trigger Kyvos 'dataset is not used to create any other
            # semantic model entity' errors (particularly for distinct-count
            # measures).  Skip the whole MG and record its measures.
            if not ctx.connected_dimension_node_ids:
                orphan_base = self._base_measures_for_dataset(fact_name)
                orphan_calc = self._calculated_measures_for_dataset(fact_name)
                self.logger.warning(
                    "orphan_fact_skipped",
                    fact=fact_name,
                    base=len(orphan_base),
                    calc=len(orphan_calc),
                    reason="no_dim_connection",
                )
                for _m in list(orphan_base) + list(orphan_calc):
                    self.skipped_items.append({
                        "kind": "calculated_measure" if getattr(_m, "is_calculated", False) else "base_measure",
                        "name": getattr(_m, "name", ""),
                        "source_dataset": fact_name,
                        "reason": "orphan_fact_no_dim_connection",
                        "detail": (
                            f"Fact '{fact_name}' has no dimension relationships in the DRD; "
                            "its measure group was skipped to avoid Kyvos validation errors."
                        ),
                    })
                continue

            ET.SubElement(
                mg_datasources,
                "DATASOURCE",
                {"ID": query_id, "NAME": fact_name},
            )

            fact_cols = self._cols_for(fact_name)
            fact_col_by_name = {c.name.lower(): c for c in fact_cols}
            calculated_measures = self._calculated_measures_for_dataset(fact_name)
            spec_base_measures = self._base_measures_for_dataset(fact_name)

            # Build (col, aggregation_type, measure_name, format_string) tuples for base measures.
            # Prefer MeasureSpec-defined base measures (which carry aggregation_type).
            # Fall back to auto-discovery from numeric columns if no specs are defined.
            base_measure_entries: list[tuple[ColumnInfo, str, str | None, str | None]] = []
            covered_col_names: set[str] = set()

            if spec_base_measures:
                for spec in spec_base_measures:
                    col_name = (getattr(spec, "source_column", None) or "").strip()
                    col = fact_col_by_name.get(col_name.lower()) if col_name else None
                    if col is None and col_name:
                        # LLMs sometimes prefix source_column with aggregation words
                        # (e.g. "avg_claim_amount" instead of "claim_amount").
                        # Try stripping common prefixes/suffixes before giving up.
                        for prefix in ("total_", "avg_", "average_", "min_", "max_", "sum_", "unique_", "distinct_"):
                            candidate = col_name.lower().removeprefix(prefix)
                            col = fact_col_by_name.get(candidate)
                            if col:
                                break
                    if col is None:
                        self.logger.warning(
                            "base_measure_source_column_not_found",
                            measure=spec.name,
                            source_column=col_name or "(empty)",
                            dataset=fact_name,
                        )
                        continue
                    agg = (getattr(spec, "aggregation_type", None) or "sum").strip().lower()
                    fmt = getattr(spec, "format_string", None) or None
                    base_measure_entries.append((col, agg, spec.name, fmt))
                    covered_col_names.add(col.name.lower())

            # Auto-discover numeric columns when no valid spec measures were available.
            # This covers both the case where no MeasureSpecs exist for this dataset
            # and the case where all spec measures were skipped (e.g., empty source_column).
            # Also runs when the only base measures are distinct_count — Kyvos requires
            # at least one non-distinct-count measure from the same dataset.
            _has_non_distinct_base = any(
                agg != "distinct_count" for _, agg, _, _ in base_measure_entries
            )
            if not base_measure_entries or not _has_non_distinct_base:
                for c in fact_cols:
                    if (
                        c.datatype == "NUMBER"
                        and c.name not in ctx.join_keys
                        and not c.name.lower().endswith("_key")
                        and not c.name.lower().endswith("_pk")
                        and not c.name.lower().endswith("_fk")
                        and c.name.lower() not in covered_col_names
                    ):
                        base_measure_entries.append((c, "sum", None, None))
                        covered_col_names.add(c.name.lower())

            # Last resort: fact has no numeric measure columns at all.
            # Every fact MUST have at least one measure group + measure.
            # Also triggers when only distinct_count measures exist and no
            # numeric columns were found for auto-discovery — Kyvos requires
            # a non-distinct-count measure from the same dataset.
            _still_only_distinct = bool(base_measure_entries) and all(
                agg == "distinct_count" for _, agg, _, _ in base_measure_entries
            )
            if (not base_measure_entries or _still_only_distinct) and fact_cols:
                count_col = fact_cols[0]
                count_name = f"{fact_name}_row_count"
                base_measure_entries.append((count_col, "count", count_name, "#,##0"))
                covered_col_names.add(count_col.name.lower())
                self.logger.warning(
                    "no_numeric_measures_adding_row_count",
                    fact=fact_name,
                    count_measure=count_name,
                )

            # Ensure columns referenced in calculated expressions are present as base
            # measures even if they were filtered out (e.g. _fk / _pk columns referenced
            # directly in expressions like DISTINCT([Measures].[sales_pk])).
            for cm in calculated_measures:
                expr = getattr(cm, "expression", "") or ""
                for ref_name in _measure_ref_re.findall(expr):
                    ref_lower = ref_name.lower()
                    if ref_lower not in covered_col_names and ref_lower in fact_col_by_name:
                        base_measure_entries.append((fact_col_by_name[ref_lower], "sum", None, None))
                        covered_col_names.add(ref_lower)

            mg = ET.SubElement(
                measure_groups,
                "MEASURE_GROUP",
                {
                    "NAME": fact_name,
                    "IS_ACCESSIBLE": "true",
                    "ISVISIBLE": "true",
                    "MATERIALIZE": "",
                },
            )
            _append_cdata(mg, "DESCRIPTION", "")

            base_measure_defs: list[tuple[str, ColumnInfo, str, str | None, str | None]] = []
            for idx, (col, agg, mname, mfmt) in enumerate(base_measure_entries):
                raw_name = mname or col.name
                unique_name = _dedupe_measure_name(raw_name, _used_names)
                mid = _safe_id("MEASURE", f"{fact_name}:{unique_name}:{idx}", 6)
                base_measure_defs.append((mid, col, agg, unique_name, mfmt))
                _append_cdata(mg, "MEASURE_ID", mid)

            calc_measure_defs: list[tuple[str, Any, str]] = []
            for idx, measure in enumerate(calculated_measures):
                unique_cname = _dedupe_measure_name(measure.name, _used_names)
                mid = _safe_id("MEASURE", f"{fact_name}:CALC:{unique_cname}:{idx}", 6)
                calc_measure_defs.append((mid, measure, unique_cname))
                _append_cdata(mg, "MEASURE_ID", mid)

            for idx, (mid, col, agg, mname, mfmt) in enumerate(base_measure_defs):
                if not default_measure_assigned and idx == 0:
                    is_default = "true"
                    default_measure_assigned = True
                else:
                    is_default = "false"

                measures.append(
                    self._build_measure(
                        measure_id=mid,
                        dataset_name=fact_name,
                        query_id=query_id,
                        col=col,
                        is_default=is_default,
                        aggregation_type=agg,
                        measure_name=mname,
                        format_string=mfmt,
                    )
                )

            for idx, (mid, measure, unique_cname) in enumerate(calc_measure_defs):
                if not default_measure_assigned and idx == 0 and not base_measure_defs:
                    is_default = "true"
                    default_measure_assigned = True
                else:
                    is_default = "false"

                measures.append(
                    self._build_calculated_measure(
                        measure_id=mid,
                        measure=measure,
                        is_default=is_default,
                        measure_name_override=unique_cname,
                        fact_query_id=query_id,
                        fact_dataset_name=fact_name,
                    )
                )

        # ------------------------------------------------------------------
        # Companion measure groups for datasets referenced by DistinctCount()
        # in calculated measures but not already having a fact context.
        # Kyvos requires that a dataset referenced by a distinct-count measure
        # must also have at least one other SM entity (measure or dimension).
        # ------------------------------------------------------------------
        existing_mg_names: set[str] = {
            ctx.fact_node.dataset_name.lower()
            for ctx in fact_contexts.values()
            if ctx.connected_dimension_node_ids
        }
        nodes_by_name: dict[str, DrdNode] = {
            n.dataset_name.lower(): n for n in drd_meta["nodes"].values()
        }

        orphan_ds_names: set[str] = set()
        distinct_count_measures_found: list[str] = []
        for m in self.semantic_measures:
            # Case 1: calculated measures with DistinctCount([Dataset]) expression
            expr = getattr(m, "expression", "") or ""
            for ref in re.findall(r"DistinctCount\s*\(\s*\[([^\]]+)\]", expr, re.IGNORECASE):
                ref_lower = ref.strip().lower()
                if ref_lower not in existing_mg_names and (ref_lower in nodes_by_name or ref_lower in self.dataset_name_to_id):
                    orphan_ds_names.add(ref_lower)
            # Case 2: base measures with aggregation_type=distinct_count
            agg = (getattr(m, "aggregation_type", "") or "").strip().lower()
            if agg == "distinct_count":
                src_ds = (getattr(m, "source_dataset", "") or "").strip().lower()
                distinct_count_measures_found.append(f"{m.name}(src={src_ds})")
                if src_ds and src_ds not in existing_mg_names and (src_ds in nodes_by_name or src_ds in self.dataset_name_to_id):
                    orphan_ds_names.add(src_ds)

        self.logger.info(
            "companion_orphan_scan",
            distinct_count_measures=len(distinct_count_measures_found),
            found=distinct_count_measures_found[:10],
            orphan_ds=sorted(orphan_ds_names),
            existing_mgs=sorted(existing_mg_names),
        )

        # Build a lookup that includes both DRD nodes and dataset_name_to_id
        # so we can resolve orphan datasets that exist in Kyvos but not in the DRD.
        ds_id_ci: dict[str, str] = {k.lower(): v for k, v in self.dataset_name_to_id.items()}

        for ds_lower in sorted(orphan_ds_names):
            node = nodes_by_name.get(ds_lower)
            if node:
                ds_name = node.dataset_name
                node_id = node.node_id
            else:
                # Dataset not in DRD — construct a synthetic node reference
                ds_name = next(
                    (k for k in self.dataset_name_to_id if k.lower() == ds_lower),
                    ds_lower,
                )
                node_id = ds_id_ci.get(ds_lower, ds_lower)

            self.logger.info(
                "companion_measure_group_added",
                dataset=ds_name,
                node_id=node_id,
                reason="distinct_count_orphan",
            )

            ET.SubElement(
                mg_datasources,
                "DATASOURCE",
                {"ID": node_id, "NAME": ds_name},
            )

            companion_cols = self._cols_for(ds_name)
            if not companion_cols:
                self.logger.warning(
                    "companion_mg_no_columns",
                    dataset=ds_name,
                )
                continue

            col_by_name = {c.name.lower(): c for c in companion_cols}

            # Collect distinct-count base measures for this dataset
            ds_distinct_measures: list[Any] = []
            for m in self.semantic_measures:
                agg = (getattr(m, "aggregation_type", "") or "").strip().lower()
                src_ds = (getattr(m, "source_dataset", "") or "").strip().lower()
                if agg == "distinct_count" and src_ds == ds_lower:
                    ds_distinct_measures.append(m)

            mg = ET.SubElement(
                measure_groups,
                "MEASURE_GROUP",
                {
                    "NAME": ds_name,
                    "IS_ACCESSIBLE": "true",
                    "ISVISIBLE": "true",
                    "MATERIALIZE": "",
                },
            )
            _append_cdata(mg, "DESCRIPTION", f"Companion measure group for {ds_name}")

            measure_idx = 0

            # Add distinct-count base measures first
            for dm in ds_distinct_measures:
                col_name = (getattr(dm, "source_column", "") or "").strip()
                col = col_by_name.get(col_name.lower()) if col_name else None
                if col is None:
                    # Fallback: use first column as anchor
                    col = companion_cols[0]
                mid = _safe_id("MEASURE", f"{ds_name}:DIST:{measure_idx}", 6)
                _append_cdata(mg, "MEASURE_ID", mid)
                measures.append(
                    self._build_measure(
                        measure_id=mid,
                        dataset_name=ds_name,
                        query_id=node_id,
                        col=col,
                        is_default="false",
                        aggregation_type="distinct_count",
                        measure_name=dm.name,
                        format_string=getattr(dm, "format_string", "#,##0") or "#,##0",
                    )
                )
                measure_idx += 1

            # Add a row-count companion measure so the dataset has a non-distinct-count entity
            anchor_col = companion_cols[0]
            companion_measure_name = f"{ds_name} Record Count"
            companion_mid = _safe_id("MEASURE", f"{ds_name}:COMPANION:{measure_idx}", 6)
            _append_cdata(mg, "MEASURE_ID", companion_mid)
            measures.append(
                self._build_measure(
                    measure_id=companion_mid,
                    dataset_name=ds_name,
                    query_id=node_id,
                    col=anchor_col,
                    is_default="false",
                    aggregation_type="count",
                    measure_name=companion_measure_name,
                    format_string="#,##0",
                )
            )

        measures_el = ET.SubElement(cube, "MEASURES")
        for m in measures:
            measures_el.append(m)

    def _build_measure(
        self,
        *,
        measure_id: str,
        dataset_name: str,
        query_id: str,
        col: ColumnInfo,
        is_default: str,
        aggregation_type: str = "sum",
        measure_name: str | None = None,
        format_string: str | None = None,
    ) -> ET.Element:
        if format_string:
            fmt = format_string
            fmt_type = _format_type_for_measure_format(format_string)
        else:
            fmt, fmt_type = _measure_format_for_column(col.name)
        display_name = measure_name or col.name
        agg_lower = aggregation_type.lower()
        if agg_lower not in _AGGREGATION_TYPE_TO_SUMMARYFUNCTION:
            self.logger.warning(
                "unknown_aggregation_type_defaulting_to_sum",
                aggregation_type=aggregation_type,
                measure=measure_name or col.name,
            )
        summary_code = _AGGREGATION_TYPE_TO_SUMMARYFUNCTION.get(agg_lower, "0")
        # For count/distinct_count use the mapped column type; all others are NUMBER.
        datatype = _datatype_map(col.datatype) if agg_lower in ("count", "distinct_count") else "NUMBER"

        m = ET.Element(
            "MEASURE",
            {
                "ID": measure_id,
                "TYPE": "STANDARD",
                "DATATYPE": datatype,
                "SUBTYPE": "",
                "ISVISIBLE": "true",
                "ISDEFAULT": is_default,
                "RESTRICT": "false",
                "SOLVE_ORDER": "0",
                "ISADJUSTMENT": "false",
                "ADJUSTMENT_TYPE": "NEGATIVE",
                "DISPLAY_FOLDER": "",
                "IS_KEYELEMENT": "false",
                "IS_VISIBLE_CONVERSATIONAL": "true",
                "COLOR": "",
                "IS_ACCESSIBLE": "true",
                "MATERIALIZE": "",
            },
        )

        _append_cdata(m, "UNIQUENAME", measure_id)
        _append_cdata(m, "NAME", display_name)
        _append_cdata(m, "DESCRIPTION", "")
        _append_cdata(m, "TAGS", "")

        format_el = ET.SubElement(m, "FORMAT", {"USEDEFAULT": "0"})
        format_el.append(_cdata(fmt))

        _append_cdata(m, "FORMATTYPE", fmt_type)
        ET.SubElement(m, "UNIT")
        _append_cdata(m, "SUMMARYFUNCTION", summary_code)
        _append_cdata(m, "ACTUALSUMMARYFUNCTION", summary_code)

        df = ET.SubElement(m, "DATAFIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id})
        df.append(_cdata(col.name))

        minmax = ET.SubElement(m, "MINON_MAXON_DATAFIELD", {"QUERY_NAME": dataset_name, "QUERY_ID": query_id})
        minmax.append(_cdata(""))

        expr = ET.SubElement(m, "EXPRESSION", {"NON_EMPTY_BEHAVIOR": "BY_MEASURE"})
        expr.append(_cdata(""))

        _append_cdata(m, "NON_EMPTY_MEASURES", "")
        _append_cdata(m, "IS_BOUNDARY_DIST_COUNT", "false")
        # For distinct-count measures Kyvos requires 'Count On' to be set to
        # the fact dataset that owns the source column.  Without this the
        # validator emits: "Could not identify 'Count On' fact dataset
        # automatically for Distinct count measure [...]".
        if agg_lower == "distinct_count":
            _append_cdata(m, "DISTINCT_COUNT_TYPE", "EXACT")
            _append_cdata(m, "DIST_COUNT_DATASET_NODE_ID", query_id)
            _append_cdata(m, "DIST_COUNT_DATASET_NODE_NAME", dataset_name)
        _append_cdata(m, "DICTIONARY_TYPE", "INDEX")
        return m

    def _build_calculated_measure(
        self,
        *,
        measure_id: str,
        measure: "MeasureSpec",
        is_default: str,
        measure_name_override: str | None = None,
        fact_query_id: str = "",
        fact_dataset_name: str = "",
    ) -> ET.Element:
        measure_name = measure_name_override or measure.name or ""
        format_string = measure.format_string or "#,##0"
        description = measure.description or ""
        expression = measure.expression or ""
        fmt_type = _format_type_for_measure_format(format_string)

        m = ET.Element(
            "MEASURE",
            {
                "ID": measure_id,
                "TYPE": "STANDARD",
                "DATATYPE": "NUMBER",
                "SUBTYPE": "",
                "ISVISIBLE": "true",
                "ISDEFAULT": is_default,
                "RESTRICT": "false",
                "SOLVE_ORDER": "0",
                "ISADJUSTMENT": "false",
                "ADJUSTMENT_TYPE": "NEGATIVE",
                "DISPLAY_FOLDER": "",
                "IS_KEYELEMENT": "false",
                "IS_VISIBLE_CONVERSATIONAL": "true",
                "COLOR": "",
                "IS_ACCESSIBLE": "true",
                "MATERIALIZE": "",
            },
        )

        _append_cdata(m, "UNIQUENAME", measure_id)
        _append_cdata(m, "NAME", measure_name)
        _append_cdata(m, "DESCRIPTION", description)
        _append_cdata(m, "TAGS", "")

        format_el = ET.SubElement(m, "FORMAT", {"USEDEFAULT": "0"})
        format_el.append(_cdata(format_string))

        _append_cdata(m, "FORMATTYPE", fmt_type)
        ET.SubElement(m, "UNIT")
        _append_cdata(m, "SUMMARYFUNCTION", "")
        _append_cdata(m, "ACTUALSUMMARYFUNCTION", "")

        _append_empty_cdata(m, "DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})

        minmax = ET.SubElement(m, "MINON_MAXON_DATAFIELD", {"QUERY_NAME": "", "QUERY_ID": ""})
        minmax.append(_cdata(""))

        expr = ET.SubElement(m, "EXPRESSION", {"NON_EMPTY_BEHAVIOR": "BY_MEASURE"})
        expr.append(_cdata(expression))

        _append_cdata(m, "NON_EMPTY_MEASURES", "")
        _append_cdata(m, "IS_BOUNDARY_DIST_COUNT", "false")
        # If the MDX expression uses DistinctCount(...), we must set Count On
        # so Kyvos knows which dataset participates in the distinct count.
        if re.search(r"\bDistinctCount\s*\(", expression, re.IGNORECASE):
            count_on_ds_name = fact_dataset_name
            count_on_node_id = fact_query_id
            # Prefer the dataset explicitly referenced inside DistinctCount(...)
            m_inner = re.search(
                r"DistinctCount\s*\(\s*\[([^\]]+)\]",
                expression,
                re.IGNORECASE,
            )
            if m_inner:
                ref_ds = m_inner.group(1).strip()
                # Resolve ref_ds -> DRD node id via the dataset_name_to_id map
                # (case-insensitive, stripped of whitespace).  Fall back to
                # fact context when we cannot resolve.
                resolved_id = self._resolve_node_id_for_dataset(ref_ds)
                if resolved_id:
                    count_on_ds_name = ref_ds
                    count_on_node_id = resolved_id
            _append_cdata(m, "DISTINCT_COUNT_TYPE", "EXACT")
            _append_cdata(m, "DIST_COUNT_DATASET_NODE_ID", count_on_node_id)
            _append_cdata(m, "DIST_COUNT_DATASET_NODE_NAME", count_on_ds_name)
        _append_cdata(m, "DICTIONARY_TYPE", "INDEX")
        return m

    def _resolve_node_id_for_dataset(self, dataset_name: str) -> str:
        """Return the DRD node ID for a dataset name (case-insensitive), or ''.

        Used by distinct-count measures to populate DIST_COUNT_DATASET_NODE_ID
        from a [Dataset] reference inside an MDX expression.
        """
        if not dataset_name:
            return ""
        target = dataset_name.strip().lower()
        try:
            root = ET.fromstring(self.drd_xml)
        except Exception:
            return ""
        response_iro = root.find(".//IRO")
        search_root = response_iro if response_iro is not None else root
        for node_el in search_root.findall(".//NODES/NODE"):
            alias_el = node_el.find("./REL_DATASET/ALIAS_NAME")
            node_name = (alias_el.text or "").strip() if alias_el is not None else ""
            if node_name.lower() == target:
                return (node_el.get("ID") or "").strip()
        return ""

    def _calculated_measures_for_dataset(self, dataset_name: str) -> list[Any]:
        matched: list[Any] = []
        for m in self.semantic_measures:
            if not getattr(m, "is_calculated", False):
                continue
            if not getattr(m, "expression", None):
                continue
            if self._dataset_name_matches(
                dataset_name,
                getattr(m, "source_dataset", ""),
            ):
                matched.append(m)
        return matched

    def _base_measures_for_dataset(self, dataset_name: str) -> list[Any]:
        matched: list[Any] = []
        for m in self.semantic_measures:
            if getattr(m, "is_calculated", False):
                continue
            if self._dataset_name_matches(
                dataset_name,
                getattr(m, "source_dataset", ""),
            ):
                matched.append(m)
        return matched

    # -----------------------------------------------------
    # metadata helpers
    # -----------------------------------------------------

    def _cols_for(self, dataset_name: str) -> list[ColumnInfo]:
        raw = self.dataset_columns.get(dataset_name, [])
        out: list[ColumnInfo] = []
        for c in raw:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            out.append(
                ColumnInfo(
                    name=name,
                    datatype=_datatype_map(c.get("datatype", "")),
                )
            )
        return out
