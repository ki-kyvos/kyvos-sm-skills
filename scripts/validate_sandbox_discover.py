#!/usr/bin/env python3
"""Sandbox validation script for the discover-sm-from-warehouse skill flow.

Simulates a clean-machine discover+deploy by:
  1. Creating an isolated sandbox directory
  2. Exporting skill files from the installed package
  3. Copying the .env config + SM design JSON into the sandbox
  4. Verifying the workspace layout
  5. Running a dry-run (inspect + build spec only)
  6. Running a full live deployment (optional, --live)
  7. Cleaning up the sandbox

No source code checkout is required — only pip-installed packages.

Usage:
    # Dry run only (no API calls, no server needed)
    python scripts/validate_sandbox_discover.py \
        --env-file /path/to/.env \
        --sm-design /path/to/sm-design.json

    # Full live deployment (creates real entities on Kyvos)
    python scripts/validate_sandbox_discover.py \
        --env-file /path/to/.env \
        --sm-design /path/to/sm-design.json \
        --live

    # LLM mode (uses Anthropic API to generate SM design)
    python scripts/validate_sandbox_discover.py \
        --env-file /path/to/.env \
        --user-intent "I want sales analytics for Adventure Works" \
        --domain adventure_works \
        --live

    # Keep the sandbox directory after completion (for inspection)
    python scripts/validate_sandbox_discover.py \
        --env-file /path/to/.env \
        --sm-design /path/to/sm-design.json \
        --live \
        --keep-sandbox

Prerequisites:
    pip install kyvos-sdk-python[env,inspect] kyvos-sm-skills[sdk,anthropic]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _print_step(step: int, msg: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  Step {step}: {msg}")
    print(f"{'═' * 70}")


def _print_ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _print_err(msg: str) -> None:
    print(f"  ✗ {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sandbox validation for discover-sm-from-warehouse skill flow",
    )
    parser.add_argument("--env-file", required=True, help="Path to .env config file")
    parser.add_argument("--sm-design", default=None, help="Path to pre-approved SM design JSON file")
    parser.add_argument("--user-intent", default=None, help="Natural language analytics intent (LLM mode)")
    parser.add_argument("--domain", default=None, help="Domain hint for LLM mode")
    parser.add_argument("--schema", default=None, help="Warehouse schema to inspect")
    parser.add_argument("--max-tables", type=int, default=500, help="Max tables to inspect")
    parser.add_argument("--payload-format", default=None, choices=["json", "xml"], help="Override payload format")
    parser.add_argument("--sandbox-dir", default=None, help="Custom sandbox directory path")
    parser.add_argument("--keep-sandbox", action="store_true", help="Keep sandbox dir after completion")
    parser.add_argument("--live", action="store_true", help="Run full live deployment (creates real entities)")
    parser.add_argument("--export-all-skills", action="store_true", help="Export all skill files, not just discover")
    args = parser.parse_args()

    # Validate args
    if not args.sm_design and not args.user_intent:
        _print_err("Either --sm-design or --user-intent must be provided.")
        return 1

    sandbox_dir = Path(args.sandbox_dir) if args.sandbox_dir else Path(tempfile.mkdtemp(prefix="kyvos-discover-sandbox-"))

    try:
        # ── Step 1: Create sandbox ──
        _print_step(1, f"Create sandbox directory")
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Sandbox: {sandbox_dir}")
        _print_ok(f"Sandbox created at {sandbox_dir}")

        # ── Step 2: Export skill files ──
        _print_step(2, "Export skill files from installed package")
        skills_dir = sandbox_dir / "skills"
        skills_dir.mkdir(exist_ok=True)

        cmd = [sys.executable, "-m", "kyvos_sm_skills.cli", "export-skill"]
        if args.export_all_skills:
            cmd.append("--all")
            cmd.extend(["-o", str(skills_dir)])
        else:
            cmd.append("discover-sm-from-warehouse")
            cmd.extend(["-o", str(skills_dir)])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            _print_err(f"Skill export failed: {result.stderr}")
            return 1
        _print_ok(f"Skill files exported to {skills_dir}")

        # Also export the inspect-warehouse-schema skill
        cmd_inspect = [sys.executable, "-m", "kyvos_sm_skills.cli", "export-skill",
                       "inspect-warehouse-schema", "-o", str(skills_dir)]
        result_inspect = subprocess.run(cmd_inspect, capture_output=True, text=True)
        if result_inspect.returncode == 0:
            _print_ok("inspect-warehouse-schema skill exported")

        # ── Step 3: Copy config + SM design into sandbox ──
        _print_step(3, "Copy config and SM design into sandbox")

        env_dest = sandbox_dir / ".env"
        shutil.copy2(args.env_file, env_dest)
        _print_ok(f".env copied to {env_dest}")

        sm_design_dest = None
        if args.sm_design:
            sm_design_dest = sandbox_dir / "sm-design.json"
            shutil.copy2(args.sm_design, sm_design_dest)
            _print_ok(f"SM design copied to {sm_design_dest}")

        # ── Step 4: Verify workspace layout ──
        _print_step(4, "Verify workspace layout")

        required_files = [env_dest]
        if sm_design_dest:
            required_files.append(sm_design_dest)

        for f in required_files:
            if not f.exists():
                _print_err(f"Missing: {f}")
                return 1
            _print_ok(f"Present: {f.name}")

        skill_files = list(skills_dir.glob("*.md"))
        if not skill_files:
            _print_err("No skill files found in sandbox")
            return 1
        _print_ok(f"{len(skill_files)} skill file(s) present")

        # ── Step 5: Dry run ──
        _print_step(5, "Run dry-run (inspect + build spec only)")

        cmd = [
            sys.executable, "-m", "kyvos_sm_skills.cli", "discover",
            "--env-file", str(env_dest),
            "--dry-run",
        ]
        if sm_design_dest:
            cmd.extend(["--sm-design", str(sm_design_dest)])
        if args.user_intent:
            cmd.extend(["--user-intent", args.user_intent])
        if args.domain:
            cmd.extend(["--domain", args.domain])
        if args.schema:
            cmd.extend(["--schema", args.schema])
        if args.payload_format:
            cmd.extend(["--payload-format", args.payload_format])
        cmd.extend(["--max-tables", str(args.max_tables)])

        print(f"  Command: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(sandbox_dir))
        if result.returncode != 0:
            _print_err(f"Dry run failed with exit code {result.returncode}")
            return 1
        _print_ok("Dry run completed successfully")

        if not args.live:
            print(f"\n{'═' * 70}")
            print(f"  ✅ Sandbox validation (dry run) complete!")
            print(f"  Sandbox: {sandbox_dir}")
            print(f"{'═' * 70}")
            return 0

        # ── Step 6: Live deployment ──
        _print_step(6, "Run live deployment")

        cmd_live = [
            sys.executable, "-m", "kyvos_sm_skills.cli", "discover",
            "--env-file", str(env_dest),
            "--auto-approve",
        ]
        if sm_design_dest:
            cmd_live.extend(["--sm-design", str(sm_design_dest)])
        if args.user_intent:
            cmd_live.extend(["--user-intent", args.user_intent])
        if args.domain:
            cmd_live.extend(["--domain", args.domain])
        if args.schema:
            cmd_live.extend(["--schema", args.schema])
        if args.payload_format:
            cmd_live.extend(["--payload-format", args.payload_format])
        cmd_live.extend(["--max-tables", str(args.max_tables)])

        print(f"  Command: {' '.join(cmd_live)}")
        result = subprocess.run(cmd_live, cwd=str(sandbox_dir))
        if result.returncode != 0:
            _print_err(f"Live deployment failed with exit code {result.returncode}")
            return 1
        _print_ok("Live deployment completed successfully")

        print(f"\n{'═' * 70}")
        print(f"  ✅ Sandbox validation (live) complete!")
        print(f"  Sandbox: {sandbox_dir}")
        print(f"{'═' * 70}")

        return 0

    finally:
        if not args.keep_sandbox:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
            print(f"\n  Sandbox cleaned up: {sandbox_dir}")
        else:
            print(f"\n  Sandbox kept at: {sandbox_dir}")


if __name__ == "__main__":
    sys.exit(main())
