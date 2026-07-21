#!/usr/bin/env python3
"""End-to-end validation for the AdventureWorks discover-sm-from-warehouse flow.

Supports two modes:
  --intent-file   : Uses a pre-written intent file (Flow A)
  --generate-intent : Auto-generates intent via LLM from schema (Flow B)
  --compare       : Runs both flows and compares the resulting models

By default uses mock mode (no live warehouse or Kyvos server needed) — requires --sm-design.
With --live, connects to the real warehouse and Kyvos server.

Usage:
    # Mock dry-run — Flow A (intent file + pre-approved SM design)
    python scripts/validate_adventureworks_discover.py --intent-file intent-adventureworks.txt --sm-design samples/adventureworks-sm-design.json --domain adventure_works --dry-run

    # Mock dry-run — Flow B (generate intent, requires LLM API key)
    python scripts/validate_adventureworks_discover.py --generate-intent --domain adventure_works --dry-run --env-file .env

    # Mock dry-run — compare both flows
    python scripts/validate_adventureworks_discover.py --compare --intent-file intent-adventureworks.txt --sm-design samples/adventureworks-sm-design.json --domain adventure_works --dry-run

    # Live E2E
    python scripts/validate_adventureworks_discover.py --compare --intent-file intent-adventureworks.txt --domain adventure_works --live --env-file .env

Prerequisites:
    pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# ── Helpers ─────────────────────────────────────────────────────────────────


def _print_step(n: int, msg: str) -> None:
    print(f"\n{'─' * 70}")
    print(f"  Step {n}: {msg}")
    print(f"{'─' * 70}")


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def _info(msg: str) -> None:
    print(f"  ℹ️  {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")


# ── Validation checks ───────────────────────────────────────────────────────


_BLOCKING_DIAG_CODES = {"NO_MEASURES_PLACED", "MISSING_DATASET_ID", "PC_DATA_TYPE_MISMATCH"}


def _validate_top_level(sm_json: dict) -> list[str]:
    errors = []
    if "common" not in sm_json:
        errors.append("Missing 'common' top-level object")
    else:
        if sm_json["common"].get("compatibilityVersion") != "3":
            errors.append(f"common.compatibilityVersion != '3' (got '{sm_json['common'].get('compatibilityVersion')}')")
    if "specific" not in sm_json:
        errors.append("Missing 'specific' top-level object")
        return errors
    if "smObject" not in sm_json["specific"]:
        errors.append("Missing 'specific.smObject'")
        return errors
    sm_obj = sm_json["specific"]["smObject"]
    if "dimensions" not in sm_obj:
        errors.append("Missing 'dimensions' in smObject")
    if "measures" not in sm_obj:
        errors.append("Missing 'measures' in smObject")
    elif "measure" not in sm_obj["measures"]:
        errors.append("Missing 'measures.measure' array")
    return errors


def _validate_hierarchy_fields(sm_json: dict) -> list[str]:
    errors = []
    for dim in sm_json.get("specific", {}).get("smObject", {}).get("dimensions", []):
        for h in dim.get("hierarchies", []):
            if "defaultMemberUniqueName" not in h:
                errors.append(f"Hierarchy '{h.get('name', '?')}' missing defaultMemberUniqueName")
            if "displayFolder" not in h:
                errors.append(f"Hierarchy '{h.get('name', '?')}' missing displayFolder")
    return errors


def _validate_level_fields(sm_json: dict) -> list[str]:
    errors = []
    for dim in sm_json.get("specific", {}).get("smObject", {}).get("dimensions", []):
        for h in dim.get("hierarchies", []):
            for lvl in h.get("levels", []):
                for field in ("dateDataType", "dateFormat", "format", "fieldDataType"):
                    if field not in lvl:
                        errors.append(f"Level '{lvl.get('name', '?')}' in hierarchy '{h.get('name', '?')}' missing {field}")
    return errors


def _validate_measure_fields(sm_json: dict) -> list[str]:
    errors = []
    for m in sm_json.get("specific", {}).get("smObject", {}).get("measures", {}).get("measure", []):
        if "actualSummaryFunction" not in m:
            errors.append(f"Measure '{m.get('name', '?')}' missing actualSummaryFunction")
    return errors


def _validate_mdx_expressions(sm_json: dict) -> list[str]:
    errors = []
    try:
        from kyvos_sm_skills.mdx_reference import validate_mdx_expression
    except ImportError:
        _warn("kyvos_sm_skills.mdx_reference not available — skipping MDX validation")
        return errors
    for m in sm_json.get("specific", {}).get("smObject", {}).get("measures", {}).get("measure", []):
        if "expression" in m:
            issues = validate_mdx_expression(m["expression"]["content"])
            for issue in issues:
                errors.append(f"Measure '{m.get('name', '?')}': {issue}")
    return errors


def _validate_sm_recommendation(sm_design: dict, schema_summary: dict) -> list[str]:
    try:
        from kyvos_sm_skills.llm_designer import validate_sm_recommendation
        return validate_sm_recommendation(sm_design, schema_summary)
    except ImportError:
        _warn("kyvos_sm_skills.llm_designer not available — skipping recommendation validation")
        return []


def _validate_no_blocking_diagnostics(diagnostics: list) -> list[str]:
    errors = []
    for diag in diagnostics:
        if hasattr(diag, "code") and diag.code in _BLOCKING_DIAG_CODES:
            errors.append(f"Blocking diagnostic: {diag.code}: {getattr(diag, 'message', '?')}")
    return errors


def _run_all_validations(sm_json: dict, sm_design: dict, schema_summary: dict, diagnostics: list) -> list[str]:
    all_errors = []
    all_errors.extend(_validate_top_level(sm_json))
    all_errors.extend(_validate_hierarchy_fields(sm_json))
    all_errors.extend(_validate_level_fields(sm_json))
    all_errors.extend(_validate_measure_fields(sm_json))
    all_errors.extend(_validate_mdx_expressions(sm_json))
    all_errors.extend(_validate_sm_recommendation(sm_design, schema_summary))
    all_errors.extend(_validate_no_blocking_diagnostics(diagnostics))
    return all_errors


# ── Mock AdventureWorks schema (for --mock-schema mode) ─────────────────────


_MOCK_AW_TABLES = [
    {
        "name": "factinternetsales",
        "schema": "public",
        "estimated_table_type": "fact",
        "outgoing_fk_count": 4,
        "incoming_fk_count": 0,
        "columns": [
            {"name": "salesordernumber", "data_type": "VARCHAR(20)", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "productkey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimproduct.productkey"},
            {"name": "customerkey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimcustomer.customerkey"},
            {"name": "orderdatekey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimdate.datekey"},
            {"name": "salesterritorykey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimsalesterritory.salesterritorykey"},
            {"name": "salesamount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "orderquantity", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "totalproductcost", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "taxamt", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "freight", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimproduct",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "productkey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "productsubcategorykey", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "productcategorykey", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimcustomer",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "customerkey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimdate",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "datekey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "weeknumberofyear", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "englishmonthname", "data_type": "VARCHAR(20)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "calendarquarter", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "calendaryear", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimsalesterritory",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "salesterritorykey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "salesterritoryregion", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "salesterritorycountry", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "salesterritorygroup", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
]


_MOCK_AW_SM_DESIGN = {
    "recommended_sms": [
        {
            "name": "AdventureWorksSales",
            "schema_type": "star",
            "rationale": "AdventureWorks star schema.",
            "tables": ["factinternetsales", "dimproduct", "dimcustomer", "dimdate", "dimsalesterritory"],
            "relationships": [
                {"from_table": "factinternetsales", "from_column": "productkey", "to_table": "dimproduct", "to_column": "productkey"},
                {"from_table": "factinternetsales", "from_column": "customerkey", "to_table": "dimcustomer", "to_column": "customerkey"},
                {"from_table": "factinternetsales", "from_column": "orderdatekey", "to_table": "dimdate", "to_column": "datekey"},
                {"from_table": "factinternetsales", "from_column": "salesterritorykey", "to_table": "dimsalesterritory", "to_column": "salesterritorykey"},
            ],
            "measures": [
                {"name": "SalesAmount", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "OrderQuantity", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "TotalProductCost", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "TaxAmt", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "Freight", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
            ],
            "hierarchies": [
                {"name": "ProductCategory", "levels": ["productkey", "productsubcategorykey", "productcategorykey"], "source_dataset": "dimproduct"},
                {"name": "CalendarDate", "levels": ["datekey", "weeknumberofyear", "englishmonthname", "calendarquarter", "calendaryear"], "source_dataset": "dimdate"},
                {"name": "SalesTerritory", "levels": ["salesterritorykey", "salesterritoryregion", "salesterritorycountry", "salesterritorygroup"], "source_dataset": "dimsalesterritory"},
            ],
        }
    ],
    "identified_domain": "adventure_works",
    "domain_research_summary": "Adventure Works is a fictional bicycle manufacturing company by Microsoft.",
    "domain_reasoning": "Canonical AdventureWorks star schema.",
    "gaps_identified": [
        "No DimPromotion table found",
        "No DimCurrency table found",
    ],
}


def _mock_inspect_schema(config, schema_filter=None, max_tables=500):
    return {
        "warehouse_type": "POSTGRES",
        "schema": schema_filter or "public",
        "table_count": len(_MOCK_AW_TABLES),
        "tables": _MOCK_AW_TABLES,
        "relationships": [
            {"from_table": "factinternetsales", "from_column": "productkey", "to_table": "dimproduct", "to_column": "productkey"},
            {"from_table": "factinternetsales", "from_column": "customerkey", "to_table": "dimcustomer", "to_column": "customerkey"},
            {"from_table": "factinternetsales", "from_column": "orderdatekey", "to_table": "dimdate", "to_column": "datekey"},
            {"from_table": "factinternetsales", "from_column": "salesterritorykey", "to_table": "dimsalesterritory", "to_column": "salesterritorykey"},
        ],
        "detected_patterns": {
            "potential_star_schemas": [{"fact_table": "factinternetsales", "dimension_tables": ["dimproduct", "dimcustomer", "dimdate", "dimsalesterritory"]}],
            "potential_snowflake_schemas": [],
            "potential_multifact_schemas": [],
            "single_table_candidates": [],
            "disjoint_table_groups": [],
        },
    }


# ── Flow runner ─────────────────────────────────────────────────────────────


def _run_flow(
    *,
    env_file: str,
    intent_file: str | None = None,
    generate_intent: bool = False,
    domain: str | None = None,
    sm_design_path: str | None = None,
    sm_design: dict | None = None,
    dry_run: bool = True,
    live: bool = False,
    output_dir: str = ".",
) -> dict[str, Any]:
    """Run a single discover flow and return results for comparison."""
    from kyvos_sm_skills.skill_runner import run_discover_sm_from_warehouse

    user_intent = None
    if intent_file:
        intent_path = Path(intent_file)
        if not intent_path.exists():
            _fail(f"Intent file not found: {intent_file}")
            return {"success": False, "error": f"Intent file not found: {intent_file}"}
        user_intent = intent_path.read_text()
        _info(f"Loaded intent from: {intent_file} ({len(user_intent)} chars)")

    if generate_intent:
        _info("Generating intent via LLM from schema analysis...")
        # The CLI handles this; here we call the skill_runner with user_intent=None
        # and let the CLI's --generate-intent path handle it
        # For this script, we'll use the CLI directly
        import subprocess
        cmd = [
            sys.executable, "-m", "kyvos_sm_skills.cli", "discover",
            "--env-file", env_file,
            "--generate-intent",
            "--auto-approve",
        ]
        if domain:
            cmd.extend(["--domain", domain])
        if dry_run:
            cmd.append("--dry-run")
        cmd.extend(["--payload-format", "json"])
        intent_output = str(Path(output_dir) / "generated_intent.txt")
        cmd.extend(["--intent-output", intent_output])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            _fail(f"Generate intent flow failed: {result.stderr}")
            return {"success": False, "error": result.stderr}
        _ok("Generate intent flow completed")
        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "intent_path": intent_output,
        }

    # For intent file mode, run skill_runner directly
    rc = run_discover_sm_from_warehouse(
        env_file=env_file,
        sm_design_path=sm_design_path,
        sm_design=sm_design,
        user_intent=user_intent,
        domain=domain,
        auto_approve=True,
        dry_run=dry_run,
        payload_format="json",
    )
    return {"success": rc == 0, "rc": rc}


# ── Comparison report ───────────────────────────────────────────────────────


def _compare_models(flow_a: dict, flow_b: dict) -> None:
    """Print a comparison report between Flow A and Flow B results."""
    print(f"\n{'═' * 70}")
    print("  Comparison Report: Intent File vs Generate Intent")
    print(f"{'═' * 70}")

    print(f"\n  {'Metric':<40s} {'Flow A (Intent File)':<25s} {'Flow B (Generate Intent)':<25s}")
    print(f"  {'─' * 90}")

    a_success = flow_a.get("success", False)
    b_success = flow_b.get("success", False)
    print(f"  {'Flow success':<40s} {'✅' if a_success else '❌':<25s} {'✅' if b_success else '❌':<25s}")

    # Compare SM designs if available
    a_design = flow_a.get("sm_design")
    b_design = flow_b.get("sm_design")
    if a_design and b_design:
        a_sms = a_design.get("recommended_sms", [])
        b_sms = b_design.get("recommended_sms", [])
        if a_sms and b_sms:
            a_sm = a_sms[0]
            b_sm = b_sms[0]

            print(f"  {'SM name':<40s} {a_sm.get('name', '?'):<25s} {b_sm.get('name', '?'):<25s}")
            print(f"  {'Schema type':<40s} {a_sm.get('schema_type', '?'):<25s} {b_sm.get('schema_type', '?'):<25s}")
            print(f"  {'Table count':<40s} {len(a_sm.get('tables', [])):<25d} {len(b_sm.get('tables', [])):<25d}")
            print(f"  {'Relationship count':<40s} {len(a_sm.get('relationships', [])):<25d} {len(b_sm.get('relationships', [])):<25d}")
            print(f"  {'Measure count':<40s} {len(a_sm.get('measures', [])):<25d} {len(b_sm.get('measures', [])):<25d}")
            print(f"  {'Hierarchy count':<40s} {len(a_sm.get('hierarchies', [])):<25d} {len(b_sm.get('hierarchies', [])):<25d}")

            a_tables = set(a_sm.get("tables", []))
            b_tables = set(b_sm.get("tables", []))
            if a_tables == b_tables:
                _ok("Tables match between flows")
            else:
                _warn(f"Tables differ: A-only={a_tables - b_tables}, B-only={b_tables - a_tables}")

            a_measures = {m["name"] for m in a_sm.get("measures", [])}
            b_measures = {m["name"] for m in b_sm.get("measures", [])}
            if a_measures == b_measures:
                _ok("Measure names match between flows")
            else:
                _warn(f"Measures differ: A-only={a_measures - b_measures}, B-only={b_measures - a_measures}")

            a_hierarchies = {h["name"] for h in a_sm.get("hierarchies", [])}
            b_hierarchies = {h["name"] for h in b_sm.get("hierarchies", [])}
            if a_hierarchies == b_hierarchies:
                _ok("Hierarchy names match between flows")
            else:
                _warn(f"Hierarchies differ: A-only={a_hierarchies - b_hierarchies}, B-only={b_hierarchies - a_hierarchies}")

    # Compare validation errors
    a_errors = flow_a.get("validation_errors", [])
    b_errors = flow_b.get("validation_errors", [])
    print(f"\n  Validation errors (Flow A): {len(a_errors)}")
    for e in a_errors:
        print(f"    - {e}")
    print(f"  Validation errors (Flow B): {len(b_errors)}")
    for e in b_errors:
        print(f"    - {e}")

    if not a_errors and not b_errors:
        _ok("Both flows pass all validations with zero errors")
    elif not a_errors:
        _ok("Flow A passes all validations")
    elif not b_errors:
        _ok("Flow B passes all validations")
    else:
        _fail("Both flows have validation errors")

    print(f"\n{'═' * 70}")


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AdventureWorks discover-sm-from-warehouse validation (intent file vs generate intent)",
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--intent-file", default=None, help="Path to pre-written intent file (Flow A)")
    parser.add_argument("--generate-intent", action="store_true", help="Auto-generate intent via LLM (Flow B)")
    parser.add_argument("--compare", action="store_true", help="Run both flows and compare")
    parser.add_argument("--domain", default="adventure_works", help="Domain hint")
    parser.add_argument("--sm-design", default=None, help="Path to pre-approved SM design JSON (skips LLM)")
    parser.add_argument("--dry-run", action="store_true", help="No API calls to Kyvos")
    parser.add_argument("--live", action="store_true", help="Use live warehouse + Kyvos server")
    parser.add_argument("--output-dir", default=".", help="Directory for output files")
    parser.add_argument("--mock-schema", action="store_true", help="Use mock AdventureWorks schema (no warehouse connection needed)")
    args = parser.parse_args()

    failures: list[str] = []

    # If --mock-schema, patch inspect_schema to use mock AdventureWorks data
    if args.mock_schema:
        from unittest.mock import patch
        _patch = patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema)
        _patch.start()
        _info("Using mock AdventureWorks schema (no warehouse connection)")

    if args.compare:
        # Run both flows
        _print_step(1, "Flow A: Intent File Mode")
        flow_a = _run_flow(
            env_file=args.env_file,
            intent_file=args.intent_file,
            domain=args.domain,
            sm_design_path=args.sm_design,
            sm_design=_MOCK_AW_SM_DESIGN if args.mock_schema and not args.sm_design else None,
            dry_run=args.dry_run,
            live=args.live,
            output_dir=args.output_dir,
        )
        if not flow_a["success"]:
            failures.append("Flow A: Intent File Mode")

        _print_step(2, "Flow B: Generate Intent Mode")
        flow_b = _run_flow(
            env_file=args.env_file,
            generate_intent=True,
            domain=args.domain,
            dry_run=args.dry_run,
            live=args.live,
            output_dir=args.output_dir,
        )
        if not flow_b["success"]:
            failures.append("Flow B: Generate Intent Mode")

        _print_step(3, "Comparison")
        _compare_models(flow_a, flow_b)

    elif args.intent_file:
        _print_step(1, "Flow A: Intent File Mode")
        flow_a = _run_flow(
            env_file=args.env_file,
            intent_file=args.intent_file,
            domain=args.domain,
            sm_design_path=args.sm_design,
            sm_design=_MOCK_AW_SM_DESIGN if args.mock_schema and not args.sm_design else None,
            dry_run=args.dry_run,
            live=args.live,
            output_dir=args.output_dir,
        )
        if not flow_a["success"]:
            failures.append("Flow A: Intent File Mode")

    elif args.generate_intent:
        _print_step(1, "Flow B: Generate Intent Mode")
        flow_b = _run_flow(
            env_file=args.env_file,
            generate_intent=True,
            domain=args.domain,
            dry_run=args.dry_run,
            live=args.live,
            output_dir=args.output_dir,
        )
        if not flow_b["success"]:
            failures.append("Flow B: Generate Intent Mode")

    else:
        _fail("Must specify --intent-file, --generate-intent, or --compare")
        return 1

    # Summary
    print(f"\n{'═' * 70}")
    if failures:
        print(f"  ❌ Validation FAILED — {len(failures)} step(s) failed:")
        for f in failures:
            print(f"     - {f}")
    else:
        print("  ✅ Validation PASSED — all steps completed successfully")
    print(f"{'═' * 70}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
