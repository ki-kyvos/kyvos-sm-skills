"""Generate Kyvos-compatible Simplified JSON for semantic model creation (Kyvos 2026.5+).

Emits a JSON dict suitable for ``POST /rest/v2/semantic-models`` with
``Content-Type: application/x-www-form-urlencoded`` and the JSON payload
sent as a form-encoded ``json`` parameter.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import logging

logger = logging.getLogger(__name__)


_AGGREGATION_TYPE_TO_SUMMARYFUNCTION: dict[str, str] = {
    "sum": "0",
    "average": "1",
    "count": "2",
    "minimum": "3",
    "maximum": "4",
    "first_child": "8",
    "last_child": "9",
    "first_non_empty_child": "10",
    "distinct_count": "11",
}


def _safe_id(prefix: str, seed: str, length: int = 6) -> str:
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % (10 ** length)
    return f"{prefix}_{h:0{length}d}"


def _data_field(query_name: str, content: str, query_id: str) -> dict[str, str]:
    """Build a dataField object as used in the simplified JSON."""
    return {"queryName": query_name, "content": content, "queryId": query_id}


def _format_obj(fmt: str = "#,##0.00") -> dict[str, str]:
    """Build a format object as used in the simplified JSON."""
    return {"useDefault": "0", "content": fmt}


class SModelJsonGenerator:
    """Generate Kyvos semantic model Simplified JSON definitions."""

    def __init__(
        self,
        folder_id: str,
        folder_name: str,
        smodel_name: str,
        connection_name: str,
        drd_id: str,
        drd_name: str,
        drd_xml: str = "",
        dataset_name_to_id: dict[str, str] | None = None,
        dataset_columns: dict[str, list[dict]] | None = None,
        hierarchy_specs: list | None = None,
        semantic_measures: list | None = None,
        skipped_items: list | None = None,
        display_to_kyvos: dict[str, str] | None = None,
        fact_dataset_names: set[str] | None = None,
        connected_dim_names: set[str] | None = None,
        join_keys_by_dataset: dict[str, set[str]] | None = None,
    ) -> None:
        self.folder_id = folder_id
        self.folder_name = folder_name
        self.smodel_name = smodel_name
        self.connection_name = connection_name
        self.drd_id = drd_id
        self.drd_name = drd_name
        self.drd_xml = drd_xml
        self.dataset_name_to_id = dataset_name_to_id or {}
        self.dataset_columns = dataset_columns or {}
        self.hierarchy_specs = hierarchy_specs or []
        self.semantic_measures = semantic_measures or []
        self.skipped_items = skipped_items or []
        self.display_to_kyvos = display_to_kyvos or {}
        self.fact_dataset_names = fact_dataset_names or set()
        self.connected_dim_names = connected_dim_names or set()
        self.join_keys_by_dataset = join_keys_by_dataset or {}
        self._display_to_kyvos_ci: dict[str, str] = {
            k.lower(): v for k, v in self.display_to_kyvos.items()
        }
        self._name_to_id_ci: dict[str, str] = {
            k.lower(): v for k, v in self.dataset_name_to_id.items()
        }
        self.smodel_id: str = ""

    def _cols_for(self, ds_name: str) -> list[dict]:
        """Get columns for a dataset, case-insensitive lookup."""
        cols = self.dataset_columns.get(ds_name)
        if cols:
            return cols
        for k, v in self.dataset_columns.items():
            if k.lower() == ds_name.lower():
                return v
        return []

    def _resolve_kyvos_name(self, ds_name: str) -> str:
        """Translate a spec/display dataset name to the Kyvos dataset name.

        Tries exact match in display_to_kyvos, then case-insensitive,
        then tries stripping fact_/dim_ prefixes, then assumes the name
        is already a Kyvos name.
        """
        if ds_name in self.display_to_kyvos:
            return self.display_to_kyvos[ds_name]
        ci = self._display_to_kyvos_ci.get(ds_name.lower())
        if ci:
            return ci
        # Fuzzy: strip fact_/dim_ prefix and retry
        stripped = self._strip_table_prefix(ds_name)
        if stripped != ds_name:
            if stripped in self.display_to_kyvos:
                return self.display_to_kyvos[stripped]
            ci = self._display_to_kyvos_ci.get(stripped.lower())
            if ci:
                return ci
        # Fuzzy: try substring matching against display_to_kyvos keys
        ds_lower = ds_name.lower()
        stripped_lower = stripped.lower() if stripped != ds_name else ds_lower
        for key, val in self._display_to_kyvos_ci.items():
            if stripped_lower and stripped_lower in key:
                return val
            if ds_lower and len(ds_lower) >= 4 and ds_lower in key:
                return val
        return ds_name

    def _resolve_dataset_id(self, ds_name: str) -> str:
        """Resolve a dataset name (display or Kyvos) to its Kyvos ID.

        Tries exact match in dataset_name_to_id, then case-insensitive,
        then translates via display_to_kyvos and retries, then tries
        stripping fact_/dim_ prefixes for fuzzy matching.
        """
        ds_id = self.dataset_name_to_id.get(ds_name, "")
        if ds_id:
            return ds_id
        ds_id = self._name_to_id_ci.get(ds_name.lower(), "")
        if ds_id:
            return ds_id
        kyvos_name = self._resolve_kyvos_name(ds_name)
        if kyvos_name != ds_name:
            ds_id = self.dataset_name_to_id.get(kyvos_name, "")
            if ds_id:
                return ds_id
            ds_id = self._name_to_id_ci.get(kyvos_name.lower(), "")
            if ds_id:
                return ds_id
        # Fuzzy: strip fact_/dim_ prefix and retry on name_to_id
        stripped = self._strip_table_prefix(ds_name)
        if stripped != ds_name:
            ds_id = self._name_to_id_ci.get(stripped.lower(), "")
            if ds_id:
                return ds_id
            kyvos_name = self._resolve_kyvos_name(stripped)
            if kyvos_name != stripped:
                ds_id = self._name_to_id_ci.get(kyvos_name.lower(), "")
                if ds_id:
                    return ds_id
        # Fuzzy: try substring matching as last resort (e.g. finance → financial_reporting)
        ds_lower = ds_name.lower()
        stripped_lower = stripped.lower() if stripped != ds_name else ds_lower
        for known_name, known_id in self._name_to_id_ci.items():
            if stripped_lower and stripped_lower in known_name:
                return known_id
            if ds_lower and len(ds_lower) >= 4 and ds_lower in known_name:
                return known_id
        return ""

    @staticmethod
    def _strip_table_prefix(name: str) -> str:
        """Strip fact_/dim_/bridge_ prefix and trailing _N suffix from a snake_case table name."""
        lower = name.lower()
        for prefix in ("fact_", "dim_", "bridge_"):
            if lower.startswith(prefix):
                name = name[len(prefix):]
                lower = name.lower()
                break
        # Strip trailing _<digits> suffix (e.g. internet_sales_1 → internet_sales)
        m = re.match(r"^(.+?)_(\d+)$", name)
        if m:
            name = m.group(1)
        return name

    def _summary_function_for(self, aggregation_type: str) -> str:
        """Map aggregation_type string to Kyvos summaryFunction string."""
        agg_lower = (aggregation_type or "sum").strip().lower()
        if agg_lower not in _AGGREGATION_TYPE_TO_SUMMARYFUNCTION:
            logger.warning(
                "unknown_aggregation_type_defaulting_to_sum",
                aggregation_type=aggregation_type,
            )
        return _AGGREGATION_TYPE_TO_SUMMARYFUNCTION.get(agg_lower, "0")

    def _build_dimension(
        self,
        ds_name: str,
        dataset_id: str,
        cols: list[dict],
        hierarchy_map: dict[str, list[dict]],
    ) -> dict[str, Any]:
        """Build a single dimension with hierarchies and levels.

        Each non-NUMBER column becomes a level in the dimension's default hierarchy.
        If hierarchy_specs define hierarchies for this dataset, use those instead.
        """
        dim_id = _safe_id("DIM", f"{ds_name}{time.time()}")
        dim_name = ds_name

        # Build hierarchies for this dimension
        hiers: list[dict[str, Any]] = []
        hier_levels = hierarchy_map.get(ds_name, [])

        if hier_levels:
            # Use hierarchy specs
            for h_spec in hier_levels:
                h_name = h_spec.get("name", f"H_{dim_name}")
                h_levels = h_spec.get("levels", [])
                levels_json: list[dict[str, Any]] = []
                for idx, level in enumerate(h_levels):
                    level_name = level if isinstance(level, str) else (level.get("name", "") if isinstance(level, dict) else str(level))
                    col_type = "CHAR"
                    for c in cols:
                        if c.get("name", "").lower() == level_name.lower():
                            col_type = c.get("dataType", c.get("datatype", c.get("dataTypeName", "CHAR")))
                            break
                    levels_json.append({
                        "name": level_name,
                        "uniqueName": _safe_id("DIMENSION_LEVEL", f"{h_name}{level_name}{idx}"),
                        "dataType": col_type,
                        "isAll": False,
                        "isVisible": True,
                        "isAccessible": True,
                        "materialize": "YES",
                        "aggregationType": "BOTH",
                        "processType": "DATA_AND_METADATA",
                        "dataField": _data_field(ds_name, level_name, dataset_id),
                    })
                hiers.append({
                    "name": h_name,
                    "uniqueName": _safe_id("HIER", f"{h_name}{ds_name}"),
                    "isDefault": len(hiers) == 0,
                    "hasAll": False,
                    "isVisible": True,
                    "isAccessible": True,
                    "materialize": "",
                    "hasAlternatePath": False,
                    "hasParentChildRelation": False,
                    "pcLevelCount": -1,
                    "qualifyMembers": {"type": "ALL_PARENTS", "value": ""},
                    "dataField": _data_field("", "", ""),
                    "levels": levels_json,
                })
        else:
            # Default: one hierarchy with all non-NUMBER columns as levels
            levels_json: list[dict[str, Any]] = []
            for idx, col in enumerate(cols):
                col_name = col.get("name", "")
                col_type = col.get("dataType", col.get("datatype", col.get("dataTypeName", "CHAR")))
                if col_type in ("NUMBER",):
                    continue
                levels_json.append({
                    "name": col_name,
                    "uniqueName": _safe_id("DIMENSION_LEVEL", f"{ds_name}{col_name}{idx}"),
                    "dataType": col_type,
                    "isAll": False,
                    "isVisible": True,
                    "isAccessible": True,
                    "materialize": "YES",
                    "aggregationType": "BOTH",
                    "processType": "DATA_AND_METADATA",
                    "dataField": _data_field(ds_name, col_name, dataset_id),
                })
            if levels_json:
                h_name = f"H_{dim_name}"
                hiers.append({
                    "name": h_name,
                    "uniqueName": _safe_id("HIER", f"{h_name}{ds_name}"),
                    "isDefault": True,
                    "hasAll": False,
                    "isVisible": True,
                    "isAccessible": True,
                    "materialize": "",
                    "hasAlternatePath": False,
                    "hasParentChildRelation": False,
                    "pcLevelCount": -1,
                    "qualifyMembers": {"type": "ALL_PARENTS", "value": ""},
                    "dataField": _data_field("", "", ""),
                    "levels": levels_json,
                })

        # Build attrs (non-measure, non-level columns as standalone attributes).
        # NUMBER columns are included as attributes (not levels) so they remain
        # available in the model for filtering and drilling.
        level_names = {lvl["name"].lower() for h in hiers for lvl in h.get("levels", [])}
        attrs_json: list[dict[str, Any]] = []
        for col in cols:
            col_name = col.get("name", "")
            col_type = col.get("dataType", col.get("datatype", col.get("dataTypeName", "CHAR")))
            if col_name.lower() in level_names:
                continue
            attrs_json.append({
                "name": col_name,
                "uniqueName": _safe_id("DIMENSION_ATTRIBUTE", f"{ds_name}{col_name}"),
                "dataType": col_type,
                "type": "Regular",
                "isVisible": True,
                "isAccessible": True,
                "materialize": "YES",
                "aggregationType": "BOTH",
                "processType": "DATA_AND_METADATA",
                "processMetadata": "YES",
                "dataField": _data_field(ds_name, col_name, dataset_id),
            })

        return {
            "name": dim_name,
            "id": dim_id,
            "uniqueName": dim_id,
            "type": "Regular",
            "scdType": "2",
            "materialize": "",
            "isAccessible": True,
            "isVisible": True,
            "dimToFactMapping": "ONE_TO_MANY",
            "incrementalUpdateProperties": {"processingMode": 1},
            "dataSources": [{"id": dataset_id, "type": "REFERENCED"}],
            "hierarchies": hiers,
            "attrs": attrs_json,
        }

    def generate(self) -> dict[str, Any]:
        """Generate Simplified JSON payload for semantic model creation.

        Returns:
            Dict matching the Kyvos 2026.5+ Simplified semantic model JSON shape.
        """
        self.smodel_id = _safe_id("SMODEL", f"{self.smodel_name}{self.drd_id}{time.time()}")

        # ------------------------------------------------------------------
        # Build hierarchy map: dataset_name -> list of hierarchy specs
        # ------------------------------------------------------------------
        hierarchy_map: dict[str, list[dict]] = {}
        for hierarchy in self.hierarchy_specs:
            h_name = hierarchy.name if hasattr(hierarchy, "name") else hierarchy.get("name", "")
            h_levels = hierarchy.levels if hasattr(hierarchy, "levels") else hierarchy.get("levels", [])
            h_source = hierarchy.source_dataset if hasattr(hierarchy, "source_dataset") else hierarchy.get("source_dataset", "")
            hierarchy_map.setdefault(h_source, []).append({"name": h_name, "levels": h_levels})

        # ------------------------------------------------------------------
        # Build dimensions from dataset columns
        # ------------------------------------------------------------------
        dimensions_json: list[dict[str, Any]] = []
        for ds_name, cols in self.dataset_columns.items():
            # Skip fact tables — they become measure groups, not dimensions
            kyvos_ds_name = self._resolve_kyvos_name(ds_name)
            if ds_name in self.fact_dataset_names or kyvos_ds_name in self.fact_dataset_names:
                continue
            # Skip orphan dimensions — not connected to any fact via relationships
            if self.connected_dim_names:
                if (ds_name not in self.connected_dim_names
                        and kyvos_ds_name not in self.connected_dim_names
                        and ds_name.lower() not in {n.lower() for n in self.connected_dim_names}):
                    logger.warning(
                        "orphan_dimension_skipped_json",
                        dimension=ds_name,
                        kyvos_name=kyvos_ds_name,
                        reason="no_fact_relationship",
                    )
                    continue
            dataset_id = self._resolve_dataset_id(ds_name)
            # Skip datasets that have no non-NUMBER columns — unless the dataset
            # is a known connected dimension (some dim tables like Department or
            # Scenario may have only key columns).
            has_dim_cols = any(
                col.get("dataType", col.get("datatype", col.get("dataTypeName", "CHAR"))) not in ("NUMBER",)
                for col in cols
            )
            is_connected_dim = (
                ds_name in self.connected_dim_names
                or kyvos_ds_name in self.connected_dim_names
                or ds_name.lower() in {n.lower() for n in self.connected_dim_names}
            )
            if has_dim_cols or is_connected_dim:
                dimensions_json.append(
                    self._build_dimension(ds_name, dataset_id, cols, hierarchy_map)
                )

        # ------------------------------------------------------------------
        # Build measures
        # ------------------------------------------------------------------
        measures_json: list[dict[str, Any]] = []
        measure_groups_json: list[dict[str, Any]] = []
        measure_counter = 0
        default_measure_assigned = False

        # Track which datasets already have a measure group
        existing_mg_names: set[str] = set()

        # Group measures by source_dataset
        measures_by_dataset: dict[str, list] = {}
        for measure in self.semantic_measures:
            src_ds = measure.source_dataset if hasattr(measure, "source_dataset") else measure.get("source_dataset", "")
            measures_by_dataset.setdefault(src_ds, []).append(measure)

        for ds_name, ds_measures in measures_by_dataset.items():
            kyvos_ds_name = self._resolve_kyvos_name(ds_name)
            dataset_id = self._resolve_dataset_id(ds_name)
            if not dataset_id:
                logger.warning(
                    "smodel_measure_dataset_id_empty",
                    source_dataset=ds_name,
                    kyvos_dataset_name=kyvos_ds_name,
                    available_ids=list(self.dataset_name_to_id.keys())[:20],
                )
            existing_mg_names.add(kyvos_ds_name.lower())

            mg_measure_ids: list[str] = []
            for measure in ds_measures:
                measure_id = f"MEASURE_{measure_counter}"
                measure_counter += 1

                measure_name = measure.name if hasattr(measure, "name") else str(measure.get("name", ""))
                is_calculated = measure.is_calculated if hasattr(measure, "is_calculated") else measure.get("is_calculated", False)
                expression = measure.expression if hasattr(measure, "expression") else measure.get("expression", "")
                format_string = measure.format_string if hasattr(measure, "format_string") else measure.get("format_string", "#,##0.00")
                source_column = measure.source_column if hasattr(measure, "source_column") else measure.get("source_column", "")
                aggregation_type = measure.aggregation_type if hasattr(measure, "aggregation_type") else measure.get("aggregation_type", "sum")

                agg_lower = aggregation_type.strip().lower()
                summary_func = self._summary_function_for(aggregation_type)
                is_default = not default_measure_assigned
                if is_default:
                    default_measure_assigned = True

                # Count and distinct-count measures should always use NUMBER
                # dataType regardless of the source column's type.
                col_datatype = "NUMBER"

                measure_obj: dict[str, Any] = {
                    "id": measure_id,
                    "name": measure_name,
                    "uniqueName": measure_id,
                    "type": "STANDARD",
                    "dataType": col_datatype,
                    "formatType": "2",
                    "isDefault": is_default,
                    "isVisible": True,
                    "isAccessible": True,
                    "summaryFunction": summary_func,
                    "format": _format_obj(format_string or "#,##0.00"),
                    "materialize": "YES",
                    "dataField": _data_field(kyvos_ds_name, source_column or "", dataset_id),
                }

                if is_calculated and expression:
                    measure_obj["expression"] = {
                        "nonEmptyBehavior": "BY_MEASURE",
                        "content": expression,
                    }
                    measure_obj["summaryFunction"] = ""
                    measure_obj["dataField"] = _data_field("", "", "")

                if agg_lower == "distinct_count":
                    measure_obj["isBoundaryDistCount"] = True
                    measure_obj["distinctCountType"] = "EXACT"
                    measure_obj["distCountDatasetNodeId"] = dataset_id
                    measure_obj["distCountDatasetNodeName"] = kyvos_ds_name

                measures_json.append(measure_obj)
                mg_measure_ids.append(measure_id)

            measure_groups_json.append({
                "name": kyvos_ds_name,
                "materialize": "YES",
                "measureId": mg_measure_ids,
            })

        # ------------------------------------------------------------------
        # Auto-discover base measures from fact table numeric columns
        # for facts that have no spec base measures (or only calculated/distinct_count).
        # This mirrors the XML generator's auto-discovery logic.
        # ------------------------------------------------------------------
        # Track which Kyvos dataset names already have non-distinct-count base measures
        ds_has_base_measure: dict[str, bool] = {}
        for measure in self.semantic_measures:
            is_calc = measure.is_calculated if hasattr(measure, "is_calculated") else measure.get("is_calculated", False)
            agg = (measure.aggregation_type if hasattr(measure, "aggregation_type") else measure.get("aggregation_type", "sum")).strip().lower()
            src_ds = measure.source_dataset if hasattr(measure, "source_dataset") else measure.get("source_dataset", "")
            kyvos_name = self._resolve_kyvos_name(src_ds)
            if not is_calc and agg != "distinct_count":
                ds_has_base_measure[kyvos_name.lower()] = True

        auto_discovered: set[str] = set()
        for fact_name in sorted(self.fact_dataset_names):
            fact_kyvos = self._resolve_kyvos_name(fact_name)
            if fact_kyvos.lower() in auto_discovered:
                continue  # Already auto-discovered for this Kyvos dataset
            if fact_kyvos.lower() in existing_mg_names and ds_has_base_measure.get(fact_kyvos.lower(), False):
                logger.debug("smodel_json_auto_discover_skip", fact=fact_name, kyvos=fact_kyvos, reason="already_has_base_measures")
                auto_discovered.add(fact_kyvos.lower())
                continue  # Already has base measures from spec

            fact_cols = self._cols_for(fact_kyvos) or self._cols_for(fact_name)
            if not fact_cols:
                logger.debug("smodel_json_auto_discover_skip", fact=fact_name, kyvos=fact_kyvos, reason="no_columns")
                continue

            dataset_id = self._resolve_dataset_id(fact_name)
            if not dataset_id:
                continue

            # Collect covered column names from existing spec measures
            covered_cols: set[str] = set()
            for measure in self.semantic_measures:
                src_ds = measure.source_dataset if hasattr(measure, "source_dataset") else measure.get("source_dataset", "")
                src_kyvos = self._resolve_kyvos_name(src_ds)
                if src_kyvos.lower() == fact_kyvos.lower():
                    col = (measure.source_column if hasattr(measure, "source_column") else measure.get("source_column", "") or "").strip().lower()
                    if col:
                        covered_cols.add(col)

            # Auto-discover numeric columns
            fact_join_keys = self.join_keys_by_dataset.get(fact_kyvos.lower(), set())
            auto_cols: list[dict] = []
            for col in fact_cols:
                col_name = col.get("name", "")
                col_type = col.get("dataType", col.get("datatype", col.get("dataTypeName", "CHAR")))
                if col_type != "NUMBER":
                    continue
                if col_name.lower() in covered_cols:
                    continue
                if col_name.lower() in fact_join_keys:
                    continue
                if col_name.lower().endswith("_key") or col_name.lower().endswith("_pk") or col_name.lower().endswith("_fk"):
                    continue
                auto_cols.append(col)

            if not auto_cols:
                # Last resort: use first column as count measure
                if fact_cols:
                    auto_cols = [fact_cols[0]]

            if not auto_cols:
                continue

            auto_discovered.add(fact_kyvos.lower())

            logger.info(
                "smodel_json_auto_discover",
                fact=fact_name,
                kyvos=fact_kyvos,
                auto_col_count=len(auto_cols),
                auto_cols=[c.get("name", "") for c in auto_cols[:10]],
                in_existing_mg=fact_kyvos.lower() in existing_mg_names,
                has_base_measure=ds_has_base_measure.get(fact_kyvos.lower(), False),
            )

            mg_measure_ids: list[str] = []
            for col in auto_cols:
                col_name = col.get("name", "")
                col_type = col.get("dataType", col.get("datatype", col.get("dataTypeName", "NUMBER")))
                measure_id = f"MEASURE_{measure_counter}"
                measure_counter += 1
                is_default = not default_measure_assigned
                if is_default:
                    default_measure_assigned = True

                measure_name = col_name
                agg_type = "sum" if col_type == "NUMBER" else "count"
                # Last-resort auto-discovery: always use count aggregation to
                # avoid creating a sum of a surrogate key or other non-measure column.
                if not auto_cols or col_type != "NUMBER":
                    agg_type = "count"
                summary_func = self._summary_function_for(agg_type)

                measures_json.append({
                    "id": measure_id,
                    "name": measure_name,
                    "uniqueName": measure_id,
                    "type": "STANDARD",
                    "dataType": col_type if agg_type == "count" else "NUMBER",
                    "formatType": "2",
                    "isDefault": is_default,
                    "isVisible": True,
                    "isAccessible": True,
                    "summaryFunction": summary_func,
                    "format": _format_obj("#,##0.00"),
                    "materialize": "YES",
                    "dataField": _data_field(fact_kyvos, col_name, dataset_id),
                })
                mg_measure_ids.append(measure_id)

            if fact_kyvos.lower() in existing_mg_names:
                # Append to existing measure group
                for mg in measure_groups_json:
                    if mg.get("name", "").lower() == fact_kyvos.lower():
                        mg["measureId"].extend(mg_measure_ids)
                        break
            else:
                measure_groups_json.append({
                    "name": fact_kyvos,
                    "materialize": "YES",
                    "measureId": mg_measure_ids,
                })
                existing_mg_names.add(fact_kyvos.lower())

        # ------------------------------------------------------------------
        # Companion measure groups for datasets referenced by DistinctCount()
        # ------------------------------------------------------------------
        orphan_ds_names: set[str] = set()
        for m in self.semantic_measures:
            expr = getattr(m, "expression", "") or ""
            for ref in re.findall(r"DistinctCount\s*\(\s*\[([^\]]+)\]", expr, re.IGNORECASE):
                ref_stripped = ref.strip()
                ref_kyvos = self._resolve_kyvos_name(ref_stripped)
                if ref_kyvos.lower() not in existing_mg_names and self._resolve_dataset_id(ref_stripped):
                    orphan_ds_names.add(ref_kyvos.lower())
            agg = (getattr(m, "aggregation_type", "") or "").strip().lower()
            if agg == "distinct_count":
                src_ds = (getattr(m, "source_dataset", "") or "").strip()
                src_kyvos = self._resolve_kyvos_name(src_ds)
                if src_kyvos and src_kyvos.lower() not in existing_mg_names and self._resolve_dataset_id(src_ds):
                    orphan_ds_names.add(src_kyvos.lower())

        for ds_lower in sorted(orphan_ds_names):
            ds_name_proper = next(
                (k for k in self.dataset_name_to_id if k.lower() == ds_lower),
                next(
                    (k for k in self._display_to_kyvos_ci.values() if k.lower() == ds_lower),
                    ds_lower,
                ),
            )
            node_id = self._resolve_dataset_id(ds_name_proper)

            companion_cols = self._cols_for(ds_name_proper)
            if not companion_cols:
                continue

            mg_measure_ids: list[str] = []

            # Add distinct-count base measures
            ds_distinct_measures = []
            for m in self.semantic_measures:
                agg = (getattr(m, "aggregation_type", "") or "").strip().lower()
                src_ds = (getattr(m, "source_dataset", "") or "").strip()
                src_kyvos_lower = self._resolve_kyvos_name(src_ds).lower()
                if agg == "distinct_count" and src_kyvos_lower == ds_lower:
                    ds_distinct_measures.append(m)

            for dm in ds_distinct_measures:
                col_name = (getattr(dm, "source_column", "") or "").strip()
                col = None
                for c in companion_cols:
                    if c.get("name", "").lower() == col_name.lower():
                        col = c
                        break
                if col is None:
                    col = companion_cols[0]

                measure_id = f"MEASURE_{measure_counter}"
                measure_counter += 1
                is_default = not default_measure_assigned
                if is_default:
                    default_measure_assigned = True

                dm_name = getattr(dm, "name", f"DistinctCount_{col_name}")

                measures_json.append({
                    "id": measure_id,
                    "name": dm_name,
                    "uniqueName": measure_id,
                    "type": "STANDARD",
                    "dataType": "NUMBER",
                    "formatType": "2",
                    "isDefault": is_default,
                    "isVisible": True,
                    "isAccessible": True,
                    "summaryFunction": "11",
                    "format": _format_obj("#,##0"),
                    "materialize": "YES",
                    "dataField": _data_field(ds_name_proper, col_name or col.get("name", ""), node_id),
                    "isBoundaryDistCount": True,
                    "distinctCountType": "EXACT",
                    "distCountDatasetNodeId": node_id,
                    "distCountDatasetNodeName": ds_name_proper,
                })
                mg_measure_ids.append(measure_id)

            # Row-count companion measure
            anchor_col = companion_cols[0]
            companion_measure_name = f"{ds_name_proper} Record Count"
            companion_mid = f"MEASURE_{measure_counter}"
            measure_counter += 1
            is_default = not default_measure_assigned
            if is_default:
                default_measure_assigned = True

            measures_json.append({
                "id": companion_mid,
                "name": companion_measure_name,
                "uniqueName": companion_mid,
                "type": "STANDARD",
                "dataType": "NUMBER",
                "formatType": "2",
                "isDefault": is_default,
                "isVisible": True,
                "isAccessible": True,
                "summaryFunction": "2",
                "format": _format_obj("#,##0"),
                "materialize": "YES",
                "dataField": _data_field(ds_name_proper, anchor_col.get("name", ""), node_id),
            })
            mg_measure_ids.append(companion_mid)

            measure_groups_json.append({
                "name": ds_name_proper,
                "materialize": "YES",
                "measureId": mg_measure_ids,
            })

        logger.info(
            "smodel_json_structure",
            dimensions=len(dimensions_json),
            measures=len(measures_json),
            measure_groups=len(measure_groups_json),
            fact_datasets_skipped=len(self.fact_dataset_names),
        )

        return {
            "name": self.smodel_name,
            "folderName": self.folder_name,
            "folderId": self.folder_id,
            "specific": {
                "attrs": {
                    "drdName": self.drd_name,
                    "drdId": self.drd_id,
                    "modelType": "BASE",
                    "rawDataQuerying": "Disable",
                    "rawDataConnectionMode": "MANUAL",
                    "rawDataConnectionName": self.connection_name,
                },
                "smObject": {
                    "dimensions": dimensions_json,
                    "measures": {"measure": measures_json},
                    "measureGroups": {"measureGroup": measure_groups_json},
                },
            },
        }
