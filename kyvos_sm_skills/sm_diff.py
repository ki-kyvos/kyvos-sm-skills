"""Structural comparison utility for semantic model specs.

Normalizes and compares two spec objects (``DiscoveredSpec`` or
``DomainDemoSpec``) to verify cross-path structural compatibility.

Compatible specs must agree on:
- Table set
- Fact / dimension / bridge classification per table
- Relationship set and direction (left=fact/bridge, right=dimension)
- Parent-child hierarchy detection

They may differ on:
- Measure names and calculated measure formulas
- Hierarchy display names
- Extra KPIs/calculated measures
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kyvos_sm_skills.models import (
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)


@dataclass
class DiffResult:
    """Result of comparing two semantic model specs."""

    table_set_match: bool = True
    table_type_mismatches: list[tuple[str, str, str]] = field(default_factory=list)
    relationship_mismatches: list[str] = field(default_factory=list)
    dim_to_dim_relationships: list[str] = field(default_factory=list)
    disconnected_dimensions: list[str] = field(default_factory=list)
    measure_source_mismatches: list[str] = field(default_factory=list)
    parent_child_mismatches: list[str] = field(default_factory=list)

    @property
    def is_compatible(self) -> bool:
        """True if no structural incompatibilities were found."""
        return (
            self.table_set_match
            and not self.table_type_mismatches
            and not self.relationship_mismatches
            and not self.dim_to_dim_relationships
            and not self.disconnected_dimensions
        )

    def summary(self) -> str:
        lines: list[str] = []
        if self.table_set_match:
            lines.append("Table sets: MATCH")
        else:
            lines.append("Table sets: MISMATCH")
        if self.table_type_mismatches:
            lines.append(f"Table type mismatches ({len(self.table_type_mismatches)}):")
            for name, t1, t2 in self.table_type_mismatches:
                lines.append(f"  {name}: {t1} vs {t2}")
        if self.relationship_mismatches:
            lines.append(f"Relationship mismatches ({len(self.relationship_mismatches)}):")
            for m in self.relationship_mismatches:
                lines.append(f"  {m}")
        if self.dim_to_dim_relationships:
            lines.append(f"Dim→dim relationships ({len(self.dim_to_dim_relationships)}):")
            for r in self.dim_to_dim_relationships:
                lines.append(f"  {r}")
        if self.disconnected_dimensions:
            lines.append(f"Disconnected dimensions ({len(self.disconnected_dimensions)}):")
            for d in self.disconnected_dimensions:
                lines.append(f"  {d}")
        if self.measure_source_mismatches:
            lines.append(f"Measure source_dataset mismatches ({len(self.measure_source_mismatches)}):")
            for m in self.measure_source_mismatches:
                lines.append(f"  {m}")
        if self.parent_child_mismatches:
            lines.append(f"Parent-child hierarchy mismatches ({len(self.parent_child_mismatches)}):")
            for m in self.parent_child_mismatches:
                lines.append(f"  {m}")
        if self.is_compatible:
            lines.append("Overall: COMPATIBLE")
        else:
            lines.append("Overall: INCOMPATIBLE")
        return "\n".join(lines)


def _extract_tables(spec: Any) -> list[TableSpec]:
    if hasattr(spec, "tables"):
        return spec.tables
    return []


def _extract_semantic_model(spec: Any) -> SemanticModelSpec | None:
    if hasattr(spec, "semantic_model"):
        return spec.semantic_model
    return None


def _normalize_rel(rel: RelationshipSpec) -> tuple[str, str, str, str]:
    return (
        rel.left_dataset.lower(),
        rel.left_column.lower(),
        rel.right_dataset.lower(),
        rel.right_column.lower(),
    )


def compare_specs(spec_a: Any, spec_b: Any) -> DiffResult:
    """Compare two semantic model specs for structural compatibility.

    Args:
        spec_a: First spec (``DiscoveredSpec`` or ``DomainDemoSpec``).
        spec_b: Second spec (``DiscoveredSpec`` or ``DomainDemoSpec``).

    Returns:
        ``DiffResult`` with detailed mismatch information.
    """
    result = DiffResult()

    tables_a = _extract_tables(spec_a)
    tables_b = _extract_tables(spec_b)

    # Compare table sets
    names_a = {t.name.lower() for t in tables_a}
    names_b = {t.name.lower() for t in tables_b}
    if names_a != names_b:
        result.table_set_match = False
        only_a = names_a - names_b
        only_b = names_b - names_a
        if only_a:
            result.relationship_mismatches.append(
                f"Tables only in spec A: {sorted(only_a)}"
            )
        if only_b:
            result.relationship_mismatches.append(
                f"Tables only in spec B: {sorted(only_b)}"
            )

    # Compare table types
    type_map_a = {t.name.lower(): t.table_type for t in tables_a}
    type_map_b = {t.name.lower(): t.table_type for t in tables_b}
    for name in sorted(names_a & names_b):
        ta = type_map_a.get(name, "unknown")
        tb = type_map_b.get(name, "unknown")
        if ta != tb:
            result.table_type_mismatches.append((name, ta, tb))

    # Compare relationships
    sm_a = _extract_semantic_model(spec_a)
    sm_b = _extract_semantic_model(spec_b)

    rels_a: set[tuple[str, str, str, str]] = set()
    rels_b: set[tuple[str, str, str, str]] = set()

    if sm_a and sm_a.relationships:
        rels_a = {_normalize_rel(r) for r in sm_a.relationships}
    if sm_b and sm_b.relationships:
        rels_b = {_normalize_rel(r) for r in sm_b.relationships}

    if rels_a != rels_b:
        only_in_a = rels_a - rels_b
        only_in_b = rels_b - rels_a
        for r in sorted(only_in_a):
            result.relationship_mismatches.append(
                f"Relationship only in A: {r[0]}.{r[1]} -> {r[2]}.{r[3]}"
            )
        for r in sorted(only_in_b):
            result.relationship_mismatches.append(
                f"Relationship only in B: {r[0]}.{r[1]} -> {r[2]}.{r[3]}"
            )

    # Check for dim→dim relationships in either spec
    all_types = {**type_map_a, **type_map_b}
    for rels, label in [(rels_a, "A"), (rels_b, "B")]:
        for r in rels:
            left_type = all_types.get(r[0], "dimension")
            right_type = all_types.get(r[2], "dimension")
            if left_type not in ("fact", "bridge") and right_type not in ("fact", "bridge"):
                result.dim_to_dim_relationships.append(
                    f"Spec {label}: {r[0]} ({left_type}) -> {r[2]} ({right_type})"
                )

    # Check for disconnected dimensions in either spec
    for tables, rels, label in [(tables_a, rels_a, "A"), (tables_b, rels_b, "B")]:
        fact_names = {t.name.lower() for t in tables if t.table_type == "fact"}
        if not fact_names:
            continue
        graph: dict[str, set[str]] = {t.name.lower(): set() for t in tables}
        for r in rels:
            if r[0] in graph and r[2] in graph:
                graph[r[0]].add(r[2])
        connected: set[str] = set()
        queue = list(fact_names)
        while queue:
            current = queue.pop(0)
            if current in connected:
                continue
            connected.add(current)
            for neighbor in graph.get(current, set()):
                if neighbor not in connected:
                    queue.append(neighbor)
        spec_names = {t.name.lower() for t in tables}
        disconnected = spec_names - connected
        for d in sorted(disconnected):
            result.disconnected_dimensions.append(f"Spec {label}: {d}")

    # Check for measure source_dataset mismatches (measures on non-fact tables)
    for sm, label in [(sm_a, "A"), (sm_b, "B")]:
        if not sm or not sm.measures:
            continue
        for m in sm.measures:
            if m.source_dataset:
                ds_lower = m.source_dataset.lower()
                ds_type = all_types.get(ds_lower, "unknown")
                if ds_type not in ("fact", "bridge"):
                    result.measure_source_mismatches.append(
                        f"Spec {label}: Measure '{m.name}' references '{m.source_dataset}' ({ds_type})"
                    )

    # Compare parent-child hierarchies
    hiers_a: dict[str, bool] = {}
    hiers_b: dict[str, bool] = {}
    if sm_a and sm_a.hierarchies:
        for h in sm_a.hierarchies:
            key = (h.source_dataset or "").lower()
            hiers_a[key] = h.is_parent_child
    if sm_b and sm_b.hierarchies:
        for h in sm_b.hierarchies:
            key = (h.source_dataset or "").lower()
            hiers_b[key] = h.is_parent_child
    for ds in sorted(set(hiers_a.keys()) & set(hiers_b.keys())):
        if hiers_a[ds] != hiers_b[ds]:
            result.parent_child_mismatches.append(
                f"{ds}: parent_child={hiers_a[ds]} vs parent_child={hiers_b[ds]}"
            )

    return result
