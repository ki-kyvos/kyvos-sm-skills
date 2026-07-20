"""CLI for kyvos-sm-skills — export skill files and deploy XMLA models.

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


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="kyvos-skills",
        description="Kyvos SM Skills CLI — export skill files and deploy XMLA models",
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

    args = parser.parse_args()

    if args.command == "list":
        return cmd_list(args)
    elif args.command == "export-skill":
        return cmd_export_skill(args)
    elif args.command == "deploy":
        return cmd_deploy(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
