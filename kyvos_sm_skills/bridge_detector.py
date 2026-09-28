"""Pure bridge table detection logic.

This module is intentionally side-effect free: it takes tables, relationships,
measures, and aliases as input and returns a classification result.  Both the
live deployment flow (``skill_runner.py``) and the offline analyzer
(``scripts/analyze_bridge_classification.py``) call the same function so that
behaviour is identical.

Invariants for a true bridge / junction table
----------------------------------------------
A table is classified as **bridge** only when **all** of the following hold:

* It has an incoming relationship from at least one fact table.
* It has an outgoing relationship to at least one dimension table.
* It has **no** outgoing relationship to any fact table.
* It has ≤ ``max_outgoing`` outgoing relationships (default 3).
* It has **no** measures assigned to it.
* Its column structure is that of a pure many-to-many resolver:
  at most 1 non-PK column and ≤ 6 total columns.
  (A classic bridge has a composite PK of FKs and zero or one extra
  business-key column.)

If the LLM classified a table as ``bridge`` but any invariant fails, the table
is reclassified as ``dimension``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from kyvos_sm_skills.models import MeasureSpec, RelationshipSpec, TableSpec

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BridgeDecision:
    """Per-table decision record."""

    table_name: str
    llm_type: str
    final_type: str
    is_bridge: bool
    reason: str
    # diagnostic metrics
    outgoing_count: int = 0
    outgoing_to_dim_count: int = 0
    has_outgoing_to_fact: bool = False
    has_outgoing_to_dim: bool = False
    has_incoming_from_fact: bool = False
    has_incoming_from_fact_via_non_pk: bool = False
    has_measures: bool = False
    total_columns: int = 0
    non_pk_columns: int = 0
    pk_columns: list[str] = field(default_factory=list)


@dataclass
class BridgeDetectionResult:
    """Result of the full bridge detection pass."""

    bridge_names: set[str]
    reclassified: dict[str, str]  # table_name -> reason
    decisions: list[BridgeDecision]
    fact_names: set[str]
    dim_names: set[str]

    def summary(self) -> str:
        lines = [
            f"Bridge datasets: {sorted(self.bridge_names)}",
            f"Fact datasets:   {sorted(self.fact_names)}",
            f"Dim datasets:    {sorted(self.dim_names)}",
            f"Reclassified:    {len(self.reclassified)} table(s)",
        ]
        for name, reason in sorted(self.reclassified.items()):
            lines.append(f"  {name}: {reason}")
        lines.append("")
        lines.append("Per-table decisions:")
        for d in self.decisions:
            flag = "BRIDGE" if d.is_bridge else d.final_type
            lines.append(
                f"  {d.table_name:30s}  llm={d.llm_type:10s}  "
                f"final={flag:10s}  out={d.outgoing_count}  "
                f"out_dim={d.outgoing_to_dim_count}  "
                f"out_fact={d.has_outgoing_to_fact}  "
                f"in_fact={d.has_incoming_from_fact}  "
                f"measures={d.has_measures}  "
                f"cols={d.total_columns}  non_pk={d.non_pk_columns}  "
                f"reason={d.reason}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_names": sorted(self.bridge_names),
            "fact_names": sorted(self.fact_names),
            "dim_names": sorted(self.dim_names),
            "reclassified": dict(self.reclassified),
            "decisions": [
                {
                    "table_name": d.table_name,
                    "llm_type": d.llm_type,
                    "final_type": d.final_type,
                    "is_bridge": d.is_bridge,
                    "reason": d.reason,
                    "outgoing_count": d.outgoing_count,
                    "outgoing_to_dim_count": d.outgoing_to_dim_count,
                    "has_outgoing_to_fact": d.has_outgoing_to_fact,
                    "has_outgoing_to_dim": d.has_outgoing_to_dim,
                    "has_incoming_from_fact": d.has_incoming_from_fact,
                    "has_incoming_from_fact_via_non_pk": d.has_incoming_from_fact_via_non_pk,
                    "has_measures": d.has_measures,
                    "total_columns": d.total_columns,
                    "non_pk_columns": d.non_pk_columns,
                    "pk_columns": d.pk_columns,
                }
                for d in self.decisions
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

BRIDGE_NAME_PATTERNS = (
    "reasons", "reason", "bridge", "junction", "xref", "crossref",
    "association", "assoc", "mapping", "linkage",
)


def detect_bridges(
    *,
    tables: list[TableSpec],
    relationships: list[RelationshipSpec],
    measures: list[MeasureSpec],
    dataset_aliases: dict[str, str] | None = None,
    max_outgoing: int = 3,
    max_non_pk_columns: int = 1,
    max_total_columns: int = 6,
    reclassify: bool = True,
) -> BridgeDetectionResult:
    """Run bridge detection on the given tables and relationships.

    Parameters
    ----------
    tables
        All table specs (after spec_builder pruning).
    relationships
        Validated relationships (left → right).
    measures
        All measures.
    dataset_aliases
        Mapping from internal table name to Kyvos server name.
    max_outgoing
        Maximum outgoing relationships for a bridge table.
    max_non_pk_columns
        Maximum non-PK columns for a bridge table.
    max_total_columns
        Maximum total columns for a bridge table.

    Returns
    -------
    BridgeDetectionResult
    """
    dataset_aliases = dataset_aliases or {}

    # -- helper -----------------------------------------------------------
    def _server_name(t: TableSpec) -> str:
        return dataset_aliases.get(t.name, t.name)

    def _server_lower(t: TableSpec) -> str:
        return _server_name(t).lower()

    # -- initial classification from LLM ----------------------------------
    fact_names: set[str] = set()
    bridge_names: set[str] = set()
    for t in tables:
        sn = _server_name(t)
        if t.table_type == "fact":
            fact_names.add(sn)
        elif t.table_type == "bridge":
            bridge_names.add(sn)

    fact_names_lower = {n.lower() for n in fact_names}
    dim_names_lower = {
        _server_lower(t) for t in tables if t.table_type == "dimension"
    }

    # -- PK columns per table ---------------------------------------------
    table_pk_cols: dict[str, set[str]] = {}
    for t in tables:
        sl = _server_lower(t)
        table_pk_cols[sl] = {
            c.name.lower() for c in (t.columns or []) if c.is_primary_key
        }

    # -- measure source datasets ------------------------------------------
    measure_source_datasets = {
        m.source_dataset.lower() for m in measures if m.source_dataset
    }

    # -- relationship structure maps (early) ------------------------------
    _outgoing_count: dict[str, int] = {}
    _outgoing_to_fact: set[str] = set()
    _outgoing_to_dim_early: set[str] = set()
    for rel in relationships:
        left_sn = dataset_aliases.get(rel.left_dataset, rel.left_dataset)
        right_sn = dataset_aliases.get(rel.right_dataset, rel.right_dataset)
        left_lower = left_sn.lower()
        _outgoing_count[left_lower] = _outgoing_count.get(left_lower, 0) + 1
        if right_sn.lower() in fact_names_lower:
            _outgoing_to_fact.add(left_lower)
        if right_sn.lower() in dim_names_lower:
            _outgoing_to_dim_early.add(left_lower)

    # -- Phase 1: validate LLM-classified bridges -------------------------
    reclassified: dict[str, str] = {}
    decisions: list[BridgeDecision] = []

    for t in tables:
        sn = _server_name(t)
        sl = sn.lower()
        llm_type = t.table_type

        # Build diagnostic metrics for every table
        pk_set = table_pk_cols.get(sl, set())
        all_cols = t.columns or []
        non_pk = sum(1 for c in all_cols if c.name.lower() not in pk_set)
        has_measures = t.name.lower() in measure_source_datasets
        out_cnt = _outgoing_count.get(sl, 0)
        out_to_fact = sl in _outgoing_to_fact
        out_to_dim = sl in _outgoing_to_dim_early

        # Determine incoming-from-fact (need the detailed pass below for
        # via_pk vs via_non_pk, but for the early phase we just need
        # whether any incoming from fact exists).
        # We'll fill in the detailed metrics after the second pass.

        if llm_type != "bridge":
            # Will be checked again in auto-detection; for now just record
            # the LLM type.  We'll build full decisions after both passes.
            continue

        # Reclassify if any invariant fails
        reason = ""
        new_type = "bridge"

        if out_to_fact:
            reason = "has outgoing to fact"
            new_type = "dimension"
        elif out_cnt > max_outgoing:
            reason = f"too many outgoing ({out_cnt})"
            new_type = "dimension"
        elif has_measures:
            reason = "has measures assigned"
            new_type = "dimension"
        elif not out_to_dim:
            reason = "no outgoing to dimension"
            new_type = "dimension"
        elif non_pk > max_non_pk_columns or len(all_cols) > max_total_columns:
            # Don't reclassify based on column structure if the table has a
            # bridge-like name and meets relationship conditions. Warehouse
            # schemas often don't mark composite PKs on junction tables.
            _has_bridge_name = any(
                p in t.name.lower() for p in BRIDGE_NAME_PATTERNS
            )
            if (
                _has_bridge_name
                and not out_to_fact
                and out_cnt > 0
                and out_cnt <= max_outgoing
                and not has_measures
            ):
                pass  # Keep as bridge despite column structure
            else:
                reason = f"too many non-PK columns ({non_pk}/{len(all_cols)})"
                new_type = "dimension"

        if new_type != "bridge":
            if reclassify:
                reclassified[sn] = reason
                t.table_type = "dimension"
                bridge_names.discard(sn)
            else:
                # Pre-sweep mode: don't reclassify — keep the bridge type
                # so the sweep's BFS includes this table's edges. The
                # post-sweep detection will handle reclassification.
                pass

    # -- Phase 2: refresh dim_names_lower after reclassification ----------
    dim_names_lower = {
        _server_lower(t) for t in tables if t.table_type == "dimension"
    }

    # -- Phase 2: detailed relationship maps for auto-detection -----------
    incoming_from_fact: set[str] = set()
    incoming_from_fact_via_pk: set[str] = set()
    incoming_from_fact_via_non_pk: set[str] = set()
    incoming_from_fact_or_bridge: set[str] = set()
    incoming_from_fact_or_bridge_via_non_pk: set[str] = set()
    outgoing_to_dim: set[str] = set()
    outgoing_to_fact_set: set[str] = set()
    outgoing_to_dim_via_non_pk: set[str] = set()
    outgoing_count: dict[str, int] = {}
    outgoing_to_dim_count: dict[str, int] = {}

    # Build bridge names lower for checking incoming from bridge
    _bridge_names_lower = {n.lower() for n in bridge_names}

    for rel in relationships:
        left_sn = dataset_aliases.get(rel.left_dataset, rel.left_dataset)
        right_sn = dataset_aliases.get(rel.right_dataset, rel.right_dataset)
        left_lower = left_sn.lower()
        right_lower = right_sn.lower()
        outgoing_count[left_lower] = outgoing_count.get(left_lower, 0) + 1

        if left_lower in fact_names_lower:
            incoming_from_fact.add(right_lower)
            incoming_from_fact_or_bridge.add(right_lower)
            rel_col_lower = rel.right_column.lower()
            right_pk = table_pk_cols.get(right_lower, set())
            if rel_col_lower in right_pk:
                incoming_from_fact_via_pk.add(right_lower)
            else:
                incoming_from_fact_via_non_pk.add(right_lower)
                incoming_from_fact_or_bridge_via_non_pk.add(right_lower)
        elif left_lower in _bridge_names_lower:
            # Incoming from a bridge table also counts for bridge detection
            incoming_from_fact_or_bridge.add(right_lower)
            rel_col_lower = rel.right_column.lower()
            right_pk = table_pk_cols.get(right_lower, set())
            if rel_col_lower not in right_pk:
                incoming_from_fact_or_bridge_via_non_pk.add(right_lower)

        if right_sn.lower() in dim_names_lower:
            outgoing_to_dim.add(left_lower)
            outgoing_to_dim_count[left_lower] = (
                outgoing_to_dim_count.get(left_lower, 0) + 1
            )
            left_pk = table_pk_cols.get(left_lower, set())
            if rel.left_column.lower() not in left_pk:
                outgoing_to_dim_via_non_pk.add(left_lower)

        if right_sn.lower() in fact_names_lower:
            outgoing_to_fact_set.add(left_lower)

    # -- Phase 2: auto-detect bridges from unknown/dimension tables -------
    for t in tables:
        if t.table_type not in ("unknown", "", "dimension"):
            continue
        sn = _server_name(t)
        if sn in fact_names or sn in bridge_names:
            continue
        sl = sn.lower()
        if t.name.lower() in measure_source_datasets:
            continue

        pk_set = table_pk_cols.get(sl, set())
        all_cols = t.columns or []
        non_pk = sum(1 for c in all_cols if c.name.lower() not in pk_set)
        out_cnt = outgoing_count.get(sl, 0)

        # Name-based heuristic: checked FIRST, before the column structure
        # filter, because warehouse schemas may not mark composite PKs
        # correctly. The name pattern plus relationship constraints
        # (incoming from fact/bridge, no outgoing to fact, few outgoing)
        # provide sufficient signal even without PK metadata.
        if (
            sl in incoming_from_fact_or_bridge
            and sl not in outgoing_to_fact_set
            and out_cnt > 0
            and out_cnt <= max_outgoing
            and any(p in t.name.lower() for p in BRIDGE_NAME_PATTERNS)
        ):
            bridge_names.add(sn)
            t.table_type = "bridge"
            continue

        # Column structure filter for remaining patterns
        if non_pk > max_non_pk_columns or len(all_cols) > max_total_columns:
            continue

        # Standard bridge pattern: incoming from fact (or bridge) via non-PK
        # AND outgoing to dimension via non-PK AND NO outgoing to fact
        # AND few outgoing relationships.
        if (
            sl in incoming_from_fact_or_bridge_via_non_pk
            and sl in outgoing_to_dim_via_non_pk
            and sl not in outgoing_to_fact_set
            and out_cnt <= max_outgoing
        ):
            bridge_names.add(sn)
            t.table_type = "bridge"
            continue

        # Composite PK bridge: table has 2+ PK columns, incoming from fact
        # (or bridge), outgoing to dimension via non-PK, NO outgoing to fact,
        # and few outgoing relationships.
        if (
            len(pk_set) >= 2
            and sl in incoming_from_fact_or_bridge
            and sl in outgoing_to_dim_via_non_pk
            and sl not in outgoing_to_fact_set
            and out_cnt <= max_outgoing
        ):
            bridge_names.add(sn)
            t.table_type = "bridge"
            continue

    # -- Build per-table decision records ---------------------------------
    final_dim_names = {
        _server_lower(t) for t in tables if t.table_type == "dimension"
    }

    for t in tables:
        sn = _server_name(t)
        sl = sn.lower()
        pk_set = table_pk_cols.get(sl, set())
        all_cols = t.columns or []
        non_pk = sum(1 for c in all_cols if c.name.lower() not in pk_set)

        d = BridgeDecision(
            table_name=sn,
            llm_type=t.table_type if t.table_type else "unknown",
            final_type=t.table_type if t.table_type else "unknown",
            is_bridge=sn in bridge_names,
            reason="",
            outgoing_count=outgoing_count.get(sl, _outgoing_count.get(sl, 0)),
            outgoing_to_dim_count=outgoing_to_dim_count.get(sl, 0),
            has_outgoing_to_fact=sl in outgoing_to_fact_set or sl in _outgoing_to_fact,
            has_outgoing_to_dim=sl in outgoing_to_dim or sl in _outgoing_to_dim_early,
            has_incoming_from_fact=sl in incoming_from_fact,
            has_incoming_from_fact_via_non_pk=sl in incoming_from_fact_via_non_pk,
            has_measures=t.name.lower() in measure_source_datasets,
            total_columns=len(all_cols),
            non_pk_columns=non_pk,
            pk_columns=sorted(pk_set),
        )

        if sn in reclassified:
            d.reason = reclassified[sn]
        elif d.is_bridge:
            d.reason = "detected as bridge"
        else:
            d.reason = "not a bridge"

        decisions.append(d)

    return BridgeDetectionResult(
        bridge_names=bridge_names,
        reclassified=reclassified,
        decisions=decisions,
        fact_names=fact_names,
        dim_names=final_dim_names,
    )
