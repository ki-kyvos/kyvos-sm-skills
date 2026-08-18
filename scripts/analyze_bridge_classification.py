#!/usr/bin/env python3
"""Standalone bridge classification analyzer.

Loads a snapshot JSON file (produced by ``skill_runner.py`` during a live run)
and runs the pure ``detect_bridges`` function, printing a per-table decision
report.  This allows iterating on bridge detection logic in seconds without
re-deploying to a live Kyvos server.

Usage
-----
    .venv/bin/python scripts/analyze_bridge_classification.py <snapshot.json>

Snapshot format
---------------
The snapshot is a JSON file with the following top-level keys::

    {
      "tables":        [ {TableSpec.dict()}, ... ],
      "relationships": [ {RelationshipSpec.dict()}, ... ],
      "measures":      [ {MeasureSpec.dict()}, ... ],
      "dataset_aliases": {"internal_name": "server_name", ...}
    }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the package is importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kyvos_sm_skills.bridge_detector import detect_bridges
from kyvos_sm_skills.models import MeasureSpec, RelationshipSpec, TableSpec


def load_snapshot(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <snapshot.json>")
        return 1

    snapshot = load_snapshot(sys.argv[1])

    tables = [TableSpec(**t) for t in snapshot.get("tables", [])]
    relationships = [RelationshipSpec(**r) for r in snapshot.get("relationships", [])]
    measures = [MeasureSpec(**m) for m in snapshot.get("measures", [])]
    dataset_aliases = snapshot.get("dataset_aliases", {})

    result = detect_bridges(
        tables=tables,
        relationships=relationships,
        measures=measures,
        dataset_aliases=dataset_aliases,
    )

    print(result.summary())
    print()
    print(f"Total tables:      {len(tables)}")
    print(f"Total rels:        {len(relationships)}")
    print(f"Total measures:    {len(measures)}")
    print(f"Bridge count:      {len(result.bridge_names)}")
    print(f"Reclassified count: {len(result.reclassified)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
