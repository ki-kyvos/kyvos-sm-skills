"""Spec builder — convert LLM SM recommendation dicts into typed spec objects.

Takes an LLM-produced semantic model recommendation (plain dict) and the
warehouse schema inspection result, and produces a ``DiscoveredSpec`` with
typed ``TableSpec``, ``RelationshipSpec``, ``MeasureSpec``, ``HierarchySpec``,
and ``SemanticModelSpec`` objects ready for the deployment pipeline.

Usage::

    from kyvos_sm_skills.spec_builder import build_spec_from_recommendation

    spec = build_spec_from_recommendation(
        sm_rec=llm_recommendation,
        warehouse_tables=inspected_schema["tables"],
    )
    # spec.tables → list[TableSpec]
    # spec.semantic_model → SemanticModelSpec
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kyvos_sm_skills.mdx_reference import convert_dax_to_mdx, validate_mdx_expression
from kyvos_sm_skills.models import (
    ColumnSpec,
    DatasetSpec,
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)


@dataclass
class DiscoveredSpec:
    """Result of building a spec from an LLM recommendation + warehouse schema.

    Attributes:
        tables: List of TableSpec objects for each table in the SM recommendation.
        semantic_model: SemanticModelSpec with relationships, measures, hierarchies.
        metadata: Extra context (schema_type, domain, rationale, etc.).
    """

    tables: list[TableSpec] = field(default_factory=list)
    semantic_model: SemanticModelSpec = field(default_factory=lambda: SemanticModelSpec(name=""))
    metadata: dict[str, Any] = field(default_factory=dict)


def build_spec_from_recommendation(
    sm_rec: dict[str, Any],
    warehouse_tables: list[dict[str, Any]],
) -> DiscoveredSpec:
    """Construct a DiscoveredSpec from an LLM SM recommendation and warehouse schema.

    Args:
        sm_rec: LLM recommendation dict with keys: name, schema_type, tables,
                relationships, measures, hierarchies, rationale.
        warehouse_tables: List of table dicts from ``inspect_schema()`` output,
                          each with: name, schema, columns, estimated_table_type,
                          outgoing_fk_count, incoming_fk_count.

    Returns:
        DiscoveredSpec with typed TableSpec and SemanticModelSpec objects.

    Raises:
        ValueError: If a table in the recommendation is not found in the warehouse,
                    or if a measure's source_dataset doesn't match any table,
                    or if a relationship references unknown tables/columns.
    """
    # Build a lookup of warehouse tables by name (case-insensitive)
    wh_table_map: dict[str, dict[str, Any]] = {}
    for wt in warehouse_tables:
        wh_table_map[wt["name"].lower()] = wt

    # Validate all tables in the recommendation exist in the warehouse
    rec_table_names = sm_rec.get("tables", [])
    missing_tables = [
        t for t in rec_table_names
        if t.lower() not in wh_table_map
    ]
    if missing_tables:
        raise ValueError(
            f"Tables in SM recommendation not found in warehouse schema: {missing_tables}. "
            f"Available warehouse tables: {list(wh_table_map.keys())}"
        )

    # Build TableSpec objects for each recommended table
    table_specs: list[TableSpec] = []
    table_name_set = {t.lower() for t in rec_table_names}

    for table_name in rec_table_names:
        wt = wh_table_map[table_name.lower()]
        columns = _build_column_specs(wt)
        table_type = _map_table_type(wt.get("estimated_table_type", "unknown"))

        table_specs.append(TableSpec(
            name=wt["name"],
            schema_name=wt.get("schema", "public"),
            table_type=table_type,
            columns=columns,
        ))

    # Build RelationshipSpec objects
    relationships = _build_relationships(
        sm_rec.get("relationships", []),
        wh_table_map,
    )

    # Auto-add any tables referenced in relationships but missing from table_specs
    _existing_table_names = {ts.name.lower() for ts in table_specs}
    for rel in relationships:
        for rel_table in [rel.left_dataset, rel.right_dataset]:
            if rel_table.lower() not in _existing_table_names and rel_table.lower() in wh_table_map:
                wt = wh_table_map[rel_table.lower()]
                columns = _build_column_specs(wt)
                table_type = _map_table_type(wt.get("estimated_table_type", "unknown"))
                table_specs.append(TableSpec(
                    name=wt["name"],
                    schema_name=wt.get("schema", "public"),
                    table_type=table_type,
                    columns=columns,
                ))
                _existing_table_names.add(wt["name"].lower())

    # Build MeasureSpec objects
    measures = _build_measures(
        sm_rec.get("measures", []),
        wh_table_map,
        table_specs,
    )

    # Auto-add any tables referenced by measure source_dataset but missing from table_specs
    for ms in measures:
        if ms.source_dataset and ms.source_dataset.lower() not in _existing_table_names and ms.source_dataset.lower() in wh_table_map:
            wt = wh_table_map[ms.source_dataset.lower()]
            columns = _build_column_specs(wt)
            table_type = _map_table_type(wt.get("estimated_table_type", "unknown"))
            table_specs.append(TableSpec(
                name=wt["name"],
                schema_name=wt.get("schema", "public"),
                table_type=table_type,
                columns=columns,
            ))
            _existing_table_names.add(wt["name"].lower())

    # Build HierarchySpec objects
    hierarchies = _build_hierarchies(
        sm_rec.get("hierarchies", []),
        wh_table_map,
    )

    # Build DatasetSpec objects for each table (needed for contract validation)
    dataset_specs = [
        DatasetSpec(
            name=ts.name,
            source_table=ts.name,
            connection_name="",
            columns=[c.name for c in ts.columns],
        )
        for ts in table_specs
    ]

    # Build SemanticModelSpec
    sm_name = sm_rec.get("name", "DiscoveredSM")
    semantic_model = SemanticModelSpec(
        name=sm_name,
        datasets=dataset_specs,
        relationships=relationships,
        measures=measures,
        hierarchies=hierarchies,
    )

    # Build metadata
    metadata = {
        "schema_type": sm_rec.get("schema_type", "unknown"),
        "rationale": sm_rec.get("rationale", ""),
        "source": "warehouse_discovery",
    }

    return DiscoveredSpec(
        tables=table_specs,
        semantic_model=semantic_model,
        metadata=metadata,
    )


def _build_column_specs(warehouse_table: dict[str, Any]) -> list[ColumnSpec]:
    """Convert warehouse column dicts to ColumnSpec objects."""
    columns = []
    for col in warehouse_table.get("columns", []):
        columns.append(ColumnSpec(
            name=col["name"],
            data_type=col.get("data_type", "TEXT"),
            nullable=not col.get("is_pk", False),
            is_primary_key=col.get("is_pk", False),
            is_foreign_key=col.get("is_fk", False),
            references=col.get("references") or None,
        ))
    return columns


def _map_table_type(estimated_type: str) -> str:
    """Map warehouse inspector's estimated type to TableSpec.table_type."""
    mapping = {
        "fact": "fact",
        "dimension": "dimension",
        "bridge": "bridge",
        "unknown": "dimension",  # Default to dimension for unknown
    }
    return mapping.get(estimated_type, "dimension")


def _build_relationships(
    rels: list[dict[str, Any]],
    wh_table_map: dict[str, dict[str, Any]],
) -> list[RelationshipSpec]:
    """Convert relationship dicts to RelationshipSpec objects.

    Validates that referenced tables and columns exist in the warehouse schema.
    """
    relationships = []
    for rel in rels:
        from_table = rel.get("from_table", "")
        to_table = rel.get("to_table", "")
        from_column = rel.get("from_column", "")
        to_column = rel.get("to_column", "")

        # Validate tables exist
        if from_table.lower() not in wh_table_map:
            raise ValueError(
                f"Relationship references unknown table '{from_table}' "
                f"(from_table). Available: {list(wh_table_map.keys())}"
            )
        if to_table.lower() not in wh_table_map:
            raise ValueError(
                f"Relationship references unknown table '{to_table}' "
                f"(to_table). Available: {list(wh_table_map.keys())}"
            )

        # Validate columns exist
        from_cols_list = wh_table_map[from_table.lower()].get("columns", [])
        to_cols_list = wh_table_map[to_table.lower()].get("columns", [])
        from_cols = {c["name"].lower() for c in from_cols_list}
        to_cols = {c["name"].lower() for c in to_cols_list}

        if from_column.lower() not in from_cols:
            raise ValueError(
                f"Relationship column '{from_column}' not found in table '{from_table}'. "
                f"Available columns: {list(from_cols)}"
            )
        if to_column.lower() not in to_cols:
            raise ValueError(
                f"Relationship column '{to_column}' not found in table '{to_table}'. "
                f"Available columns: {list(to_cols)}"
            )

        # Check column type compatibility — skip incompatible relationships
        from_col_type = next((c.get("data_type", "") for c in from_cols_list if c["name"].lower() == from_column.lower()), "")
        to_col_type = next((c.get("data_type", "") for c in to_cols_list if c["name"].lower() == to_column.lower()), "")
        _from_is_date = "DATE" in from_col_type.upper()
        _to_is_date = "DATE" in to_col_type.upper()
        _from_is_int = "INT" in from_col_type.upper()
        _to_is_int = "INT" in to_col_type.upper()
        if (_from_is_date and _to_is_int) or (_from_is_int and _to_is_date):
            # Skip incompatible date/int relationships
            continue

        # Skip self-join relationships — Kyvos DRD does not support them.
        # Parent-child relationships (e.g., employee.parentemployeekey -> employee.employeekey)
        # should be modeled as hierarchies in the semantic model, not as DRD relationships.
        if from_table.lower() == to_table.lower():
            continue

        # Normalize to actual warehouse table names (case-insensitive)
        actual_from = wh_table_map[from_table.lower()]["name"]
        actual_to = wh_table_map[to_table.lower()]["name"]

        rel_type = rel.get("relationship_type", "many_to_one")

        relationships.append(RelationshipSpec(
            left_dataset=actual_from,
            left_column=from_column,
            right_dataset=actual_to,
            right_column=to_column,
            relationship_type=rel_type,
        ))

    return relationships


def _build_measures(
    measures: list[dict[str, Any]],
    wh_table_map: dict[str, dict[str, Any]],
    table_specs: list[TableSpec] | None = None,
) -> list[MeasureSpec]:
    """Convert measure dicts to MeasureSpec objects.

    Validates that source_dataset matches a known table.
    For calculated measures without source_dataset, assigns them to the first
    fact table in the spec to prevent the compiler from duplicating them across
    all fact tables (which causes 'Measure is not unique' validation errors).
    """
    # Identify fact tables from the spec for assigning unscoped calculated measures
    _fact_table_names: list[str] = []
    if table_specs:
        _fact_table_names = [
            ts.name for ts in table_specs if ts.table_type == "fact"
        ]

    result = []
    for m in measures:
        name = m.get("name", "")
        source_dataset = m.get("source_dataset", "")
        agg_type = m.get("aggregation_type", "sum")

        if not name:
            raise ValueError(f"Measure missing 'name' field: {m}")

        if source_dataset and source_dataset.lower() not in wh_table_map:
            raise ValueError(
                f"Measure '{name}' references unknown source_dataset '{source_dataset}'. "
                f"Available tables: {list(wh_table_map.keys())}"
            )

        # Normalize source_dataset to actual warehouse table name
        actual_source = wh_table_map[source_dataset.lower()]["name"] if source_dataset else ""

        # For calculated measures without source_dataset, assign to the first fact table.
        # The Kyvos compiler places measures with no source_dataset on EVERY fact table,
        # which causes "Measure is not unique" validation errors. By assigning a specific
        # fact table, the measure is placed only once.
        is_calculated = m.get("is_calculated", False)
        if not actual_source and is_calculated and _fact_table_names:
            actual_source = _fact_table_names[0]

        # Use source_column from SM design if explicitly provided
        source_column = m.get("source_column") or None
        if not source_column and source_dataset:
            cols = wh_table_map[source_dataset.lower()].get("columns", [])
            col_names_lower = {c["name"].lower(): c["name"] for c in cols}
            # Try exact match first
            if name.lower() in col_names_lower:
                source_column = col_names_lower[name.lower()]
            else:
                # Try matching with spaces/underscores removed
                name_nospace = name.lower().replace(" ", "").replace("_", "")
                for col_lower, col_actual in col_names_lower.items():
                    if col_lower.replace(" ", "").replace("_", "") == name_nospace:
                        source_column = col_actual
                        break

                # Try abbreviation-aware matching (amt=amount, pct=percent, etc.)
                if not source_column:
                    _abbr_map = {
                        "amount": "amt", "percent": "pct", "quantity": "qty",
                        "number": "nbr", "description": "desc",
                    }
                    name_normalized = name_nospace
                    for full, abbr in _abbr_map.items():
                        name_normalized = name_normalized.replace(full, abbr)
                    for col_lower, col_actual in col_names_lower.items():
                        col_normalized = col_lower.replace(" ", "").replace("_", "")
                        for full, abbr in _abbr_map.items():
                            col_normalized = col_normalized.replace(full, abbr)
                        if col_normalized == name_normalized:
                            source_column = col_actual
                            break

                # Try prefix-based matching as last resort (e.g. "Discount Amount" → "discount*")
                if not source_column:
                    name_prefix = name_nospace[:8]  # first 8 chars for specificity
                    for col_lower, col_actual in col_names_lower.items():
                        col_nospace = col_lower.replace(" ", "").replace("_", "")
                        if col_nospace.startswith(name_prefix):
                            source_column = col_actual
                            break

                # Try substring matching (e.g. "Total Sales Amount" contains "salesamount")
                if not source_column:
                    _abbr_map = {
                        "amount": "amt", "percent": "pct", "quantity": "qty",
                        "number": "nbr", "description": "desc",
                    }
                    name_abbr = name_nospace
                    for full, abbr in _abbr_map.items():
                        name_abbr = name_abbr.replace(full, abbr)
                    for col_lower, col_actual in col_names_lower.items():
                        col_nospace = col_lower.replace(" ", "").replace("_", "")
                        col_abbr = col_nospace
                        for full, abbr in _abbr_map.items():
                            col_abbr = col_abbr.replace(full, abbr)
                        # Check if column name appears as substring in measure name
                        if len(col_nospace) >= 5 and (col_nospace in name_nospace or col_abbr in name_abbr):
                            source_column = col_actual
                            break
                        # Or measure name appears in column name
                        if len(name_nospace) >= 5 and (name_nospace in col_nospace or name_abbr in col_abbr):
                            source_column = col_actual
                            break

        expression = m.get("expression", "")

        # Convert DAX patterns to Kyvos MDX if the LLM produced DAX syntax
        if expression and is_calculated:
            expression = convert_dax_to_mdx(expression)
            _mdx_warnings = validate_mdx_expression(expression)
            for _w in _mdx_warnings:
                print(f"  MDX warning for measure '{name}': {_w}")

        # Make measure name unique across fact tables
        measure_name = name
        _existing_names = {ms.name.lower() for ms in result}
        if measure_name.lower() in _existing_names:
            if actual_source:
                # Prefix with dataset name (e.g., "Internet Sales Amount")
                _ds_prefix = actual_source.replace("_", " ").title()
                measure_name = f"{_ds_prefix} {name}"
            else:
                # No source_dataset — add numeric suffix
                _suffix = 2
                while f"{measure_name} {_suffix}".lower() in _existing_names:
                    _suffix += 1
                measure_name = f"{name} {_suffix}"
            # If still colliding, add numeric suffix
            if measure_name.lower() in _existing_names:
                _suffix = 2
                while f"{measure_name} {_suffix}".lower() in _existing_names:
                    _suffix += 1
                measure_name = f"{measure_name} {_suffix}"

        result.append(MeasureSpec(
            name=measure_name,
            expression=expression or (source_column or name),
            source_dataset=actual_source or None,
            aggregation_type=agg_type,
            source_column=source_column,
            is_calculated=is_calculated,
        ))

    return result


def _build_hierarchies(
    hierarchies: list[dict[str, Any]],
    wh_table_map: dict[str, dict[str, Any]],
) -> list[HierarchySpec]:
    """Convert hierarchy dicts to HierarchySpec objects.

    Validates that:
    - source_dataset matches a known table
    - each level is an actual column on the source_dataset table
    - parent-child hierarchies have parent_column and child_column
    """
    result = []
    for h in hierarchies:
        name = h.get("name", "")
        levels = h.get("levels", [])
        source_dataset = h.get("source_dataset", "")
        is_parent_child = h.get("is_parent_child", False)
        parent_column = h.get("parent_column")
        child_column = h.get("child_column")

        if not name:
            raise ValueError(f"Hierarchy missing 'name' field: {h}")

        if not source_dataset:
            raise ValueError(
                f"Hierarchy '{name}' is missing 'source_dataset'. "
                f"Every hierarchy must specify a source_dataset (dimension table)."
            )

        if source_dataset.lower() not in wh_table_map:
            raise ValueError(
                f"Hierarchy '{name}' references unknown source_dataset '{source_dataset}'. "
                f"Available tables: {list(wh_table_map.keys())}"
            )

        # Normalize source_dataset to actual warehouse table name
        actual_source = wh_table_map[source_dataset.lower()]["name"]

        # Validate that each level is an actual column on the source_dataset table
        table_cols = wh_table_map[source_dataset.lower()].get("columns", [])
        col_names_lower = {c["name"].lower() for c in table_cols}

        _pc_columns_validated = False

        # For parent-child hierarchies, levels may be empty — they use parent_column/child_column
        if is_parent_child and not levels:
            if not parent_column or not child_column:
                print(
                    f"  Hierarchy '{name}': parent-child hierarchy missing parent_column or "
                    f"child_column. Skipping this hierarchy."
                )
                continue
            # Validate parent/child columns exist on the table
            if parent_column.lower() not in col_names_lower:
                print(
                    f"  Hierarchy '{name}': parent_column '{parent_column}' not found on "
                    f"table '{actual_source}'. Skipping this hierarchy."
                )
                continue
            if child_column.lower() not in col_names_lower:
                print(
                    f"  Hierarchy '{name}': child_column '{child_column}' not found on "
                    f"table '{actual_source}'. Skipping this hierarchy."
                )
                continue
            # Use actual column casing and validate data type match
            parent_dt = None
            child_dt = None
            for c in table_cols:
                if c["name"].lower() == parent_column.lower():
                    parent_column = c["name"]
                    parent_dt = c.get("data_type", c.get("type", "")).upper()
                if c["name"].lower() == child_column.lower():
                    child_column = c["name"]
                    child_dt = c.get("data_type", c.get("type", "")).upper()
            if parent_dt and child_dt and parent_dt != child_dt:
                print(
                    f"  Hierarchy '{name}': parent_column '{parent_column}' (type {parent_dt}) "
                    f"and child_column '{child_column}' (type {child_dt}) have different data types. "
                    f"Kyvos requires both columns to have the same data type. Skipping this hierarchy."
                )
                continue
            validated_levels = [child_column]
            _pc_columns_validated = True
        else:
            if not levels:
                print(
                    f"  Hierarchy '{name}': no levels provided and not a parent-child hierarchy. "
                    f"Skipping this hierarchy."
                )
                continue

            validated_levels: list[str] = []
            for level in levels:
                if level.lower() in col_names_lower:
                    # Use the actual column name casing from the warehouse
                    for c in table_cols:
                        if c["name"].lower() == level.lower():
                            validated_levels.append(c["name"])
                            break
                else:
                    print(
                        f"  Hierarchy '{name}': level '{level}' not found on table '{actual_source}'. "
                        f"Available columns: {[c['name'] for c in table_cols[:10]]}..."
                    )
                    # Skip levels that don't exist on the table

            if not validated_levels:
                print(
                    f"  Hierarchy '{name}': no valid levels found on table '{actual_source}'. "
                    f"Skipping this hierarchy."
                )
                continue

        # Validate parent-child columns if not already validated in the early branch
        if is_parent_child and not _pc_columns_validated:
            if not parent_column or not child_column:
                print(
                    f"  Hierarchy '{name}': parent-child hierarchy requires both "
                    f"parent_column and child_column. Clearing parent-child flag."
                )
                is_parent_child = False
                parent_column = None
                child_column = None
            elif parent_column.lower() not in col_names_lower:
                print(
                    f"  Hierarchy '{name}': parent_column '{parent_column}' not found on "
                    f"table '{actual_source}'. Clearing parent-child flag."
                )
                is_parent_child = False
                parent_column = None
                child_column = None
            elif child_column.lower() not in col_names_lower:
                print(
                    f"  Hierarchy '{name}': child_column '{child_column}' not found on "
                    f"table '{actual_source}'. Clearing parent-child flag."
                )
                is_parent_child = False
                parent_column = None
                child_column = None

        # Extract optional parent-child fields
        root_member_type = h.get("root_member_type", "auto")
        non_leaf_data_member_visible = h.get("non_leaf_data_member_visible", False)
        non_leaf_data_member_caption = h.get("non_leaf_data_member_caption", "self")
        display_column = h.get("display_column")
        pc_level_naming_pattern = h.get("pc_level_naming_pattern", "Level_*")
        has_alternate_path = h.get("has_alternate_path", False)
        custom_rollup_weight_column = h.get("custom_rollup_weight_column")

        # Validate display_column if specified
        if display_column and display_column.lower() not in col_names_lower:
            print(
                f"  Hierarchy '{name}': display_column '{display_column}' not found on "
                f"table '{actual_source}'. Clearing display_column."
            )
            display_column = None

        # Validate custom_rollup_weight_column if specified
        if custom_rollup_weight_column and custom_rollup_weight_column.lower() not in col_names_lower:
            print(
                f"  Hierarchy '{name}': custom_rollup_weight_column '{custom_rollup_weight_column}' "
                f"not found on table '{actual_source}'. Clearing."
            )
            custom_rollup_weight_column = None

        result.append(HierarchySpec(
            name=name,
            levels=validated_levels,
            source_dataset=actual_source or None,
            is_parent_child=is_parent_child,
            has_alternate_path=has_alternate_path,
            parent_column=parent_column,
            child_column=child_column,
            custom_rollup_weight_column=custom_rollup_weight_column,
            pc_level_naming_pattern=pc_level_naming_pattern,
            root_member_type=root_member_type,
            non_leaf_data_member_visible=non_leaf_data_member_visible,
            non_leaf_data_member_caption=non_leaf_data_member_caption,
            display_column=display_column,
        ))

    return result
