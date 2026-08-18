"""Remove orphaned tables, relationships, and dimensions from SM design JSONs.

Two-phase cleanup:
1. Remove fact tables that have no measures (dead-weight facts)
2. Remove dimensions that are only connected to removed fact tables

These cause Kyvos validation errors:
"Dimension X is invalid because it does not have valid relation with any measure"
"""
import json
from collections import defaultdict, deque


def find_connected_to_measures(sm: dict) -> set[str]:
    """Find tables directly connected to at least one fact table with measures.

    In Kyvos star-schema, a dimension is valid only if it has a direct
    relationship to a fact table that contains measures. Dimension-to-dimension
    traversal through intermediate facts is NOT supported.
    """
    # Fact tables with measures
    fact_with_measures = set()
    for m in sm.get("measures", []):
        ds = m.get("source_dataset", "")
        if ds:
            fact_with_measures.add(ds.lower())

    # A table is connected if it IS a fact with measures,
    # or is directly related to a fact with measures
    connected = set(fact_with_measures)
    for rel in sm.get("relationships", []):
        ft = rel["from_table"].lower()
        tt = rel["to_table"].lower()
        if ft in fact_with_measures:
            connected.add(tt)
        if tt in fact_with_measures:
            connected.add(ft)

    return connected


def remove_orphaned(sm_path: str) -> None:
    with open(sm_path) as f:
        design = json.load(f)

    sm = design["recommended_sms"][0]
    sm_name = sm.get("name", "?")

    # Phase 1: Identify fact tables with no measures
    fact_with_measures = set()
    for m in sm.get("measures", []):
        ds = m.get("source_dataset", "")
        if ds:
            fact_with_measures.add(ds.lower())

    # All tables in relationships
    all_rel_tables = set()
    for rel in sm.get("relationships", []):
        all_rel_tables.add(rel["from_table"].lower())
        all_rel_tables.add(rel["to_table"].lower())

    # Phase 2: Find tables connected to facts with measures
    connected = find_connected_to_measures(sm)
    orphaned = all_rel_tables - connected

    print(f"\n{'='*60}")
    print(f"SM: {sm_name} ({sm_path})")
    print(f"{'='*60}")
    print(f"Fact tables with measures: {sorted(fact_with_measures)}")
    print(f"Connected tables: {sorted(connected)}")
    print(f"Orphaned tables: {sorted(orphaned)}")

    if not orphaned:
        print("No orphaned tables found.")
        return

    # Remove orphaned relationships (any relationship involving an orphaned table)
    orig_rels = sm.get("relationships", [])
    new_rels = [
        rel for rel in orig_rels
        if rel["from_table"].lower() not in orphaned
        and rel["to_table"].lower() not in orphaned
    ]
    removed_rels = len(orig_rels) - len(new_rels)
    print(f"Removing {removed_rels} relationships involving orphaned tables")

    # Remove orphaned tables from table list
    orig_tables = sm.get("tables", [])
    if isinstance(orig_tables, list) and orig_tables and isinstance(orig_tables[0], str):
        new_tables = [t for t in orig_tables if t.lower() not in orphaned]
    elif isinstance(orig_tables, list) and orig_tables and isinstance(orig_tables[0], dict):
        new_tables = [t for t in orig_tables if t.get("name", "").lower() not in orphaned]
    else:
        new_tables = orig_tables
    removed_tables = len(orig_tables) - len(new_tables)
    print(f"Removing {removed_tables} orphaned tables")

    # Remove orphaned hierarchies (whose source_dataset is orphaned)
    orig_hiers = sm.get("hierarchies", [])
    new_hiers = [
        h for h in orig_hiers
        if h.get("source_dataset", "").lower() not in orphaned
    ]
    removed_hiers = len(orig_hiers) - len(new_hiers)
    if removed_hiers:
        print(f"Removing {removed_hiers} hierarchies from orphaned tables")

    # Remove orphaned measures (whose source_dataset is orphaned)
    orig_measures = sm.get("measures", [])
    new_measures = [
        m for m in orig_measures
        if m.get("source_dataset", "").lower() not in orphaned
        or not m.get("source_dataset")  # Keep calculated measures with no source_dataset
    ]
    removed_measures = len(orig_measures) - len(new_measures)
    if removed_measures:
        print(f"Removing {removed_measures} measures from orphaned tables")

    # Update SM
    sm["relationships"] = new_rels
    sm["tables"] = new_tables
    sm["hierarchies"] = new_hiers
    sm["measures"] = new_measures

    # Also remove orphaned tables from shared_dimensions if present
    if "shared_dimensions" in sm:
        orig_shared = sm["shared_dimensions"]
        new_shared = [d for d in orig_shared if d.lower() not in orphaned]
        if len(new_shared) < len(orig_shared):
            print(f"Removing {len(orig_shared) - len(new_shared)} shared_dimensions")
            sm["shared_dimensions"] = new_shared

    with open(sm_path, "w") as f:
        json.dump(design, f, indent=2)
    print(f"Updated {sm_path}")


remove_orphaned("samples/output/flow-a-intent-file-sm-design.json")
remove_orphaned("samples/output/flow-b-generate-intent-sm-design.json")
