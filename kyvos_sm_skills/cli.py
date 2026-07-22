"""CLI for kyvos-sm-skills — export skill files, deploy XMLA models, and discover SMs.

Usage:
    # List available skills
    kyvos-skills list

    # Export a skill file to the current directory
    kyvos-skills export-skill deploy-from-xmla

    # Export all skills to a directory
    kyvos-skills export-skill --all -o ./my-skills

    # Deploy an XMLA model (no Claude needed — runs the skill flow directly)
    kyvos-skills deploy --xmla-path ./AdventureWorks.xmla --env-file ./.env

    # Dry run (parse only, no API calls)
    kyvos-skills deploy --xmla-path ./AdventureWorks.xmla --env-file ./.env --dry-run

    # Discover SM from warehouse (pre-approved JSON mode)
    kyvos-skills discover --env-file ./.env --sm-design ./sm-design.json --dry-run

    # Discover SM from warehouse (LLM mode via Anthropic API)
    kyvos-skills discover --env-file ./.env --user-intent "I want sales analytics" --domain adventure_works
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _skills_dir() -> Path:
    """Return the directory where skill .md files are bundled in the package."""
    return Path(__file__).resolve().parent / "skills"


def cmd_list(args: argparse.Namespace) -> int:
    skills = _skills_dir()
    if not skills.exists():
        print("No skill files found in installed package.")
        return 1
    md_files = sorted(skills.glob("*.md"))
    if not md_files:
        print("No skill files found.")
        return 1
    print("Available skills:")
    for f in md_files:
        name = f.stem
        size = f.stat().st_size
        print(f"  {name:<40s} ({size:,d} bytes)")
    shared = skills / "_shared"
    if shared.exists():
        shared_files = sorted(shared.glob("*.md"))
        if shared_files:
            print(f"\nShared resources ({len(shared_files)} files):")
            for f in shared_files:
                print(f"  _shared/{f.name}")
    return 0


def cmd_export_skill(args: argparse.Namespace) -> int:
    skills = _skills_dir()
    if not skills.exists():
        print("No skill files found in installed package.")
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        count = 0
        for f in skills.glob("*.md"):
            dest = output_dir / f.name
            shutil.copy2(f, dest)
            print(f"  Exported: {dest}")
            count += 1
        shared = skills / "_shared"
        if shared.exists():
            dest_shared = output_dir / "_shared"
            dest_shared.mkdir(exist_ok=True)
            for f in shared.glob("*.md"):
                dest = dest_shared / f.name
                shutil.copy2(f, dest)
                print(f"  Exported: {dest}")
                count += 1
        print(f"\nExported {count} skill file(s) to {output_dir}/")
        return 0

    skill_name = args.skill_name
    skill_file = skills / f"{skill_name}.md"
    if not skill_file.exists():
        print(f"Skill '{skill_name}' not found. Available skills:")
        for f in skills.glob("*.md"):
            print(f"  {f.stem}")
        return 1

    dest = output_dir / skill_file.name
    shutil.copy2(skill_file, dest)
    print(f"Exported: {dest}")

    shared = skills / "_shared"
    if shared.exists():
        dest_shared = output_dir / "_shared"
        dest_shared.mkdir(exist_ok=True)
        for f in shared.glob("*.md"):
            shutil.copy2(f, dest_shared / f.name)
        print(f"  (also exported _shared/ resources)")

    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Run the deploy-from-xmla skill flow directly (no Claude needed)."""
    from kyvos_sm_skills.skill_runner import run_deploy_from_xmla

    return run_deploy_from_xmla(
        xmla_file_path=args.xmla_path,
        env_file=args.env_file,
        payload_format=args.payload_format,
        dry_run=args.dry_run,
        live=True,
    )


def cmd_discover(args: argparse.Namespace) -> int:
    """Run the discover-sm-from-warehouse skill flow directly."""
    from kyvos_sm_skills.skill_runner import run_discover_sm_from_warehouse

    # Cleanup is the default behavior — old entities are deleted before deploying.
    # --cleanup-dry-run overrides to list-only mode.
    # --cleanup is kept for explicitness but is the default.
    _cleanup_dry_run = args.cleanup_dry_run
    _perform_cleanup = not args.cleanup_dry_run

    # Handle --generate-intent: auto-generate intent from schema
    _user_intent = args.user_intent
    if args.generate_intent:
        from kyvos_sm_skills.intent_generator import generate_intent_from_file
        from kyvos_sdk.warehouse_inspector import inspect_schema
        from kyvos_sdk.config import KyvosConfig

        config = KyvosConfig.from_env_file(args.env_file)
        schema_summary = inspect_schema(config, schema_filter=args.schema, max_tables=args.max_tables)

        intent_path = args.intent_output or f"intent_{args.domain or 'auto'}.txt"
        print(f"  Generating intent via LLM from schema analysis...")
        _user_intent = generate_intent_from_file(
            intent_path=intent_path,
            schema_summary=schema_summary,
            domain=args.domain,
        )
        print(f"  Intent saved to: {intent_path}")
        print(f"  Generated intent preview (first 200 chars): {_user_intent[:200]}...")

    return run_discover_sm_from_warehouse(
        env_file=args.env_file,
        sm_design_path=args.sm_design,
        user_intent=_user_intent,
        domain=args.domain,
        allow_web_research=not args.no_web_research,
        auto_approve=args.auto_approve,
        schema_filter=args.schema,
        max_tables=args.max_tables,
        payload_format=args.payload_format,
        dry_run=args.dry_run,
        cleanup_dry_run=_cleanup_dry_run,
        perform_cleanup=_perform_cleanup,
        sm_folder_suffix=args.sm_folder_suffix,
    )


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Clean up old entities from Kyvos matching the base name."""
    from kyvos_sm_skills.skill_runner import cleanup_entities

    return cleanup_entities(
        env_file=args.env_file,
        base_name=args.base_name,
        dry_run=args.dry_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="kyvos-skills",
        description="Kyvos SM Skills CLI — export skill files, deploy XMLA models, and discover SMs",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="List available skill files")

    # export-skill
    exp = sub.add_parser("export-skill", help="Export skill .md file(s) to disk")
    exp.add_argument("skill_name", nargs="?", default=None, help="Skill name (e.g., deploy-from-xmla)")
    exp.add_argument("--all", action="store_true", help="Export all skill files")
    exp.add_argument("-o", "--output-dir", default=None, help="Output directory (default: current dir)")

    # deploy
    dep = sub.add_parser("deploy", help="Run the deploy-from-xmla skill flow directly")
    dep.add_argument("--xmla-path", required=True, help="Path to the .xmla file")
    dep.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    dep.add_argument("--payload-format", default=None, choices=["json", "xml"], help="Override payload format")
    dep.add_argument("--dry-run", action="store_true", help="Parse + compile only, no API calls")

    # discover
    dis = sub.add_parser("discover", help="Run the discover-sm-from-warehouse skill flow")
    dis.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    dis.add_argument("--sm-design", default=None, help="Path to pre-approved SM design JSON file")
    dis.add_argument("--user-intent", default=None, help="Natural language analytics intent (triggers LLM mode)")
    dis.add_argument("--domain", default=None, help="Domain hint (e.g., adventure_works, retail_ecommerce)")
    dis.add_argument("--no-web-research", action="store_true", help="Disable web research in LLM mode")
    dis.add_argument("--auto-approve", action="store_true", help="Skip interactive approval gate (for CI/CD)")
    dis.add_argument("--schema", default=None, help="Warehouse schema to inspect (default: per warehouse type)")
    dis.add_argument("--max-tables", type=int, default=500, help="Max tables to inspect (default: 500)")
    dis.add_argument("--payload-format", default=None, choices=["json", "xml"], help="Override payload format")
    dis.add_argument("--dry-run", action="store_true", help="Inspect + build spec only, no API calls")
    dis.add_argument("--cleanup-dry-run", action="store_true", help="List old entities that would be deleted, without actually deleting them (cleanup is default)")
    dis.add_argument("--cleanup", action="store_true", help="Explicitly enable cleanup (this is the default; use --cleanup-dry-run to preview instead)")
    dis.add_argument("--sm-folder-suffix", default="", help="Suffix for SM folder name to avoid conflicts when deploying multiple SMs to the same schema (e.g., 'B' for awdw2019multidimensionalee_SModelB)")
    dis.add_argument("--generate-intent", action="store_true", help="Auto-generate user intent via LLM from schema analysis (replaces --user-intent)")
    dis.add_argument("--intent-output", default=None, help="Path to save the generated intent (default: intent_<domain>.txt)")

    # cleanup
    cln = sub.add_parser("cleanup", help="Clean up old entities from Kyvos matching the base name")
    cln.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    cln.add_argument("--base-name", default=None, help="Base name to derive cleanup prefixes from (default: from warehouse database name)")
    cln.add_argument("--dry-run", action="store_true", help="List entities that would be deleted, without actually deleting them (recommended first)")

    args = parser.parse_args()

    if args.command == "list":
        return cmd_list(args)
    elif args.command == "export-skill":
        return cmd_export_skill(args)
    elif args.command == "deploy":
        return cmd_deploy(args)
    elif args.command == "discover":
        return cmd_discover(args)
    elif args.command == "cleanup":
        return cmd_cleanup(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
