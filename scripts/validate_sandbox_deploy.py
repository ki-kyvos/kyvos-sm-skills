#!/usr/bin/env python3
"""Sandbox validation script for the deploy-from-xmla skill flow.

Simulates a clean-machine deployment by:
  1. Creating an isolated sandbox directory
  2. Exporting skill files from the installed package
  3. Copying the XMLA model + .env config into the sandbox
  4. Verifying the workspace layout
  5. Running a dry-run (parse only)
  6. Running a full live deployment (optional, --live)
  7. Cleaning up the sandbox

No source code checkout is required — only pip-installed packages.

Usage:
    # Dry run only (no API calls, no server needed)
    python scripts/validate_sandbox_deploy.py \
        --xmla-path /path/to/AdventureWorks.xmla \
        --env-file /path/to/.env

    # Full live deployment (creates real entities on Kyvos)
    python scripts/validate_sandbox_deploy.py \
        --xmla-path /path/to/AdventureWorks.xmla \
        --env-file /path/to/.env \
        --live

    # Keep the sandbox directory after completion (for inspection)
    python scripts/validate_sandbox_deploy.py \
        --xmla-path /path/to/AdventureWorks.xmla \
        --env-file /path/to/.env \
        --live \
        --keep-sandbox

    # Use a specific sandbox path
    python scripts/validate_sandbox_deploy.py \
        --xmla-path /path/to/AdventureWorks.xmla \
        --env-file /path/to/.env \
        --sandbox-dir /tmp/my-sandbox

    # Export all skills (not just deploy-from-xmla)
    python scripts/validate_sandbox_deploy.py \
        --xmla-path /path/to/AdventureWorks.xmla \
        --env-file /path/to/.env \
        --export-all-skills

Prerequisites:
    pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ── Helpers ─────────────────────────────────────────────────────────────────


def _print_header(msg: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  {msg}")
    print(f"{'═' * 70}")


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


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and stream output."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
    return result


# ── Default paths ───────────────────────────────────────────────────────────

_CASCADE_PROJECTS = Path(__file__).resolve().parents[2]
DEFAULT_XMLA_PATHS = [
    _CASCADE_PROJECTS / "agentic-ai-demo-automation" / "samples" / "xmla" / "AdventureWorks.xmla",
    _CASCADE_PROJECTS / "kyvos-sm-skills" / "samples" / "xmla" / "AdventureWorks.xmla",
]
DEFAULT_ENV_PATHS = [
    _CASCADE_PROJECTS / "kyvos-sdk-python" / ".env",
    Path.cwd() / ".env",
]


def _resolve_path(arg: str | None, defaults: list[Path], label: str) -> Path:
    if arg:
        p = Path(arg).resolve()
        if not p.exists():
            raise FileNotFoundError(f"{label} not found: {p}")
        return p
    for d in defaults:
        if d.exists():
            return d
    raise FileNotFoundError(
        f"{label} not found. Searched: {[str(d) for d in defaults]}. "
        f"Pass --{label.lower().replace(' ', '-')} explicitly."
    )


# ── Main validation flow ────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sandbox validation for the deploy-from-xmla skill flow.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--xmla-path",
        default=None,
        help="Path to the .xmla file (auto-detected if omitted)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to the .env config file (auto-detected if omitted)",
    )
    parser.add_argument(
        "--sandbox-dir",
        default=None,
        help="Sandbox directory path (default: temp dir)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run full live deployment (creates real entities on Kyvos)",
    )
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        help="Keep the sandbox directory after completion (for inspection)",
    )
    parser.add_argument(
        "--export-all-skills",
        action="store_true",
        help="Export all skill files, not just deploy-from-xmla",
    )
    parser.add_argument(
        "--payload-format",
        default=None,
        choices=["json", "xml"],
        help="Override payload format",
    )
    parser.add_argument(
        "--skill-name",
        default="deploy-from-xmla",
        help="Skill to export and validate (default: deploy-from-xmla)",
    )
    args = parser.parse_args()

    # ── Resolve input paths ─────────────────────────────────────────────────
    xmla_path = _resolve_path(args.xmla_path, DEFAULT_XMLA_PATHS, "XMLA file")
    env_path = _resolve_path(args.env_file, DEFAULT_ENV_PATHS, "Env file")

    _print_header("Sandbox Validation: deploy-from-xmla skill flow")
    _info(f"XMLA file : {xmla_path}")
    _info(f"Env file  : {env_path}")
    _info(f"Live mode : {args.live}")
    _info(f"Skill     : {args.skill_name}")

    # ── Verify prerequisites ───────────────────────────────────────────────
    _print_step(0, "Verify prerequisites")

    try:
        import kyvos_sdk
        _ok(f"kyvos-sdk-python v{kyvos_sdk.__version__}")
    except ImportError:
        _fail("kyvos-sdk-python not installed. Run: pip install kyvos-sdk-python[env]")
        return 1

    try:
        import kyvos_sm_skills
        _ok(f"kyvos-sm-skills v{kyvos_sm_skills.__version__}")
    except ImportError:
        _fail("kyvos-sm-skills not installed. Run: pip install kyvos-sm-skills[sdk]")
        return 1

    try:
        import kyvos_xmla_parser
        _ok("kyvos-xmla-parser installed")
    except ImportError:
        _fail("kyvos-xmla-parser not installed. Run: pip install kyvos-xmla-parser")
        return 1

    # ── Create sandbox ─────────────────────────────────────────────────────
    _print_step(1, "Create sandbox directory")

    if args.sandbox_dir:
        sandbox = Path(args.sandbox_dir).resolve()
        if sandbox.exists():
            shutil.rmtree(sandbox)
        sandbox.mkdir(parents=True)
    else:
        sandbox = Path(tempfile.mkdtemp(prefix="kyvos-sandbox-"))

    _ok(f"Sandbox created: {sandbox}")

    # ── Export skill files ─────────────────────────────────────────────────
    _print_step(2, f"Export skill files from installed package")

    # Find the kyvos-skills CLI
    kyvos_skills_bin = shutil.which("kyvos-skills")
    if not kyvos_skills_bin:
        # Try the venv bin
        venv_bin = Path(sys.prefix) / "bin" / "kyvos-skills"
        if venv_bin.exists():
            kyvos_skills_bin = str(venv_bin)
        else:
            _fail("kyvos-skills CLI not found. Ensure kyvos-sm-skills is installed.")
            return 1

    if args.export_all_skills:
        _run([kyvos_skills_bin, "export-skill", "--all", "-o", str(sandbox)])
    else:
        _run([kyvos_skills_bin, "export-skill", args.skill_name, "-o", str(sandbox)])
    _ok(f"Skill files exported to {sandbox}")

    # ── Copy XMLA + .env ───────────────────────────────────────────────────
    _print_step(3, "Copy XMLA model + .env config into sandbox")

    shutil.copy2(xmla_path, sandbox / xmla_path.name)
    _ok(f"XMLA copied: {xmla_path.name}")

    shutil.copy2(env_path, sandbox / ".env")
    _ok(".env copied")

    # ── Verify workspace layout ────────────────────────────────────────────
    _print_step(4, "Verify sandbox workspace layout")

    expected_files = {
        ".env": "Config file",
        xmla_path.name: "XMLA model",
        f"{args.skill_name}.md": "Skill definition",
    }

    all_present = True
    for filename, label in expected_files.items():
        fpath = sandbox / filename
        if fpath.exists():
            size = fpath.stat().st_size
            _ok(f"{label}: {filename} ({size:,} bytes)")
        else:
            _fail(f"{label} missing: {filename}")
            all_present = False

    shared_dir = sandbox / "_shared"
    if shared_dir.exists():
        shared_files = list(shared_dir.glob("*.md"))
        _ok(f"Shared resources: {len(shared_files)} file(s)")
        for f in shared_files:
            print(f"       _shared/{f.name}")
    else:
        _info("No _shared/ directory (OK if skill has no shared resources)")

    if not all_present:
        _fail("Workspace layout verification failed")
        return 1

    _ok("Workspace layout verified")

    # Print the tree
    print(f"\n  Sandbox tree:")
    for f in sorted(sandbox.rglob("*")):
        if f.is_file():
            rel = f.relative_to(sandbox)
            print(f"    {rel}")

    # ── Create prompt file ─────────────────────────────────────────────────
    _print_step(5, "Create prompt file (for Claude Code non-interactive mode)")

    prompt_text = (
        f"Read the skill file at {args.skill_name}.md in this directory. "
        f"Then deploy the Adventure Works XMLA model at {xmla_path.name} to Kyvos. "
        f"My .env is at .env in the current directory."
    )
    prompt_file = sandbox / "prompt.txt"
    prompt_file.write_text(prompt_text + "\n")
    _ok(f"Prompt file created: prompt.txt")
    print(f"       Content: \"{prompt_text}\"")

    # ── Dry run ────────────────────────────────────────────────────────────
    _print_step(6, "Dry run (parse only, no API calls)")

    deploy_cmd = [
        kyvos_skills_bin,
        "deploy",
        "--xmla-path", xmla_path.name,
        "--env-file", ".env",
        "--dry-run",
    ]
    if args.payload_format:
        deploy_cmd.extend(["--payload-format", args.payload_format])

    result = _run(deploy_cmd, cwd=str(sandbox))
    if result.returncode == 0:
        _ok("Dry run passed — XMLA parsing and config loading verified")
    else:
        _fail("Dry run failed")
        if not args.keep_sandbox:
            shutil.rmtree(sandbox)
        return 1

    # ── Live deployment ────────────────────────────────────────────────────
    if args.live:
        _print_step(7, "Live deployment (creates real entities on Kyvos)")

        deploy_cmd_live = [
            kyvos_skills_bin,
            "deploy",
            "--xmla-path", xmla_path.name,
            "--env-file", ".env",
        ]
        if args.payload_format:
            deploy_cmd_live.extend(["--payload-format", args.payload_format])

        result = _run(deploy_cmd_live, cwd=str(sandbox))
        if result.returncode == 0:
            _ok("Live deployment succeeded — all 9 steps completed")
        else:
            _fail("Live deployment failed")
            if not args.keep_sandbox:
                shutil.rmtree(sandbox)
            return 1
    else:
        _print_step(7, "Live deployment (skipped — use --live to enable)")
        _info("Skipping live deployment. Use --live to run full deployment.")

    # ── Report ─────────────────────────────────────────────────────────────
    _print_header("Sandbox Validation Complete")

    print(f"""
  Sandbox path : {sandbox}
  XMLA file    : {xmla_path.name}
  Skill        : {args.skill_name}.md
  Dry run      : ✅ Passed
  Live deploy  : {'✅ Passed' if args.live else '⏭️  Skipped (--live not set)'}
  Sandbox kept : {'Yes' if args.keep_sandbox else 'No (cleaned up)'}

  To run with Claude Code non-interactive mode:
    cd {sandbox}
    claude --print "$(cat prompt.txt)"

  To run with kyvos-skills CLI:
    cd {sandbox}
    kyvos-skills deploy --xmla-path {xmla_path.name} --env-file .env
""")

    # ── Cleanup ────────────────────────────────────────────────────────────
    if not args.keep_sandbox:
        shutil.rmtree(sandbox)
        _info(f"Sandbox cleaned up: {sandbox}")
    else:
        _info(f"Sandbox preserved for inspection: {sandbox}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
