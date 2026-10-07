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

    # Build TableSpec objects for each recommended table.
    # If the LLM provided explicit table_classifications, use those to override
    # the warehouse-inferred estimated_table_type.
    table_specs: list[TableSpec] = []
    llm_classifications: dict[str, str] = {
        k.lower(): v.lower()
        for k, v in sm_rec.get("table_classifications", {}).items()
    }

    for table_name in rec_table_names:
        wt = wh_table_map[table_name.lower()]
        columns = _build_column_specs(wt)
        # Prefer LLM classification over warehouse heuristic
        if table_name.lower() in llm_classifications:
            table_type = llm_classifications[table_name.lower()]
        else:
            table_type = _map_table_type(wt.get("estimated_table_type", "unknown"), wt)

        table_specs.append(TableSpec(
            name=wt["name"],
            schema_name=wt.get("schema", "public"),
            database_name=wt.get("database"),
            table_type=table_type,
            columns=columns,
        ))

    # Build RelationshipSpec objects
    relationships = _build_relationships(
        sm_rec.get("relationships", []),
        wh_table_map,
        table_type_overrides=llm_classifications or None,
    )

    # Normalize bridge relationship directions. Kyvos connectivity expects
    # fact -> bridge -> dimension. LLMs sometimes emit bridge -> fact.
    relationships = _normalize_bridge_relationships(
        relationships=relationships,
        wh_table_map=wh_table_map,
    )

    # Auto-add any tables referenced in relationships but missing from table_specs
    _existing_table_names = {ts.name.lower() for ts in table_specs}
    for rel in relationships:
        for rel_table in [rel.left_dataset, rel.right_dataset]:
            if rel_table.lower() not in _existing_table_names and rel_table.lower() in wh_table_map:
                wt = wh_table_map[rel_table.lower()]
                columns = _build_column_specs(wt)
                table_type = _map_table_type(wt.get("estimated_table_type", "unknown"), wt)
                table_specs.append(TableSpec(
                    name=wt["name"],
                    schema_name=wt.get("schema", "public"),
                    database_name=wt.get("database"),
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

    # Ensure every fact table has at least one measure — fact tables with no
    # measures are dropped by the connectivity sweep (they're not valid BFS
    # starting points and are unreachable from other facts).
    measures = _ensure_fact_measures(measures, table_specs)

    # Auto-add any tables referenced by measure source_dataset but missing from table_specs
    measures_to_drop: set[str] = set()
    for ms in measures:
        if (
            ms.source_dataset
            and ms.source_dataset.lower() not in _existing_table_names
            and ms.source_dataset.lower() in wh_table_map
        ):
            wt = wh_table_map[ms.source_dataset.lower()]
            columns = _build_column_specs(wt)
            table_type = _map_table_type(wt.get("estimated_table_type", "unknown"), wt)

            # Only add the table if it's a fact table. If it's a dimension or unknown,
            # the compiler will silently drop the measure anyway, and the auto-added
            # dimension can trigger dim→dim relationships and ENTITY_ID null errors.
            if table_type != "fact":
                print(
                    f"  WARNING: Measure '{ms.name}' references '{ms.source_dataset}' "
                    f"which is classified as '{table_type}', not 'fact'. "
                    f"Dropping measure to prevent deployment errors."
                )
                measures_to_drop.add(ms.name)
                continue

            table_specs.append(TableSpec(
                name=wt["name"],
                schema_name=wt.get("schema", "public"),
                database_name=wt.get("database"),
                table_type=table_type,
                columns=columns,
            ))
            _existing_table_names.add(wt["name"].lower())

    # Also check measures whose source_dataset IS in table_specs but the table is not a fact
    for ms in measures:
        if ms.name in measures_to_drop:
            continue
        if ms.source_dataset:
            for ts in table_specs:
                if ts.name.lower() == ms.source_dataset.lower() and ts.table_type != "fact":
                    print(
                        f"  WARNING: Measure '{ms.name}' references '{ms.source_dataset}' "
                        f"which is classified as '{ts.table_type}', not 'fact'. "
                        f"Dropping measure to prevent deployment errors."
                    )
                    measures_to_drop.add(ms.name)
                    break

    if measures_to_drop:
        measures = [m for m in measures if m.name not in measures_to_drop]

    # Auto-include bridge tables the LLM may have missed.
    # Scans warehouse for tables with FK to a dimension that's already in the spec
    # AND business key columns matching fact tables in the spec.
    _auto_include_bridge_tables(
        table_specs=table_specs,
        relationships=relationships,
        wh_table_map=wh_table_map,
        existing_table_names=_existing_table_names,
    )

    _promote_bridge_tables(table_specs, relationships, measures, llm_classifications)

    # Ensure every table has at least one PK column. Bridge tables often lack a
    # single-column PK — mark their FK columns as composite primary key so the
    # Kyvos DRD and review validation pass.
    _ensure_pk_columns(table_specs, relationships)

    # Auto-detect missing relationships from warehouse FK metadata.
    # The LLM may include dimension tables in the model but forget to create
    # relationships connecting them to fact tables. Kyvos rejects dimensions
    # that have no path to any measure. This step scans FK columns in the
    # warehouse schema and auto-creates relationships for disconnected tables.
    relationships = _auto_detect_missing_relationships(
        relationships=relationships,
        table_specs=table_specs,
        wh_table_map=wh_table_map,
        measures=measures,
    )

    # Run bridge detection before the connectivity sweep so that bridge
    # tables get their table_type updated to "bridge". The sweep's BFS
    # includes edges from "bridge" type tables, so this ensures bridge
    # tables propagate connectivity to their associated dimensions.
    try:
        from kyvos_sm_skills.bridge_detector import detect_bridges
        _pre_sweep_result = detect_bridges(
            tables=table_specs,
            relationships=relationships,
            measures=measures,
            reclassify=False,
        )
        # detect_bridges mutates table_type on auto-detected bridges
        _bridge_lower = {n.lower() for n in _pre_sweep_result.bridge_names}
        _reclass_lower = {n.lower() for n in _pre_sweep_result.reclassified}
        for _t in table_specs:
            _sn = _t.name.lower()
            if _sn in _bridge_lower:
                _t.table_type = "bridge"
            elif _sn in _reclass_lower:
                _t.table_type = "dimension"
    except Exception:
        pass

    # Final connectivity sweep: remove any table not reachable from a fact
    # table via directed relationships. This is a safety net — the auto-detection
    # should catch most cases, but LLM-generated relationships may still leave
    # disconnected dimensions.
    table_specs, relationships, measures = _connectivity_sweep(
        table_specs=table_specs,
        relationships=relationships,
        measures=measures,
        wh_table_map=wh_table_map,
    )

    # Build HierarchySpec objects (filter out hierarchies referencing pruned tables)
    remaining_table_names = {ts.name.lower() for ts in table_specs}
    hierarchies = _build_hierarchies(
        sm_rec.get("hierarchies", []),
        wh_table_map,
    )
    hierarchies = [
        h for h in hierarchies
        if h.source_dataset.lower() in remaining_table_names
    ]

    # Kyvos constraint: a dimension with a parent-child hierarchy cannot have
    # multiple hierarchies. If the LLM generated more than one hierarchy for a
    # parent-child dimension, keep only the parent-child one.
    _pc_dims = {h.source_dataset.lower() for h in hierarchies if h.is_parent_child}
    if _pc_dims:
        _seen_pc = set()
        _filtered = []
        for h in hierarchies:
            src_lower = h.source_dataset.lower()
            if src_lower in _pc_dims:
                if h.is_parent_child and src_lower not in _seen_pc:
                    _filtered.append(h)
                    _seen_pc.add(src_lower)
                elif not h.is_parent_child:
                    print(
                        f"  Dropping non-parent-child hierarchy '{h.name}' on "
                        f"'{h.source_dataset}' — dimension has a parent-child hierarchy"
                    )
            else:
                _filtered.append(h)
        hierarchies = _filtered

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


def merge_specs_for_review(
    specs: list[tuple[dict[str, Any], DiscoveredSpec]],
) -> DiscoveredSpec:
    """Merge multiple per-SM DiscoveredSpecs into one for review display.

    Each SM gets its own ``DiscoveredSpec`` for deployment, but the review UI
    needs to show the union of all tables, relationships, measures, and
    hierarchies across all recommended SMs.

    Deduplication rules:
    - Tables: deduped by name (case-insensitive). Fact > bridge > dimension wins
      when the same table appears with different types.
    - Relationships: deduped by (left, right) dataset pair.
    - Measures/hierarchies: kept as-is; the ``associated_table`` /
      ``source_dataset`` fields disambiguate same-named entries across SMs.

    Args:
        specs: List of ``(sm_rec, spec)`` tuples, one per recommended SM.

    Returns:
        A merged ``DiscoveredSpec`` for review display only — NOT used for
        deployment (each SM deploys its own spec).
    """
    merged_tables: dict[str, TableSpec] = {}
    merged_datasets: dict[str, DatasetSpec] = {}
    merged_relationships: dict[tuple[str, str], RelationshipSpec] = {}
    merged_measures: list[MeasureSpec] = []
    merged_hierarchies: list[HierarchySpec] = []

    _type_rank = {"fact": 0, "bridge": 1, "snowflake_dimension": 2, "dimension": 3, "unknown": 4}

    for _sm_rec, spec in specs:
        # Tables — dedup by name, prefer higher-priority type
        for ts in spec.tables:
            key = ts.name.lower()
            existing = merged_tables.get(key)
            if existing is None or _type_rank.get(ts.table_type, 4) < _type_rank.get(existing.table_type, 4):
                merged_tables[key] = ts

        # Datasets — dedup by name
        for ds in spec.semantic_model.datasets:
            merged_datasets.setdefault(ds.name, ds)

        # Relationships — dedup by (left, right) pair
        for rel in spec.semantic_model.relationships:
            key = (rel.left_dataset.lower(), rel.right_dataset.lower())
            merged_relationships.setdefault(key, rel)

        # Measures and hierarchies — keep all (they're per-SM)
        merged_measures.extend(spec.semantic_model.measures)
        merged_hierarchies.extend(spec.semantic_model.hierarchies)

    return DiscoveredSpec(
        tables=list(merged_tables.values()),
        semantic_model=SemanticModelSpec(
            name=specs[0][1].semantic_model.name if specs else "MergedSM",
            datasets=list(merged_datasets.values()),
            relationships=list(merged_relationships.values()),
            measures=merged_measures,
            hierarchies=merged_hierarchies,
        ),
        metadata={
            "merged_sms": len(specs),
            "sm_names": [r.get("name", "unknown") for r, _ in specs],
            "source": "multi_sm_merge",
        },
    )


_BRIDGE_NAME_PATTERNS = (
    "reasons", "reason", "bridge", "junction", "xref", "crossref",
    "association", "assoc", "mapping", "linkage",
)


def _is_bridge_by_name(table_name: str) -> bool:
    """Check if a table name matches common bridge/junction table patterns."""
    lower = table_name.lower()
    return any(p in lower for p in _BRIDGE_NAME_PATTERNS)


def _auto_include_bridge_tables(
    table_specs: list[TableSpec],
    relationships: list[RelationshipSpec],
    wh_table_map: dict[str, dict[str, Any]],
    existing_table_names: set[str],
) -> None:
    """Auto-include bridge tables the LLM may have missed.

    Scans warehouse tables not already in the spec for bridge table patterns:
    - Has FK columns referencing a dimension table that IS in the spec
    - Has non-FK business key columns matching fact table columns in the spec
    - Or has a bridge-like name pattern with FK to a dimension in the spec

    When a bridge table is found, also adds its associated dimension table
    if not already in the spec, and creates the necessary relationships:
    fact_table → bridge_table (via matching business key)
    bridge_table → dimension_table (via FK)
    """
    spec_table_names = {ts.name.lower() for ts in table_specs}
    dim_names = {ts.name.lower() for ts in table_specs if ts.table_type == "dimension"}

    # Build fact table column sets for business key matching
    fact_columns: dict[str, set[str]] = {}
    for ts in table_specs:
        if ts.table_type == "fact":
            fact_columns[ts.name.lower()] = {c.name.lower() for c in (ts.columns or [])}

    # Build existing relationship pairs to avoid duplicates
    existing_rel_pairs: set[tuple[str, str]] = set()
    for rel in relationships:
        existing_rel_pairs.add((rel.left_dataset.lower(), rel.right_dataset.lower()))
        existing_rel_pairs.add((rel.right_dataset.lower(), rel.left_dataset.lower()))

    for wh_name, wh_table in wh_table_map.items():
        if wh_name in spec_table_names:
            continue

        # Never auto-include a table already classified as fact — it belongs
        # to a different SM, not to this one.
        if wh_table.get("estimated_table_type") == "fact":
            continue

        wt_columns = wh_table.get("columns", [])
        fk_cols = [c for c in wt_columns if c.get("is_fk")]
        non_fk_cols = [c for c in wt_columns if not c.get("is_fk") and not c.get("is_pk")]

        # Check if any FK references a dimension table — either one already in
        # the spec, or one in the warehouse that could be auto-included.
        referenced_dims: list[tuple[str, str, str]] = []  # (dim_name, fk_col, ref_col)
        for fk_col in fk_cols:
            ref = fk_col.get("references", "")
            if not ref:
                continue
            ref_table, ref_column = ref.rsplit(".", 1) if "." in ref else (ref, "")
            ref_table_lower = ref_table.lower()
            # Dimension already in spec, or dimension in warehouse not yet in spec
            if ref_table_lower in dim_names:
                referenced_dims.append((ref_table, fk_col["name"], ref_column))
            elif ref_table_lower in wh_table_map and ref_table_lower not in spec_table_names:
                # Check if the referenced table looks like a dimension (has PK, not a fact)
                ref_wt = wh_table_map.get(ref_table_lower, {})
                ref_estimated_type = ref_wt.get("estimated_table_type", "unknown")
                if ref_estimated_type in ("dimension", "unknown"):
                    referenced_dims.append((ref_table, fk_col["name"], ref_column))

        if not referenced_dims:
            continue

        # Check if non-FK columns match fact table columns (business key pattern)
        matching_facts: list[tuple[str, str, str]] = []  # (fact_name, fact_col, bridge_col)
        for fact_name, fact_cols in fact_columns.items():
            for nf_col in non_fk_cols:
                if nf_col["name"].lower() in fact_cols:
                    matching_facts.append((fact_name, nf_col["name"], nf_col["name"]))

        # Also check name-based heuristic
        name_match = _is_bridge_by_name(wh_table.get("name", wh_name))

        if not matching_facts and not name_match:
            continue

        # This is a bridge table — add it to the spec
        bridge_name = wh_table.get("name", wh_name)
        bridge_columns = _build_column_specs(wh_table)
        table_specs.append(TableSpec(
            name=bridge_name,
            schema_name=wh_table.get("schema", "public"),
            database_name=wh_table.get("database"),
            table_type="bridge",
            columns=bridge_columns,
        ))
        spec_table_names.add(bridge_name.lower())
        existing_table_names.add(bridge_name.lower())
        print(f"  Auto-included bridge table '{bridge_name}' from warehouse schema")

        # Add the associated dimension table if not already in spec
        for dim_name, fk_col_name, ref_col_name in referenced_dims:
            if dim_name.lower() not in spec_table_names:
                dim_wt = wh_table_map.get(dim_name.lower())
                if dim_wt:
                    dim_columns = _build_column_specs(dim_wt)
                    table_specs.append(TableSpec(
                        name=dim_wt.get("name", dim_name),
                        schema_name=dim_wt.get("schema", "public"),
                        database_name=dim_wt.get("database"),
                        table_type="dimension",
                        columns=dim_columns,
                    ))
                    spec_table_names.add(dim_wt.get("name", dim_name).lower())
                    existing_table_names.add(dim_wt.get("name", dim_name).lower())
                    print(f"  Auto-included dimension table '{dim_wt.get('name', dim_name)}' "
                          f"for bridge '{bridge_name}'")

            # Create relationship: bridge → dimension
            actual_dim_name = wh_table_map.get(dim_name.lower(), {}).get("name", dim_name)
            rel_key = (bridge_name.lower(), actual_dim_name.lower())
            if rel_key not in existing_rel_pairs:
                relationships.append(RelationshipSpec(
                    left_dataset=bridge_name,
                    left_column=fk_col_name,
                    right_dataset=actual_dim_name,
                    right_column=ref_col_name,
                    relationship_type="many_to_one",
                ))
                existing_rel_pairs.add(rel_key)
                existing_rel_pairs.add((actual_dim_name.lower(), bridge_name.lower()))
                print(f"  Auto-created relationship: {bridge_name}.{fk_col_name} "
                      f"-> {actual_dim_name}.{ref_col_name}")

        # Create relationships: fact → bridge (via matching business keys)
        for fact_name, fact_col, bridge_col in matching_facts:
            actual_fact_name = wh_table_map.get(fact_name.lower(), {}).get("name", fact_name)
            # Use the actual fact table name from the spec
            for ts in table_specs:
                if ts.name.lower() == fact_name.lower() and ts.table_type == "fact":
                    actual_fact_name = ts.name
                    break
            rel_key = (actual_fact_name.lower(), bridge_name.lower())
            if rel_key not in existing_rel_pairs:
                relationships.append(RelationshipSpec(
                    left_dataset=actual_fact_name,
                    left_column=fact_col,
                    right_dataset=bridge_name,
                    right_column=bridge_col,
                    relationship_type="many_to_one",
                ))
                existing_rel_pairs.add(rel_key)
                existing_rel_pairs.add((bridge_name.lower(), actual_fact_name.lower()))
                print(f"  Auto-created relationship: {actual_fact_name}.{fact_col} "
                      f"-> {bridge_name}.{bridge_col}")


def _promote_bridge_tables(
    table_specs: list[TableSpec],
    relationships: list[RelationshipSpec],
    measures: list[MeasureSpec],
    llm_classifications: dict[str, str] | None = None,
) -> None:
    """Promote dimensions to bridge only when the LLM did not already decide.

    Bridge tables must only be used for many-to-many relationships. This
    function keeps the conservative composite-PK and name-pattern heuristics
    as a fallback, but it NEVER overrides an explicit LLM classification.
    """
    table_by_name = {table.name.lower(): table for table in table_specs}
    fact_names = {
        table.name.lower() for table in table_specs if table.table_type == "fact"
    }
    measure_sources = {
        measure.source_dataset.lower()
        for measure in measures
        if measure.source_dataset
    }
    llm_classified = set((llm_classifications or {}).keys())
    candidates_with_fact_input: set[str] = set()

    for relationship in relationships:
        left = relationship.left_dataset.lower()
        right = relationship.right_dataset.lower()
        if left in fact_names and right in table_by_name:
            candidates_with_fact_input.add(right)

    # Composite PK detection: a table with 2+ PK columns where a fact relationship
    # only uses one PK column is a bridge table (e.g., SalesReasons has a composite
    # PK of salesordernumber + salesorderlinenumber + salesreasonkey, but the fact
    # table only joins on salesordernumber).
    for table_name in candidates_with_fact_input:
        if table_name in llm_classified:
            continue
        table = table_by_name.get(table_name)
        if table is None or table.table_type != "dimension":
            continue
        if table_name in measure_sources:
            continue
        pk_cols = {c.name.lower() for c in (table.columns or []) if c.is_primary_key}
        if len(pk_cols) >= 2:
            table.table_type = "bridge"
            print(f"  Promoted bridge table '{table.name}' from composite PK pattern "
                  f"(PK columns: {sorted(pk_cols)})")

    # Name-based heuristic: tables with common bridge/junction name patterns
    # that have incoming fact relationships and no measures.
    for table_name in candidates_with_fact_input:
        if table_name in llm_classified:
            continue
        table = table_by_name.get(table_name)
        if table is None or table.table_type != "dimension":
            continue
        if table_name in measure_sources:
            continue
        if _is_bridge_by_name(table.name):
            table.table_type = "bridge"
            print(f"  Promoted bridge table '{table.name}' from name pattern heuristic")


def _ensure_fact_measures(
    measures: list[MeasureSpec],
    table_specs: list[TableSpec],
) -> list[MeasureSpec]:
    """Ensure every fact table has at least one measure.

    Fact tables with no measures are unreachable by the connectivity sweep
    (they're not valid BFS starting points and are unreachable FROM other
    facts). This function adds a ``count(*)`` measure for each such table.
    """
    measure_sources = {
        m.source_dataset.lower() for m in measures if m.source_dataset
    }
    for ts in table_specs:
        if ts.table_type != "fact":
            continue
        if ts.name.lower() in measure_sources:
            continue
        # Find the PK column for a count measure (or first numeric column)
        pk_col = next(
            (c.name for c in ts.columns if c.is_primary_key), None
        )
        if not pk_col:
            # Fall back to first non-FK column
            pk_col = next(
                (c.name for c in ts.columns if not c.is_foreign_key), None
            )
        if not pk_col and ts.columns:
            pk_col = ts.columns[0].name
        if not pk_col:
            continue
        measure_name = f"{ts.name.replace('_', ' ').title()} Count"
        measures.append(MeasureSpec(
            name=measure_name,
            expression=pk_col,
            source_dataset=ts.name,
            aggregation_type="count",
            source_column=pk_col,
            is_calculated=False,
        ))
        print(
            f"  Auto-created count measure '{measure_name}' for fact table "
            f"'{ts.name}' (no measures assigned by LLM)"
        )
    return measures


def _ensure_pk_columns(
    table_specs: list[TableSpec],
    relationships: list[RelationshipSpec],
) -> None:
    """Ensure every table has at least one column marked as primary key.

    Bridge/junction tables often lack a single-column PK in the warehouse.
    For such tables, mark all columns that participate in relationships as
    ``is_primary_key=True`` — they form the composite key.
    """
    for ts in table_specs:
        if any(c.is_primary_key for c in ts.columns):
            continue  # Already has a PK
        # Collect column names used in relationships for this table
        join_cols: set[str] = set()
        for rel in relationships:
            if rel.left_dataset.lower() == ts.name.lower():
                join_cols.add(rel.left_column.lower())
            if rel.right_dataset.lower() == ts.name.lower():
                join_cols.add(rel.right_column.lower())
        marked = 0
        for col in ts.columns:
            if col.name.lower() in join_cols:
                col.is_primary_key = True
                col.nullable = False  # PK columns must be non-nullable
                marked += 1
        if marked:
            print(
                f"  Auto-assigned composite PK for '{ts.name}': "
                f"{sorted(join_cols)}"
            )
        elif ts.columns:
            # Fallback: mark the first column as PK so DRD validation passes.
            ts.columns[0].is_primary_key = True
            ts.columns[0].nullable = False
            print(
                f"  Auto-assigned PK for '{ts.name}': "
                f"'{ts.columns[0].name}' (no relationship columns found)"
            )


def _normalize_bridge_relationships(
    relationships: list[RelationshipSpec],
    wh_table_map: dict[str, dict[str, Any]],
) -> list[RelationshipSpec]:
    """Ensure bridge tables sit between facts and dimensions in directed edges.

    Kyvos connectivity and validation require every dimension to be reachable
    from a fact table with measures. For bridge/junction tables the expected
    direction is ``fact -> bridge -> dimension``. LLMs sometimes emit the
    opposite ``bridge -> fact`` edge, which leaves the bridge and its dimension
    disconnected from the measure group and causes SM validation errors such as
    *"Dimension ... is invalid because it does not have valid relation with any
    measure"*.

    The function flips relationships so that a bridge table is always:
    - on the RIGHT side of a relationship coming from a fact table
    - on the LEFT side of a relationship going to a dimension table
    """
    # Type lookup: prefer already-built table specs if passed, else warehouse metadata.
    def _type_of(name: str) -> str:
        wt = wh_table_map.get(name.lower())
        if wt is None:
            return ""
        et = wt.get("estimated_table_type", "").lower()
        if et in {"fact", "dimension", "bridge"}:
            return et
        # Name-based bridge fallback
        if _is_bridge_by_name(name):
            return "bridge"
        return ""

    normalized: list[RelationshipSpec] = []
    for rel in relationships:
        left_type = _type_of(rel.left_dataset)
        right_type = _type_of(rel.right_dataset)

        left_is_bridge = left_type == "bridge"
        right_is_bridge = right_type == "bridge"
        left_is_fact = left_type == "fact"
        right_is_fact = right_type == "fact"
        left_is_dim = left_type == "dimension"
        right_is_dim = right_type == "dimension"

        flip = False
        if right_is_bridge and left_is_fact:
            # fact -> bridge: correct
            pass
        elif left_is_bridge and right_is_fact:
            # bridge -> fact: flip to fact -> bridge
            flip = True
        elif left_is_bridge and right_is_dim:
            # bridge -> dimension: correct
            pass
        elif right_is_bridge and left_is_dim:
            # dimension -> bridge: flip to bridge -> dimension
            flip = True

        if flip:
            print(
                f"  Normalized bridge relationship: flipped "
                f"{rel.left_dataset}.{rel.left_column} -> {rel.right_dataset}.{rel.right_column} "
                f"to {rel.right_dataset}.{rel.right_column} -> {rel.left_dataset}.{rel.left_column}"
            )
            normalized.append(RelationshipSpec(
                left_dataset=rel.right_dataset,
                left_column=rel.right_column,
                right_dataset=rel.left_dataset,
                right_column=rel.left_column,
                relationship_type=rel.relationship_type,
            ))
        else:
            normalized.append(rel)

    return normalized


def _connectivity_sweep(
    table_specs: list[TableSpec],
    relationships: list[RelationshipSpec],
    measures: list[MeasureSpec],
    wh_table_map: dict[str, dict[str, Any]] | None = None,
    follow_all_edges: bool = False,
) -> tuple[list[TableSpec], list[RelationshipSpec], list[MeasureSpec]]:
    """Remove tables not reachable from any fact table with measures via directed relationships.

    Builds a directed graph from relationships (left → right), BFS from all
    fact tables that have at least one measure, and removes any table not
    reachable. Kyvos requires every dimension to have a directed path to a
    measure; a dimension connected only to a measure-less fact table will
    fail validation.

    Args:
        follow_all_edges: If True, follow all edges regardless of the left
            table's type (not just fact/bridge/unknown). Appropriate for PBIT
            flows where relationship directions may vary (e.g. dimension →
            bridge edges). Default False (discovery-flow behaviour).

    Returns:
        Trimmed (table_specs, relationships, measures) tuple.
    """
    if not table_specs:
        return table_specs, relationships, measures

    spec_table_names = {ts.name.lower() for ts in table_specs}
    table_type_map = {ts.name.lower(): ts.table_type for ts in table_specs}

    fact_table_names: set[str] = set()
    for ts in table_specs:
        if ts.table_type == "fact":
            fact_table_names.add(ts.name.lower())

    if not fact_table_names:
        return table_specs, relationships, measures

    # Only BFS from fact tables that have at least one measure.
    # Kyvos requires every dimension to have a directed path to a measure.
    measure_dataset_names = {m.source_dataset.lower() for m in measures if m.source_dataset}
    fact_table_names_with_measures: set[str] = set()
    for ts in table_specs:
        if ts.table_type == "fact" and ts.name.lower() in measure_dataset_names:
            fact_table_names_with_measures.add(ts.name.lower())

    # Fall back to all fact tables if none have measures (edge case)
    if not fact_table_names_with_measures:
        fact_table_names_with_measures = fact_table_names

    # Build directed graph (left → right).
    # In a snowflake schema dimensions reference other dimensions, so once a
    # dimension is reached from a fact we must continue traversing through it.
    directed_graph: dict[str, set[str]] = {ts.name.lower(): set() for ts in table_specs}
    for rel in relationships:
        left = rel.left_dataset.lower()
        right = rel.right_dataset.lower()
        if left not in directed_graph or right not in directed_graph:
            continue
        directed_graph[left].add(right)

    # Directed BFS from fact tables that have measures
    connected: set[str] = set()
    queue = list(fact_table_names_with_measures)
    while queue:
        current = queue.pop(0)
        if current in connected:
            continue
        connected.add(current)
        for neighbor in directed_graph.get(current, set()):
            if neighbor not in connected:
                queue.append(neighbor)

    disconnected = spec_table_names - connected
    if not disconnected:
        return table_specs, relationships, measures

    # Before removing disconnected tables, try to reconnect them to fact
    # tables WITH measures via exact column name matching. This handles the
    # case where a bridge table was connected to a fact table without measures
    # (which the sweep will remove). For example, sales_reasons may be
    # connected to internet_customers (no measures) but also shares
    # salesordernumber with internet_sales (has measures).
    measure_dataset_names = {m.source_dataset.lower() for m in measures if m.source_dataset}
    fact_with_measures = {
        ts.name.lower() for ts in table_specs
        if ts.table_type == "fact" and ts.name.lower() in measure_dataset_names
    }
    if fact_with_measures and disconnected and wh_table_map:
        # Build a set of existing relationship keys to avoid duplicates
        _existing_rel_keys: set[tuple[str, str, str, str]] = set()
        _existing_table_pairs: set[tuple[str, str]] = set()
        for rel in relationships:
            left_ds = rel.left_dataset.lower()
            right_ds = rel.right_dataset.lower()
            _existing_rel_keys.add((left_ds, rel.left_column.lower(), right_ds, rel.right_column.lower()))
            _existing_rel_keys.add((right_ds, rel.right_column.lower(), left_ds, rel.left_column.lower()))
            _existing_table_pairs.add((left_ds, right_ds))
            _existing_table_pairs.add((right_ds, left_ds))

        _new_rels: list[RelationshipSpec] = []
        _reconnected: set[str] = set()
        for disc in sorted(disconnected):
            disc_wt = wh_table_map.get(disc)
            if not disc_wt:
                continue
            # Fact→fact guard: never reconnect a fact table to another fact table.
            if table_type_map.get(disc) == "fact":
                continue
            disc_cols = disc_wt.get("columns", [])
            for fwm in sorted(fact_with_measures):
                if fwm == disc or (fwm, disc) in _existing_table_pairs:
                    continue
                fwm_wt = wh_table_map.get(fwm)
                if not fwm_wt:
                    continue
                # Try exact column name match
                fwm_col_names = {c["name"].lower(): c for c in fwm_wt.get("columns", [])}
                _found_match = False
                for disc_col in disc_cols:
                    disc_col_lower = disc_col["name"].lower()
                    if disc_col_lower not in fwm_col_names:
                        continue
                    # Type compatibility check
                    disc_col_type = disc_col.get("data_type", "").upper()
                    fwm_col_type = fwm_col_names[disc_col_lower].get("data_type", "").upper()
                    _disc_is_int = "INT" in disc_col_type
                    _fwm_is_date = "DATE" in fwm_col_type
                    _fwm_is_int = "INT" in fwm_col_type
                    _disc_is_date = "DATE" in disc_col_type
                    if (_fwm_is_date and _disc_is_int) or (_fwm_is_int and _disc_is_date):
                        continue
                    rel_key = (fwm, disc_col_lower, disc, disc_col_lower)
                    if rel_key in _existing_rel_keys:
                        continue
                    actual_from = fwm_wt["name"]
                    actual_to = disc_wt["name"]
                    actual_from_col = fwm_col_names[disc_col_lower]["name"]
                    actual_to_col = disc_col["name"]
                    _new_rels.append(RelationshipSpec(
                        left_dataset=actual_from,
                        left_column=actual_from_col,
                        right_dataset=actual_to,
                        right_column=actual_to_col,
                        relationship_type="many_to_one",
                    ))
                    _existing_rel_keys.add(rel_key)
                    _existing_rel_keys.add((disc, disc_col_lower, fwm, disc_col_lower))
                    _existing_table_pairs.add((fwm, disc))
                    _existing_table_pairs.add((disc, fwm))
                    print(f"  Reconnect (sweep): {actual_from}.{actual_from_col} -> {actual_to}.{actual_to_col}")
                    _reconnected.add(disc)
                    break  # One connection is enough

        if _new_rels:
            relationships = relationships + _new_rels
            # Rebuild directed graph and redo BFS
            directed_graph = {ts.name.lower(): set() for ts in table_specs}
            for rel in relationships:
                left = rel.left_dataset.lower()
                right = rel.right_dataset.lower()
                if (
                    left in directed_graph
                    and right in directed_graph
                    and table_type_map.get(left) in {"fact", "bridge", "unknown", ""}
                ):
                    directed_graph[left].add(right)
            connected = set()
            queue = list(fact_table_names_with_measures)
            while queue:
                current = queue.pop(0)
                if current in connected:
                    continue
                connected.add(current)
                for neighbor in directed_graph.get(current, set()):
                    if neighbor not in connected:
                        queue.append(neighbor)
            disconnected = spec_table_names - connected
            if _reconnected:
                print(f"  Reconnected {len(_reconnected)} table(s) to fact tables with measures")

    if not disconnected:
        return table_specs, relationships, measures

    # Remove disconnected tables, their relationships, and their measures
    for disc in sorted(disconnected):
        print(
            f"  WARNING: Removing disconnected table '{disc}' — "
            f"no directed path from any fact table with measures."
        )

    table_specs = [ts for ts in table_specs if ts.name.lower() not in disconnected]
    relationships = [
        rel for rel in relationships
        if rel.left_dataset.lower() not in disconnected
        and rel.right_dataset.lower() not in disconnected
    ]
    measures = [
        m for m in measures
        if not m.source_dataset or m.source_dataset.lower() not in disconnected
    ]

    return table_specs, relationships, measures


def _auto_detect_missing_relationships(
    relationships: list[RelationshipSpec],
    table_specs: list[TableSpec],
    wh_table_map: dict[str, dict[str, Any]],
    measures: list[MeasureSpec],
) -> list[RelationshipSpec]:
    """Auto-create relationships for tables disconnected from all fact tables.

    After the LLM produces relationships, some dimension tables may be included
    in the model but have no **directed** relationship path from a fact table.
    Kyvos DRD uses directed edges (left → right) and rejects dimensions that
    cannot be reached from any measure via directed traversal.

    This function:

    1. Reorients wrong-direction relationships (dim → fact becomes fact → dim).
    2. Builds a **directed** graph from relationships (left → right).
    3. Identifies fact tables (table_type == 'fact' or tables with measures).
    4. Directed BFS from all fact tables to find reachable tables.
    5. For each unreachable table, scans FK column metadata in the warehouse
       schema to find connections and auto-creates relationships with the
       correct direction (connected_table → disconnected_table).

    Args:
        relationships: Existing RelationshipSpec list from LLM recommendation.
        table_specs: All TableSpec objects in the spec.
        wh_table_map: Warehouse table lookup (lowercase name -> table dict).
        measures: MeasureSpec list (to identify which tables have measures).

    Returns:
        Extended relationships list with reoriented + auto-detected relationships.
    """
    if not table_specs:
        return relationships

    spec_table_names = {ts.name.lower() for ts in table_specs}
    table_type_map = {ts.name.lower(): ts.table_type for ts in table_specs}

    # Identify fact tables: only table_type == "fact".
    # Do NOT add tables with measures but non-fact table_type — those are
    # dimensions that the LLM incorrectly assigned measures to. The DRD
    # generator also uses only table_type == "fact", so we must match.
    fact_table_names: set[str] = set()
    for ts in table_specs:
        if ts.table_type == "fact":
            fact_table_names.add(ts.name.lower())

    if not fact_table_names:
        return relationships  # No fact tables -- nothing to connect to

    # Step 1: Reorient wrong-direction dim → fact relationships to fact → dim.
    # The DRD uses directed edges (left → right). A fact should be on the left
    # so that dimensions are reachable from fact tables via directed traversal.
    reoriented: list[RelationshipSpec] = []
    reorient_count = 0
    for rel in relationships:
        left = rel.left_dataset.lower()
        right = rel.right_dataset.lower()
        left_is_fact = left in fact_table_names
        right_is_fact = right in fact_table_names
        left_is_dim = not left_is_fact

        if left_is_dim and right_is_fact:
            # Wrong direction: dim → fact. Flip to fact → dim.
            reoriented.append(RelationshipSpec(
                left_dataset=rel.right_dataset,
                left_column=rel.right_column,
                right_dataset=rel.left_dataset,
                right_column=rel.left_column,
                relationship_type=rel.relationship_type,
            ))
            reorient_count += 1
            print(
                f"  Reoriented relationship: {rel.left_dataset}.{rel.left_column} "
                f"-> {rel.right_dataset}.{rel.right_column}  =>  "
                f"{rel.right_dataset}.{rel.right_column} -> {rel.left_dataset}.{rel.left_column}"
            )
        else:
            reoriented.append(rel)

    if reorient_count > 0:
        print(f"  Reoriented {reorient_count} wrong-direction dim→fact relationship(s)")

    # Step 2: Build directed graph (left → right only)
    directed_graph: dict[str, set[str]] = {ts.name.lower(): set() for ts in table_specs}
    for rel in reoriented:
        left = rel.left_dataset.lower()
        right = rel.right_dataset.lower()
        if left in directed_graph and right in directed_graph:
            directed_graph[left].add(right)

    # Step 3: Directed BFS from all fact tables
    connected: set[str] = set()
    queue = list(fact_table_names)
    while queue:
        current = queue.pop(0)
        if current in connected:
            continue
        connected.add(current)
        for neighbor in directed_graph.get(current, set()):
            if neighbor not in connected:
                queue.append(neighbor)

    # Step 3b: Reorient dim→dim relationships where left is disconnected
    # and right is connected. These relationships point the wrong way for
    # directed traversal (disconnected → connected should be connected → disconnected).
    initial_disconnected = spec_table_names - connected
    if initial_disconnected:
        reoriented_2: list[RelationshipSpec] = []
        reorient_2_count = 0
        for rel in reoriented:
            left = rel.left_dataset.lower()
            right = rel.right_dataset.lower()
            if (left in initial_disconnected and right in connected
                    and left not in fact_table_names and right not in fact_table_names):
                # Wrong direction: disconnected → connected. Flip to connected → disconnected.
                reoriented_2.append(RelationshipSpec(
                    left_dataset=rel.right_dataset,
                    left_column=rel.right_column,
                    right_dataset=rel.left_dataset,
                    right_column=rel.left_column,
                    relationship_type=rel.relationship_type,
                ))
                reorient_2_count += 1
                print(
                    f"  Reoriented dim→dim: {rel.left_dataset}.{rel.left_column} "
                    f"-> {rel.right_dataset}.{rel.right_column}  =>  "
                    f"{rel.right_dataset}.{rel.right_column} -> {rel.left_dataset}.{rel.left_column}"
                )
            else:
                reoriented_2.append(rel)

        if reorient_2_count > 0:
            print(f"  Reoriented {reorient_2_count} wrong-direction dim→dim relationship(s)")
            reoriented = reoriented_2

            # Rebuild directed graph with reoriented relationships
            directed_graph = {ts.name.lower(): set() for ts in table_specs}
            for rel in reoriented:
                left = rel.left_dataset.lower()
                right = rel.right_dataset.lower()
                if left in directed_graph and right in directed_graph:
                    directed_graph[left].add(right)

            # Redo BFS
            connected = set()
            queue = list(fact_table_names)
            while queue:
                current = queue.pop(0)
                if current in connected:
                    continue
                connected.add(current)
                for neighbor in directed_graph.get(current, set()):
                    if neighbor not in connected:
                        queue.append(neighbor)

    # Find disconnected tables (in spec but not reachable from any fact table)
    disconnected = spec_table_names - connected
    if not disconnected:
        return reoriented

    # Debug: print graph state for diagnosis
    print(f"  Auto-detect debug: fact_tables={sorted(fact_table_names)}")
    print(f"  Auto-detect debug: connected={sorted(connected)}")
    print(f"  Auto-detect debug: disconnected={sorted(disconnected)}")
    for disc in sorted(disconnected):
        edges_out = directed_graph.get(disc, set())
        edges_in = {k for k, v in directed_graph.items() if disc in v}
        print(f"    {disc}: edges_out={sorted(edges_out)} edges_in={sorted(edges_in)}")
        # Show FK info from warehouse
        disc_wt_debug = wh_table_map.get(disc)
        if disc_wt_debug:
            for col in disc_wt_debug.get("columns", []):
                if col.get("is_fk"):
                    print(f"      FK: {col['name']} -> {col.get('references')}")

    # Build a set of existing relationship keys to avoid duplicates
    existing_rel_keys: set[tuple[str, str, str, str]] = set()
    existing_table_pairs: set[tuple[str, str]] = set()
    for rel in reoriented:
        key = (
            rel.left_dataset.lower(),
            rel.left_column.lower(),
            rel.right_dataset.lower(),
            rel.right_column.lower(),
        )
        existing_rel_keys.add(key)
        existing_rel_keys.add(
            (rel.right_dataset.lower(), rel.right_column.lower(),
             rel.left_dataset.lower(), rel.left_column.lower())
        )
        # Track table pairs (both directions) to prevent conflicting reverse relationships
        left_t = rel.left_dataset.lower()
        right_t = rel.right_dataset.lower()
        existing_table_pairs.add((left_t, right_t))
        existing_table_pairs.add((right_t, left_t))

    new_relationships: list[RelationshipSpec] = []

    # Helper: check if a table is a fact or bridge (eligible to be on the left
    # side of a relationship that connects a dimension).
    def _is_fact_or_bridge(name_lower: str) -> bool:
        tt = table_type_map.get(name_lower, "dimension")
        return tt in ("fact", "bridge")

    # Iteratively connect disconnected tables: after adding a relationship,
    # the connected set may grow, allowing further connections.
    while disconnected:
        progress_made = False

        for disc_table in sorted(disconnected):
            disc_wt = wh_table_map.get(disc_table)
            if not disc_wt:
                continue

            # Skip dim→dim auto-detection: if the disconnected table is a
            # dimension (not fact/bridge), only connect it to a fact or bridge
            # table. Connecting two dimensions creates edges that cause cube
            # build failures (ENTITY_ID null errors).
            disc_is_dim = not _is_fact_or_bridge(disc_table)
            # Fact-to-fact guard: a fact table must not be auto-connected to
            # another fact table.
            disc_is_fact = table_type_map.get(disc_table) == "fact"

            connected_to_this = False

            # Strategy 1: Scan FK columns in the disconnected table itself.
            # If the FK points to a connected table, create:
            #   connected_table → disconnected_table  (so directed BFS reaches it)
            for col in disc_wt.get("columns", []):
                if not col.get("is_fk") or not col.get("references"):
                    continue
                ref = col["references"]
                if "." not in ref:
                    continue
                ref_table, ref_column = ref.rsplit(".", 1)
                ref_table_lower = ref_table.lower()

                if ref_table_lower not in spec_table_names:
                    continue
                if ref_table_lower == disc_table:
                    continue

                # Only connect if the referenced table is already connected
                # (so the directed path from fact → ... → ref_table → disc_table works)
                if ref_table_lower not in connected:
                    continue
                # Dim→dim guard: if the disconnected table is a dimension,
                # only connect it to a fact or bridge table.
                if disc_is_dim and not _is_fact_or_bridge(ref_table_lower):
                    continue
                # Fact→fact guard: never auto-connect two fact tables.
                if disc_is_fact and table_type_map.get(ref_table_lower) == "fact":
                    continue
                # Skip if there's already a relationship between these tables
                if (ref_table_lower, disc_table) in existing_table_pairs:
                    continue

                ref_wt = wh_table_map.get(ref_table_lower)
                if not ref_wt:
                    continue
                ref_cols = {c["name"].lower() for c in ref_wt.get("columns", [])}
                if ref_column.lower() not in ref_cols:
                    continue

                # Relationship direction: ref_table (connected) → disc_table (disconnected)
                rel_key = (ref_table_lower, ref_column.lower(), disc_table, col["name"].lower())
                if rel_key in existing_rel_keys:
                    continue

                # Type compatibility check
                from_col_type = next(
                    (c.get("data_type", "") for c in ref_wt.get("columns", [])
                     if c["name"].lower() == ref_column.lower()), ""
                ).upper()
                to_col_type = col.get("data_type", "").upper()
                _from_is_date = "DATE" in from_col_type
                _to_is_int = "INT" in to_col_type
                _from_is_int = "INT" in from_col_type
                _to_is_date = "DATE" in to_col_type
                if (_from_is_date and _to_is_int) or (_from_is_int and _to_is_date):
                    continue

                actual_from = ref_wt["name"]
                actual_to = disc_wt["name"]
                actual_from_col = next(
                    (c["name"] for c in ref_wt.get("columns", [])
                     if c["name"].lower() == ref_column.lower()), ref_column
                )
                actual_to_col = col["name"]

                new_relationships.append(RelationshipSpec(
                    left_dataset=actual_from,
                    left_column=actual_from_col,
                    right_dataset=actual_to,
                    right_column=actual_to_col,
                    relationship_type="many_to_one",
                ))
                existing_rel_keys.add(rel_key)
                existing_rel_keys.add((disc_table, col["name"].lower(), ref_table_lower, ref_column.lower()))
                existing_table_pairs.add((ref_table_lower, disc_table))
                existing_table_pairs.add((disc_table, ref_table_lower))
                print(
                    f"  Auto-detected relationship: {actual_from}.{actual_from_col} "
                    f"-> {actual_to}.{actual_to_col}"
                )
                connected.add(disc_table)
                directed_graph[ref_table_lower].add(disc_table)
                progress_made = True
                connected_to_this = True
                break  # One connection is enough for this table

            if connected_to_this:
                continue

            # Strategy 2: Scan connected tables for FKs pointing TO the disconnected table.
            # If a connected table has an FK to the disconnected table, create:
            #   connected_table → disconnected_table
            for conn_table in sorted(connected):
                if conn_table == disc_table:
                    continue
                # Dim→dim guard: if the disconnected table is a dimension,
                # only connect it from a fact or bridge table.
                if disc_is_dim and not _is_fact_or_bridge(conn_table):
                    continue
                # Fact→fact guard: never auto-connect two fact tables.
                if disc_is_fact and table_type_map.get(conn_table) == "fact":
                    continue
                # Skip if there's already a relationship between these tables
                if (conn_table, disc_table) in existing_table_pairs:
                    continue
                conn_wt = wh_table_map.get(conn_table)
                if not conn_wt:
                    continue
                for col in conn_wt.get("columns", []):
                    if not col.get("is_fk") or not col.get("references"):
                        continue
                    ref = col["references"]
                    if "." not in ref:
                        continue
                    ref_table, ref_column = ref.rsplit(".", 1)
                    if ref_table.lower() != disc_table:
                        continue

                    disc_cols = {c["name"].lower() for c in disc_wt.get("columns", [])}
                    if ref_column.lower() not in disc_cols:
                        continue

                    rel_key = (conn_table, col["name"].lower(), disc_table, ref_column.lower())
                    if rel_key in existing_rel_keys:
                        continue

                    # Type compatibility check
                    from_col_type = col.get("data_type", "").upper()
                    to_col_type = next(
                        (c.get("data_type", "") for c in disc_wt.get("columns", [])
                         if c["name"].lower() == ref_column.lower()), ""
                    ).upper()
                    _from_is_date = "DATE" in from_col_type
                    _to_is_int = "INT" in to_col_type
                    _from_is_int = "INT" in from_col_type
                    _to_is_date = "DATE" in to_col_type
                    if (_from_is_date and _to_is_int) or (_from_is_int and _to_is_date):
                        continue

                    actual_from = conn_wt["name"]
                    actual_to = disc_wt["name"]
                    actual_from_col = col["name"]
                    actual_to_col = next(
                        (c["name"] for c in disc_wt.get("columns", [])
                         if c["name"].lower() == ref_column.lower()), ref_column
                    )

                    new_relationships.append(RelationshipSpec(
                        left_dataset=actual_from,
                        left_column=actual_from_col,
                        right_dataset=actual_to,
                        right_column=actual_to_col,
                        relationship_type="many_to_one",
                    ))
                    existing_rel_keys.add(rel_key)
                    existing_rel_keys.add((disc_table, ref_column.lower(), conn_table, col["name"].lower()))
                    existing_table_pairs.add((conn_table, disc_table))
                    existing_table_pairs.add((disc_table, conn_table))
                    print(
                        f"  Auto-detected relationship: {actual_from}.{actual_from_col} "
                        f"-> {actual_to}.{actual_to_col}"
                    )
                    connected.add(disc_table)
                    directed_graph[conn_table].add(disc_table)
                    progress_made = True
                    connected_to_this = True
                    break  # One connection is enough

                if connected_to_this:
                    break

            if connected_to_this:
                continue

            # Strategy 3: PK column name matching (no FK metadata available).
            # For role-playing dimensions and tables without FK constraints,
            # match fact table columns that contain the dimension's PK column
            # name as a suffix (e.g., date_key on date table matches
            # order_date_key, ship_date_key, due_date_key on fact tables).
            disc_pk_cols = [
                c for c in disc_wt.get("columns", []) if c.get("is_pk")
            ]
            if not disc_pk_cols:
                # Fall back to columns ending in "_key" or "key"
                disc_pk_cols = [
                    c for c in disc_wt.get("columns", [])
                    if c["name"].lower().endswith("key") or c["name"].lower().endswith("_id")
                ]

            for pk_col in disc_pk_cols:
                pk_name_lower = pk_col["name"].lower()
                pk_type = pk_col.get("data_type", "").upper()

                for conn_table in sorted(connected):
                    if conn_table == disc_table:
                        continue
                    # Dim→dim guard: if the disconnected table is a dimension,
                    # only connect it from a fact or bridge table.
                    if disc_is_dim and not _is_fact_or_bridge(conn_table):
                        continue
                    # Fact→fact guard: never auto-connect two fact tables.
                    if disc_is_fact and table_type_map.get(conn_table) == "fact":
                        continue
                    # Skip if there's already a relationship between these tables
                    if (conn_table, disc_table) in existing_table_pairs:
                        continue
                    conn_wt = wh_table_map.get(conn_table)
                    if not conn_wt:
                        continue

                    for conn_col in conn_wt.get("columns", []):
                        conn_col_lower = conn_col["name"].lower()
                        # Match if the connected table's column ends with
                        # the PK column name (e.g., "order_date_key" ends with "date_key")
                        # or matches exactly
                        if conn_col_lower == pk_name_lower or conn_col_lower.endswith("_" + pk_name_lower):
                            # Skip if already an FK with a different reference
                            if conn_col.get("is_fk") and conn_col.get("references"):
                                ref = conn_col["references"]
                                if "." in ref:
                                    ref_table, _ = ref.rsplit(".", 1)
                                    if ref_table.lower() != disc_table:
                                        continue  # FK points elsewhere

                            # Type compatibility check
                            conn_col_type = conn_col.get("data_type", "").upper()
                            _from_is_date = "DATE" in conn_col_type
                            _to_is_int = "INT" in pk_type
                            _from_is_int = "INT" in conn_col_type
                            _to_is_date = "DATE" in pk_type
                            if (_from_is_date and _to_is_int) or (_from_is_int and _to_is_date):
                                continue

                            rel_key = (conn_table, conn_col_lower, disc_table, pk_name_lower)
                            if rel_key in existing_rel_keys:
                                continue

                            actual_from = conn_wt["name"]
                            actual_to = disc_wt["name"]
                            actual_from_col = conn_col["name"]
                            actual_to_col = pk_col["name"]

                            new_relationships.append(RelationshipSpec(
                                left_dataset=actual_from,
                                left_column=actual_from_col,
                                right_dataset=actual_to,
                                right_column=actual_to_col,
                                relationship_type="many_to_one",
                            ))
                            existing_rel_keys.add(rel_key)
                            existing_rel_keys.add((disc_table, pk_name_lower, conn_table, conn_col_lower))
                            existing_table_pairs.add((conn_table, disc_table))
                            existing_table_pairs.add((disc_table, conn_table))
                            print(
                                f"  Auto-detected relationship (PK match): {actual_from}.{actual_from_col} "
                                f"-> {actual_to}.{actual_to_col}"
                            )
                            connected.add(disc_table)
                            directed_graph[conn_table].add(disc_table)
                            progress_made = True
                            connected_to_this = True
                            break

                    if connected_to_this:
                        break

            if connected_to_this:
                continue

            # Strategy 4: Exact column name matching for all columns.
            # For bridge tables and other tables without PK/FK metadata,
            # try matching any column name that exists in both the disconnected
            # table and a connected table (e.g., salesordernumber in both
            # sales_reasons and internet_sales).
            disc_cols_all = disc_wt.get("columns", [])
            for disc_col in disc_cols_all:
                disc_col_lower = disc_col["name"].lower()
                disc_col_type = disc_col.get("data_type", "").upper()

                for conn_table in sorted(connected):
                    if conn_table == disc_table:
                        continue
                    # Dim→dim guard: if the disconnected table is a dimension,
                    # only connect it from a fact or bridge table.
                    if disc_is_dim and not _is_fact_or_bridge(conn_table):
                        continue
                    # Fact→fact guard: never auto-connect two fact tables.
                    if disc_is_fact and table_type_map.get(conn_table) == "fact":
                        continue
                    # Skip if there's already a relationship between these tables
                    if (conn_table, disc_table) in existing_table_pairs:
                        continue
                    conn_wt = wh_table_map.get(conn_table)
                    if not conn_wt:
                        continue

                    for conn_col in conn_wt.get("columns", []):
                        conn_col_lower = conn_col["name"].lower()
                        if conn_col_lower != disc_col_lower:
                            continue

                        # Skip if already an FK with a different reference
                        if conn_col.get("is_fk") and conn_col.get("references"):
                            ref = conn_col["references"]
                            if "." in ref:
                                ref_table, _ = ref.rsplit(".", 1)
                                if ref_table.lower() != disc_table:
                                    continue

                        # Type compatibility check
                        conn_col_type = conn_col.get("data_type", "").upper()
                        _from_is_date = "DATE" in conn_col_type
                        _to_is_int = "INT" in disc_col_type
                        _from_is_int = "INT" in conn_col_type
                        _to_is_date = "DATE" in disc_col_type
                        if (_from_is_date and _to_is_int) or (_from_is_int and _to_is_date):
                            continue

                        rel_key = (conn_table, conn_col_lower, disc_table, disc_col_lower)
                        if rel_key in existing_rel_keys:
                            continue

                        actual_from = conn_wt["name"]
                        actual_to = disc_wt["name"]
                        actual_from_col = conn_col["name"]
                        actual_to_col = disc_col["name"]

                        new_relationships.append(RelationshipSpec(
                            left_dataset=actual_from,
                            left_column=actual_from_col,
                            right_dataset=actual_to,
                            right_column=actual_to_col,
                            relationship_type="many_to_one",
                        ))
                        existing_rel_keys.add(rel_key)
                        existing_rel_keys.add((disc_table, disc_col_lower, conn_table, conn_col_lower))
                        existing_table_pairs.add((conn_table, disc_table))
                        existing_table_pairs.add((disc_table, conn_table))
                        print(
                            f"  Auto-detected relationship (col match): {actual_from}.{actual_from_col} "
                            f"-> {actual_to}.{actual_to_col}"
                        )
                        connected.add(disc_table)
                        directed_graph[conn_table].add(disc_table)
                        progress_made = True
                        connected_to_this = True
                        break

                    if connected_to_this:
                        break

                if connected_to_this:
                    break

        # Propagate connectivity through existing directed edges (BFS)
        # When a new table is connected, tables reachable from it via existing
        # edges should also become connected (e.g., sales_reasons → sales_reason).
        queue = list(connected)
        while queue:
            current = queue.pop(0)
            for neighbor in directed_graph.get(current, set()):
                if neighbor not in connected:
                    connected.add(neighbor)
                    queue.append(neighbor)

        # Update disconnected set
        disconnected = spec_table_names - connected
        if not progress_made:
            break  # No more connections can be made

    if new_relationships:
        print(
            f"  Auto-detected {len(new_relationships)} missing relationship(s) "
            f"from warehouse FK metadata"
        )

    # Report any tables that remain disconnected
    if disconnected:
        print(
            f"  WARNING: {len(disconnected)} table(s) still disconnected from fact tables: "
            f"{sorted(disconnected)}"
        )

    return reoriented + new_relationships


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


def _map_table_type(estimated_type: str, wt: dict[str, Any] | None = None) -> str:
    """Map warehouse inspector's estimated type to TableSpec.table_type.

    When ``estimated_type`` is ``"unknown"``, applies heuristics from the XMLA
    parser to classify the table:

    - outgoing FK count ≥ 3 → ``fact``
    - all columns numeric and ≥ 5 columns → ``fact``
    - otherwise → ``dimension``
    """
    mapping = {
        "fact": "fact",
        "dimension": "dimension",
        "bridge": "bridge",
    }
    if estimated_type in mapping:
        return mapping[estimated_type]

    # Unknown — apply heuristics if warehouse table metadata is available
    if wt is not None:
        cols = wt.get("columns", [])
        outgoing_fk_count = sum(1 for c in cols if c.get("is_fk"))
        if outgoing_fk_count >= 3:
            return "fact"
        if len(cols) >= 5:
            numeric_markers = ("INT", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "BIGINT", "SMALLINT")
            all_numeric = all(
                any(m in str(c.get("data_type", "")).upper() for m in numeric_markers)
                for c in cols
            )
            if all_numeric:
                return "fact"

    return "dimension"


def _build_relationships(
    rels: list[dict[str, Any]],
    wh_table_map: dict[str, dict[str, Any]],
    table_type_overrides: dict[str, str] | None = None,
) -> list[RelationshipSpec]:
    """Convert relationship dicts to RelationshipSpec objects.

    Validates that referenced tables and columns exist in the warehouse schema.
    Rejects fact-to-fact joins.

    Args:
        rels: Relationship dicts from the LLM recommendation.
        wh_table_map: Warehouse table lookup (lowercase name -> table dict).
        table_type_overrides: Optional dict mapping lowercase table name to
            table type (from LLM table_classifications). When provided, these
            take precedence over warehouse estimated_table_type for the
            fact-to-fact check.
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
        from_col_type = next(
            (c.get("data_type", "") for c in from_cols_list
             if c["name"].lower() == from_column.lower()), "",
        )
        to_col_type = next(
            (c.get("data_type", "") for c in to_cols_list
             if c["name"].lower() == to_column.lower()), "",
        )
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

        # Skip fact-to-fact relationships — Kyvos does not allow direct joins
        # between fact tables. They must connect through shared dimensions.
        def _get_table_type(name: str) -> str:
            if table_type_overrides and name.lower() in table_type_overrides:
                return table_type_overrides[name.lower()]
            wt = wh_table_map.get(name.lower())
            return wt.get("estimated_table_type", "unknown") if wt else "unknown"

        from_type = _get_table_type(from_table)
        to_type = _get_table_type(to_table)
        if from_type == "fact" and to_type == "fact":
            print(
                f"  WARNING: Skipping fact-to-fact relationship: "
                f"{from_table}.{from_column} -> {to_table}.{to_column}. "
                f"Fact tables must connect through shared dimensions, not directly."
            )
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
        agg_type = m.get("aggregation_type") or "sum"

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

        # Validate source_column for base (non-calculated) measures
        if not is_calculated and actual_source:
            if not source_column:
                # LLM hallucinated a measure for a column that doesn't exist
                print(f"  WARNING: Skipping measure '{name}' — no matching column found on '{actual_source}'")
                continue
            # Validate source_column exists and check data type compatibility
            if source_dataset:
                cols = wh_table_map[source_dataset.lower()].get("columns", [])
                col_lookup = {c["name"].lower(): c for c in cols}
                matched_col = col_lookup.get(source_column.lower())
                if not matched_col:
                    print(
                        f"  WARNING: Skipping measure '{name}' — "
                        f"source_column '{source_column}' not found on '{actual_source}'"
                    )
                    continue
                _numeric_aggs = {"sum", "avg", "min", "max", "median", "stdev", "var"}
                if agg_type.lower() in _numeric_aggs:
                    col_type = str(matched_col.get("data_type", "")).upper()
                    _numeric_type_markers = (
                        "INT", "NUMERIC", "DECIMAL", "FLOAT",
                        "DOUBLE", "REAL", "BIGINT", "SMALLINT", "SERIAL",
                    )
                    if not any(t in col_type for t in _numeric_type_markers):
                        print(
                            f"  WARNING: Skipping measure '{name}' — aggregation '{agg_type}' "
                            f"on non-numeric column '{source_column}' ({col_type}) on '{actual_source}'"
                        )
                        continue

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


def _hierarchy_type_family(data_type: str) -> str:
    """Map a column data type to a broad family.

    Used for parent-child hierarchy validation (both columns must share the
    same type family).  Mixed types across standard-hierarchy levels are
    allowed in Kyvos and are not checked here.
    """
    base = data_type.upper().split("(")[0].strip()
    string_types = {"VARCHAR", "CHAR", "TEXT", "NVARCHAR", "NCHAR", "STRING"}
    integer_types = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "LONG", "SHORT"}
    numeric_types = {"NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "NUMBER"}
    date_types = {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIME", "DATETIME"}
    if base in string_types:
        return "string"
    if base in integer_types:
        return "integer"
    if base in numeric_types:
        return "numeric"
    if base in date_types:
        return "date"
    if base in {"BOOLEAN", "BOOL"}:
        return "boolean"
    return base


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
        has_alternate_path = h.get("has_alternate_path", False)
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
            if (
                parent_dt
                and child_dt
                and _hierarchy_type_family(parent_dt) != _hierarchy_type_family(child_dt)
            ):
                print(
                    f"  Hierarchy '{name}': parent_column '{parent_column}' (type {parent_dt}) "
                    f"and child_column '{child_column}' (type {child_dt}) have different data-type families. "
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

            # Standard hierarchies (not parent-child, not alternate-path) are
            # meaningless with only a single level.
            if not is_parent_child and not has_alternate_path and len(validated_levels) < 2:
                print(
                    f"  Hierarchy '{name}': standard hierarchy has only {len(validated_levels)} level(s) "
                    f"({validated_levels}). Skipping — a standard hierarchy requires at least 2 levels."
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
