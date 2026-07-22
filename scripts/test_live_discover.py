#!/usr/bin/env python3
"""Live integration test for the discover-sm-from-warehouse skill flow.

This script performs a full end-to-end test against a live warehouse + Kyvos server:
  1. Inspects the warehouse schema (requires real DB connection)
  2. Loads a pre-approved SM design JSON
  3. Builds the deployment spec
  4. Deploys to Kyvos (creates connection, datasets, DRD, semantic model)
  5. Validates all entities

Prerequisites:
    pip install kyvos-sdk-python[env,inspect] kyvos-sm-skills[sdk]

Required:
    - .env file with KYVOS_BASE_URL, KYVOS_AUTH_TOKEN, warehouse connection params
    - Warehouse with AdventureWorks tables (or equivalent)
    - Kyvos server running and accessible

Usage:
    # Pre-approved JSON mode
    python scripts/test_live_discover.py \
        --env-file .env \
        --sm-design samples/adventureworks-sm-design.json

    # LLM mode (requires ANTHROPIC_API_KEY)
    python scripts/test_live_discover.py \
        --env-file .env \
        --user-intent "I want sales analytics for Adventure Works" \
        --domain adventure_works

    # With schema filter
    python scripts/test_live_discover.py \
        --env-file .env \
        --sm-design samples/adventureworks-sm-design.json \
        --schema sales

    # Dry run (inspect + build spec, no Kyvos deployment)
    python scripts/test_live_discover.py \
        --env-file .env \
        --sm-design samples/adventureworks-sm-design.json \
        --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live integration test for discover-sm-from-warehouse",
    )
    parser.add_argument("--env-file", required=True, help="Path to .env config file")
    parser.add_argument("--sm-design", default=None, help="Path to SM design JSON file")
    parser.add_argument("--user-intent", default=None, help="Natural language intent (LLM mode)")
    parser.add_argument("--domain", default=None, help="Domain hint")
    parser.add_argument("--schema", default=None, help="Warehouse schema to inspect")
    parser.add_argument("--max-tables", type=int, default=500, help="Max tables to inspect")
    parser.add_argument("--payload-format", default=None, choices=["json", "xml"])
    parser.add_argument("--dry-run", action="store_true", help="Inspect + build spec only, no deployment")
    args = parser.parse_args()

    if not args.sm_design and not args.user_intent:
        print("Error: Either --sm-design or --user-intent must be provided.")
        return 1

    from kyvos_sm_skills.skill_runner import run_discover_sm_from_warehouse

    print("\n" + "=" * 70)
    print("  Live Integration Test: discover-sm-from-warehouse")
    print("=" * 70)

    if args.dry_run:
        print("  Mode: DRY RUN (no Kyvos deployment)")
    elif args.sm_design:
        print("  Mode: Pre-approved JSON")
    else:
        print("  Mode: LLM (Anthropic API)")
    print()

    rc = run_discover_sm_from_warehouse(
        env_file=args.env_file,
        sm_design_path=args.sm_design,
        user_intent=args.user_intent,
        domain=args.domain,
        auto_approve=True,
        schema_filter=args.schema,
        max_tables=args.max_tables,
        payload_format=args.payload_format,
        dry_run=args.dry_run,
    )

    if rc == 0:
        print("\n" + "=" * 70)
        print("  ✅ Live integration test PASSED!")
        print("=" * 70)
    else:
        print("\n" + "=" * 70)
        print("  ❌ Live integration test FAILED!")
        print("=" * 70)

    return rc


if __name__ == "__main__":
    sys.exit(main())
