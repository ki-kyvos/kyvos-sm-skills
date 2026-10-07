"""Programmatic runners for Kyvos skill flows.

This module mirrors the code in the skill ``.md`` files exactly —
no custom logic.  It allows running the full deployment pipeline without
Claude Code, using only the pip-installed packages.

Used by:
    - ``kyvos-skills deploy`` CLI command
    - ``kyvos-skills discover`` CLI command
    - ``validate_skill_flow_adventureworks.py`` script
    - ``validate_sandbox_deploy.py`` script
    - Any Python script that wants to run a skill flow
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from kyvos_sdk.contracts.common import Severity
    from kyvos_sdk.contracts.results import OperationStatus
except ImportError:
    from enum import Enum

    class Severity(str, Enum):
        ERROR = "error"
        WARNING = "warning"
        INFO = "info"

    class OperationStatus(str, Enum):
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        TIMED_OUT = "timed_out"

from kyvos_sm_skills.spec_builder import DiscoveredSpec

_MIN_PREFIX_LEN = 8


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 32) -> int:
    """Read a bounded integer setting from the environment."""
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _safe_input(prompt: str) -> str:
    """Prompt for user input, returning empty string on EOF in non-interactive environments."""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        print("\n  (Non-interactive environment detected — defaulting to rejection.)")
        return ""


def _derive_cleanup_prefixes(base_name: str) -> tuple[str, ...]:
    """Derive cleanup match prefixes from a base name.

    Only returns prefixes that are at least _MIN_PREFIX_LEN characters long
    to avoid accidentally matching unrelated entities on the Kyvos server.

    For "AdventureWorks_Discovered_SM" this yields
    ("adventureworks_discovered_sm", "adventureworks").
    For "awdw2019multidimensionalee" this yields
    ("awdw2019multidimensionalee",) — the first part is the full string
    so no separate first-part prefix is added.
    """
    lower = base_name.lower().lstrip("_").replace(" ", "_")
    parts = lower.split("_")
    prefixes = [lower]
    # Add the first meaningful part only if it's long enough to be specific
    # (e.g., "adventureworks" from "adventureworks_discovered_sm")
    if parts and len(parts[0]) >= _MIN_PREFIX_LEN and parts[0] != lower:
        prefixes.append(parts[0])
    # Filter out any prefix shorter than the minimum length
    prefixes = [p for p in prefixes if len(p) >= _MIN_PREFIX_LEN]
    return tuple(dict.fromkeys(prefixes))  # dedupe preserving order


def _get_protected_folders() -> set[str]:
    """Read protected folder names from KYVOS_PROTECTED_FOLDERS env var.

    Returns a set of lowercase folder names that should never be deleted.
    Format: comma-separated list, e.g., "shared,templates,system,production"
    """
    raw = os.environ.get("KYVOS_PROTECTED_FOLDERS", "")
    if not raw.strip():
        return set()
    return {f.strip().lower() for f in raw.split(",") if f.strip()}


def _check_prefix_collision(prefixes: tuple[str, ...]) -> list[str]:
    """Check for prefix collisions that could match unrelated entities.

    Returns a list of warning messages for prefixes that are too generic
    (shorter than _MIN_PREFIX_LEN or matching common generic names).
    """
    warnings = []
    generic_names = {"dataset", "smodel", "drd", "folder", "test", "demo", "sample"}
    for p in prefixes:
        if p in generic_names:
            warnings.append(
                f"Prefix '{p}' is a generic name that may match unrelated entities."
            )
    return warnings


def _write_audit_log(
    targets: list[tuple[str, str, str, str]],
    deleted: int,
    base_name: str,
    prefixes: tuple[str, ...],
    dry_run: bool,
) -> str:
    """Write an audit log of cleanup actions.

    Returns the path to the audit log file.
    """
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    log_path = f"cleanup_{timestamp}.log"

    with open(log_path, "w") as f:
        f.write("Cleanup Audit Log\n")
        f.write(f"Timestamp: {now.isoformat()}\n")
        f.write(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")
        f.write(f"Base name: {base_name}\n")
        f.write(f"Prefixes: {list(prefixes)}\n")
        f.write(f"Entities found: {len(targets)}\n")
        f.write(f"Entities deleted: {deleted}\n")
        f.write("\n--- Entity Details ---\n")
        for etype, ename, eid, folder in targets:
            status = "DRY_RUN" if dry_run else "DELETED"
            f.write(f"  [{etype:8s}] {ename} (id={eid}) in '{folder}' — {status}\n")

    return log_path


def _collect_and_cleanup_entities(
    *,
    insp: Any,
    prov: Any,
    base_name: str,
    dry_run: bool = False,
    skip_folders: set[str] | None = None,
    extra_prefixes: tuple[str, ...] = (),
    auto_approve: bool = False,
    restrict_smodel_folder: str | None = None,
    folder_suffix: str = "",
) -> bool:
    """Collect and optionally delete old entities matching the base_name prefixes.

    Scans all RDATASET, DATASET_RELATIONSHIP, and SMODEL folders whose names
    start with any derived prefix, and collects every entity (dataset, DRD,
    or semantic model) inside each matching folder. The folder match is the
    containment boundary — entity names (e.g. dataset names mirroring
    warehouse table names) are not required to also start with the prefix,
    since folders are created per-flow and everything inside one belongs to
    that flow.

    Safety features:
    - Protected folders (from KYVOS_PROTECTED_FOLDERS env var) are never deleted.
    - Prefix collision warning aborts if a prefix is too generic.
    - Confirmation gate requires user input even with auto_approve for live deletes.
    - Audit log is written for every cleanup run.
    - When folder_suffix is provided, only folders ending with _{suffix} are
      matched, preventing cross-flow cleanup from deleting other flows' entities.

    Args:
        insp: InspectionClient instance.
        prov: ProvisioningClient instance.
        base_name: Base name to derive cleanup prefixes from.
        dry_run: If True, only list entities; if False, delete them.
        skip_folders: Set of folder names to skip (e.g., the stable folders
                      we're about to reuse — those are cleaned separately).
        extra_prefixes: Additional prefixes to match (e.g., derived from the
                        LLM-generated SM name to catch entities from previous
                        runs that used different naming conventions).
        auto_approve: If True, skip interactive confirmation gate (for CI/CD).
        folder_suffix: When provided, only match folders whose names end with
                       _{suffix} (case-insensitive).  This scopes cleanup to
                       the current flow's entities only, preventing deletion of
                       other flows' entities that share the same base prefix.

    Returns:
        True if any entities were deleted (and a delay is warranted), False otherwise.
    """
    from kyvos_sdk.contracts.identity import FolderType

    prefixes = _derive_cleanup_prefixes(base_name)
    if extra_prefixes:
        # Combine and dedupe
        all_prefixes = list(prefixes) + list(extra_prefixes)
        # Defense in depth: filter out any prefix shorter than the minimum length
        # even if extra_prefixes somehow contains short strings
        filtered = [p for p in all_prefixes if len(p) >= _MIN_PREFIX_LEN]
        skipped = [p for p in all_prefixes if len(p) < _MIN_PREFIX_LEN]
        if skipped:
            print(f"  WARNING: Skipping {len(skipped)} prefix(es) shorter than {_MIN_PREFIX_LEN} chars: {skipped}")
        prefixes = tuple(dict.fromkeys(filtered))

    # Check for prefix collisions with generic names
    collision_warnings = _check_prefix_collision(prefixes)
    if collision_warnings:
        print("\n  ⚠️  PREFIX COLLISION WARNING:")
        for w in collision_warnings:
            print(f"    {w}")
        if not dry_run:
            print("  Aborting cleanup due to prefix collision risk.")
            return False

    # Merge skip_folders with protected folders
    protected = _get_protected_folders()
    if protected:
        print(f"  Protected folders: {sorted(protected)}")
    skip_folders = (skip_folders or set()) | protected

    _suffix_lower = folder_suffix.lower().lstrip() if folder_suffix else ""

    def _matches(name: str) -> bool:
        lower = name.lower().lstrip()
        if _suffix_lower:
            return any(
                lower.startswith(p) and lower.endswith(f"_{_suffix_lower}")
                for p in prefixes
            )
        return any(lower.startswith(p) for p in prefixes)

    print(f"  Scanning for old entities matching prefixes: {list(prefixes)} ...")

    targets = []
    for ft, entity_label in [
        (FolderType.RDATASET, "DATASET"),
        (FolderType.DATASET_RELATIONSHIP, "DRD"),
        (FolderType.SMODEL, "SMODEL"),
    ]:
        list_result = insp.list_folders(ft)
        if not list_result.succeeded or not list_result.entity_refs:
            continue
        for ref in list_result.entity_refs:
            folder_name = ref.name
            if folder_name in skip_folders:
                continue
            # Only scan folders whose names match the cleanup prefixes.
            # This avoids listing entities from unrelated folders (BFSI, Healthcare, etc.)
            if not _matches(folder_name):
                continue
            # Folder matches — collect entities inside it. Entity names
            # (e.g. dataset names mirroring warehouse table names) are not
            # expected to carry the SM base_name prefix themselves, so the
            # folder match (already hardened via _MIN_PREFIX_LEN, prefix
            # collision checks, protected folders, and folder_suffix scoping)
            # is the real containment boundary for "does this entity belong
            # to this cleanup run".
            if ft == FolderType.RDATASET:
                ds_list = insp.list_datasets_in_folder(folder_name)
                if ds_list.succeeded and ds_list.entity_refs:
                    for ds_ref in ds_list.entity_refs:
                        targets.append(("DATASET", ds_ref.name, ds_ref.id, folder_name))
            elif ft == FolderType.DATASET_RELATIONSHIP:
                drd_list = insp.list_drds_in_folder(folder_name)
                if drd_list.succeeded and drd_list.entity_refs:
                    for drd_ref in drd_list.entity_refs:
                        targets.append(("DRD", drd_ref.name, drd_ref.id, folder_name))
            elif ft == FolderType.SMODEL:
                if restrict_smodel_folder and folder_name != restrict_smodel_folder:
                    continue
                sm_list = insp.list_smodels_in_folder(folder_name)
                if sm_list.succeeded and sm_list.entity_refs:
                    for sm_ref in sm_list.entity_refs:
                        targets.append(("SMODEL", sm_ref.name, sm_ref.id, folder_name))
            # The folder itself matches, so mark it for deletion
            targets.append(("FOLDER", folder_name, ref.id, ft.value))

    if not targets:
        print(f"  No old entities found matching prefixes {list(prefixes)}.")
        return False

    print(f"\n  Found {len(targets)} entity(ies) to clean up:")
    for etype, ename, eid, folder in targets:
        print(f"    [{etype:8s}] {ename} (id={eid}) in folder '{folder}'")

    if dry_run:
        print("\n  DRY RUN: No entities were deleted.")
        # Write audit log even for dry runs
        log_path = _write_audit_log(targets, 0, base_name, prefixes, dry_run=True)
        print(f"  Audit log written to: {log_path}")
        return False

    # Confirmation gate — even with auto_approve, warn for live deletes
    if not auto_approve:
        print(f"\n  ⚠️  About to delete {len(targets)} entities. This cannot be undone.")
        response = _safe_input("  Type 'yes' to proceed: ")
        if response != "yes":
            print("  Cleanup aborted by user.")
            log_path = _write_audit_log(targets, 0, base_name, prefixes, dry_run=False)
            print(f"  Audit log written to: {log_path}")
            return False
    else:
        print(f"\n  Auto-approved: skipping confirmation gate for {len(targets)} entities.")

    print("\n  Performing cleanup...")
    deleted = 0
    for etype, ename, eid, folder in targets:
        try:
            if etype == "DATASET":
                print(f"    Deleting dataset: {ename} (id={eid})")
                prov.delete_dataset(eid)
            elif etype == "DRD":
                print(f"    Deleting DRD: {ename} (id={eid})")
                prov.delete_drd(eid)
            elif etype == "SMODEL":
                print(f"    Deleting SM: {ename} (id={eid})")
                prov.delete_smodel(eid)
            elif etype == "FOLDER":
                ft_val = FolderType(folder)
                print(f"    Deleting folder: {ename} (id={eid}, type={ft_val.value})")
                prov.delete_folder(eid, ft_val)
            deleted += 1
        except Exception as e:
            print(f"    WARNING: Failed to delete {etype} '{ename}': {e}")

    print(f"\n  Deleted {deleted}/{len(targets)} entities.")

    # Write audit log
    log_path = _write_audit_log(targets, deleted, base_name, prefixes, dry_run=False)
    print(f"  Audit log written to: {log_path}")
    return deleted > 0


def cleanup_entities(
    *,
    env_file: str,
    base_name: str | None = None,
    dry_run: bool = True,
) -> int:
    """List and optionally delete old entities from Kyvos matching the base name.

    In dry-run mode, lists what would be deleted without actually deleting
    anything.  When base_name is not provided, derives it from the warehouse
    schema name in the config.

    Args:
        env_file: Path to .env file with Kyvos connection config.
        base_name: Base name to derive cleanup prefixes from.  If None,
                   uses the warehouse database name from config.
        dry_run: If True, only list entities; if False, delete them.

    Returns:
        0 on success, 1 on failure.
    """
    from kyvos_sdk.client import KyvosService
    from kyvos_sdk.config import KyvosConfig
    from kyvos_sdk.inspection import InspectionClient
    from kyvos_sdk.provisioning import ProvisioningClient

    config = KyvosConfig.from_env_file(env_file)
    if config.payload_format.lower() == "json":
        os.environ["KYVOS_DISABLE_JSON_FALLBACK"] = "1"

    if not base_name:
        base_name = config.warehouse_database.replace("_", " ").title()

    svc = KyvosService(config=config)
    svc.initialize()
    prov = ProvisioningClient(svc)
    insp = InspectionClient(svc)

    prefixes = _derive_cleanup_prefixes(base_name)

    print(f"\n{'═' * 70}")
    print(f"  Entity Cleanup {'(DRY RUN)' if dry_run else '(LIVE)'}")
    print(f"{'═' * 70}")
    print(f"  Base name: {base_name}")
    print(f"  Matching prefixes: {list(prefixes)} (case-insensitive)")
    print()

    _collect_and_cleanup_entities(
        insp=insp,
        prov=prov,
        base_name=base_name,
        dry_run=dry_run,
    )

    if not dry_run:
        print("  Waiting 10s for server to process deletions...")
        time.sleep(10)

    return 0


def _extract_kyvos_connection_db_type(conn_details: dict[str, Any]) -> str:
    """Extract the provider/db type from a Kyvos connection details response."""
    try:
        for conn in conn_details.get("RESPONSE", {}).get("CONNECTION", []):
            for prop in conn.get("configuration", {}).get("property", []):
                if prop.get("name") == "kyvos.connection.provider":
                    return prop.get("value") or "POSTGRES"
    except Exception:
        pass
    return "POSTGRES"


def _dump_payload(
    dump_dir: str,
    entity_name: str,
    entity_type: str,
    payload: str,
    fmt: str,
) -> None:
    """Write a compiled artifact payload to disk for debugging.

    Payloads are saved before they are sent to Kyvos so server-side errors
    (e.g. 'An error occurred while processing JSON data') can be inspected.
    """
    try:
        out_dir = Path(dump_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-]+", "_", entity_name).strip("_") or "unnamed"
        ext = "json" if fmt.lower() == "json" else "xml"
        out_path = out_dir / f"{entity_type}_{safe_name}.{ext}"
        counter = 0
        while out_path.exists():
            counter += 1
            out_path = out_dir / f"{entity_type}_{safe_name}_{counter}.{ext}"
        out_path.write_text(payload, encoding="utf-8")
        print(f"  Dumped {entity_type} payload to {out_path}")
    except OSError:
        # Dump is best-effort; don't fail deployment if writing fails.
        pass


def _deploy_spec(
    *,
    tables: list[Any],
    semantic_model: Any,
    metadata: dict[str, Any],
    base_name: str,
    config: Any,
    skip_hidden_tables: bool = False,
    cleanup_dry_run: bool = False,
    perform_cleanup: bool = True,
    auto_approve: bool = False,
    sm_folder_suffix: str = "",
    kyvos_connection_name: str | None = None,
    payload_dump_dir: str | None = None,
) -> dict[str, Any]:
    """Shared deployment pipeline — steps 3-10 of the XMLA skill flow.

    Args:
        tables: List of TableSpec-like objects (from XMLA parser or spec_builder).
        semantic_model: SemanticModelSpec-like object with relationships, measures, hierarchies.
        metadata: Extra metadata dict (e.g. from XMLA parser or discover flow).
        base_name: Base name for entity naming (e.g. "Adventure Works").
        config: KyvosConfig instance with Kyvos server + warehouse connection params.
        skip_hidden_tables: If True, skip tables with is_hidden=True.

    Returns:
        Dict with deployment results (success, entity IDs, names, etc.).

    Raises:
        RuntimeError: If any deployment step fails.
    """
    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Initialize Kyvos client
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 3: Initialize Kyvos client")
    print(f"{'─' * 70}")

    from kyvos_sdk.client import KyvosService
    from kyvos_sdk.contracts.identity import FolderType
    from kyvos_sdk.inspection import InspectionClient
    from kyvos_sdk.provisioning import ProvisioningClient

    # Prevent XML fallback when JSON is configured — surface errors as-is
    if config.payload_format.lower() == "json":
        os.environ["KYVOS_DISABLE_JSON_FALLBACK"] = "1"

    from kyvos_sm_skills.pipeline_tracer import get_tracer
    tracer = get_tracer()

    svc = KyvosService(config=config)
    if tracer:
        svc.api_trace_hook = tracer.api_call
        tracer.step("Deployment", f"Initializing Kyvos client for base_name={base_name}")
    svc.initialize()
    prov = ProvisioningClient(svc)
    insp = InspectionClient(svc)
    print("  Kyvos client initialized")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Create or reuse folders + clean up existing entities
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 4: Create or reuse folders")
    print(f"{'─' * 70}")

    _ts = datetime.now().strftime("%m%d%y_%H%M")

    # Sanitize SM name for Kyvos (only A-Za-z, 0-9, ~@#^_- allowed)
    _safe_sm_name = re.sub(r"[^A-Za-z0-9~@#^_-]", "", semantic_model.name.replace(" ", "_"))

    smodel_name      = f"{_safe_sm_name}_{_ts}"
    drd_name         = f"{smodel_name} DRD"
    drd_id           = f"drd_{smodel_name}"

    # Use stable folder names (no timestamp) so they can be reused across runs
    # When sm_folder_suffix is provided, ALL folders get the suffix for complete isolation
    _folder_suffix = f"_{sm_folder_suffix}" if sm_folder_suffix else ""
    dataset_folder_label = f"{base_name}{_folder_suffix}"
    drd_folder_label     = f"{base_name}_DRD{_folder_suffix}"
    smodel_folder_label  = f"{base_name}_SModel{_folder_suffix}"
    space_folder_label   = f"{base_name}_Space{_folder_suffix}"

    # --- Helper: find existing folder by name ---
    def _find_existing_folder(folder_type, folder_name):
        """Return folder ID if a folder with the given name exists, else None."""
        result = insp.list_folders(folder_type)
        if result.succeeded and result.entity_refs:
            for ref in result.entity_refs:
                if ref.name == folder_name:
                    return ref.id
        return None

    # --- Helper: clean up entities in a folder ---
    def _cleanup_folder_entities(folder_type, folder_name) -> int:
        """Delete all entities in a folder before reusing it."""
        deleted = 0
        if folder_type == FolderType.RDATASET:
            list_result = insp.list_datasets_in_folder(folder_name)
            if list_result.succeeded and list_result.entity_refs:
                for ref in list_result.entity_refs:
                    print(f"    Deleting existing dataset: {ref.name} (id={ref.id})")
                    prov.delete_dataset(ref.id)
                    deleted += 1
        elif folder_type == FolderType.DATASET_RELATIONSHIP:
            list_result = insp.list_drds_in_folder(folder_name)
            if list_result.succeeded and list_result.entity_refs:
                for ref in list_result.entity_refs:
                    print(f"    Deleting existing DRD: {ref.name} (id={ref.id})")
                    prov.delete_drd(ref.id)
                    deleted += 1
        elif folder_type == FolderType.SMODEL:
            list_result = insp.list_smodels_in_folder(folder_name)
            if list_result.succeeded and list_result.entity_refs:
                for ref in list_result.entity_refs:
                    print(f"    Deleting existing SM: {ref.name} (id={ref.id})")
                    prov.delete_smodel(ref.id)
                    deleted += 1
        return deleted

    def _wait_for_folder_empty(folder_type, folder_name, timeout_seconds: float = 10.0) -> bool:
        """Poll until a reused folder is empty, instead of sleeping fixed time."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            if folder_type == FolderType.RDATASET:
                list_result = insp.list_datasets_in_folder(folder_name)
            elif folder_type == FolderType.DATASET_RELATIONSHIP:
                list_result = insp.list_drds_in_folder(folder_name)
            else:
                list_result = insp.list_smodels_in_folder(folder_name)
            if list_result.succeeded and not list_result.entity_refs:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    # --- Clean up old entities from previous runs ---
    # Uses the shared helper with prefixes derived from base_name AND the SM name.
    # This catches entities from previous runs that may have used different
    # naming conventions (e.g., "AdventureWorks" prefix from older runs).
    # By default (perform_cleanup=True), old entities are deleted to avoid
    # global measure name conflicts on the Kyvos server.
    _stable_folder_names = {dataset_folder_label, drd_folder_label, smodel_folder_label, space_folder_label}
    if sm_folder_suffix:
        # Protect the base (non-suffixed) folders so cleanup never touches other flows' entities
        _stable_folder_names.add(f"{base_name}")
        _stable_folder_names.add(f"{base_name}_DRD")
        _stable_folder_names.add(f"{base_name}_SModel")
        _stable_folder_names.add(f"{base_name}_Space")
    _sm_prefixes = _derive_cleanup_prefixes(semantic_model.name.replace("_", " ").title())
    _did_cleanup = _collect_and_cleanup_entities(
        insp=insp,
        prov=prov,
        base_name=base_name,
        dry_run=cleanup_dry_run or not perform_cleanup,
        skip_folders=_stable_folder_names,
        extra_prefixes=_sm_prefixes,
        auto_approve=auto_approve,
        restrict_smodel_folder=smodel_folder_label,
        folder_suffix=sm_folder_suffix,
    )
    if _did_cleanup:
        print("  Deletions submitted; reused folders are polled until empty.")

    # --- Dataset folder: find or create ---
    existing_ds_folder_id = _find_existing_folder(FolderType.RDATASET, dataset_folder_label)
    if existing_ds_folder_id:
        folder_id = existing_ds_folder_id
        print(f"Dataset folder: {dataset_folder_label} (id={folder_id}) — reusing existing")
        print("  Cleaning up existing datasets...")
        if _cleanup_folder_entities(FolderType.RDATASET, dataset_folder_label):
            if not _wait_for_folder_empty(FolderType.RDATASET, dataset_folder_label):
                print(f"  WARNING: dataset folder '{dataset_folder_label}' still contains entities")
    else:
        dataset_folder_result = prov.create_folder(dataset_folder_label, FolderType.RDATASET)
        if not dataset_folder_result.succeeded:
            raise RuntimeError(
                f"Dataset folder creation failed: {[d.message for d in dataset_folder_result.diagnostics]}"
            )
        folder_id = dataset_folder_result.primary_entity_id
        print(f"Dataset folder: {dataset_folder_label} (id={folder_id}) — created")

    # --- DRD folder: find or create ---
    existing_drd_folder_id = _find_existing_folder(FolderType.DATASET_RELATIONSHIP, drd_folder_label)
    if existing_drd_folder_id:
        drd_folder_id = existing_drd_folder_id
        print(f"DRD folder: {drd_folder_label} (id={drd_folder_id}) — reusing existing")
        print("  Cleaning up existing DRDs...")
        if _cleanup_folder_entities(FolderType.DATASET_RELATIONSHIP, drd_folder_label):
            if not _wait_for_folder_empty(FolderType.DATASET_RELATIONSHIP, drd_folder_label):
                print(f"  WARNING: DRD folder '{drd_folder_label}' still contains entities")
    else:
        drd_folder_result = prov.create_folder(drd_folder_label, FolderType.DATASET_RELATIONSHIP)
        if not drd_folder_result.succeeded:
            raise RuntimeError(
                f"DRD folder creation failed: {[d.message for d in drd_folder_result.diagnostics]}"
            )
        drd_folder_id = drd_folder_result.primary_entity_id
        print(f"DRD folder: {drd_folder_label} (id={drd_folder_id}) — created")

    # --- SM folder: find or create ---
    existing_sm_folder_id = _find_existing_folder(FolderType.SMODEL, smodel_folder_label)
    if existing_sm_folder_id:
        smodel_folder_id = existing_sm_folder_id
        print(f"Semantic model folder: {smodel_folder_label} (id={smodel_folder_id}) — reusing existing")
        print("  Cleaning up existing semantic models...")
        if _cleanup_folder_entities(FolderType.SMODEL, smodel_folder_label):
            if not _wait_for_folder_empty(FolderType.SMODEL, smodel_folder_label):
                print(f"  WARNING: semantic model folder '{smodel_folder_label}' still contains entities")
    else:
        smodel_folder_result = prov.create_folder(smodel_folder_label, FolderType.SMODEL)
        if not smodel_folder_result.succeeded:
            raise RuntimeError(
                f"Semantic model folder creation failed: {[d.message for d in smodel_folder_result.diagnostics]}"
            )
        smodel_folder_id = smodel_folder_result.primary_entity_id
        print(f"Semantic model folder: {smodel_folder_label} (id={smodel_folder_id}) — created")

    # Reused folders were polled until their entity listings were empty; no
    # unconditional cleanup delay is needed here.
    # ═══════════════════════════════════════════════════════════════════════
    # Step 5: Create connection
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 5: Create connection")
    print(f"{'─' * 70}")
    if tracer:
        tracer.step("Create Connection", f"kyvos_connection_name={kyvos_connection_name or 'new warehouse connection'}")

    if kyvos_connection_name:
        # Use an existing Kyvos connection (e.g. PSdatabricks) for metadata discovery flow.
        # Skip creating a new warehouse connection.
        connection_name = kyvos_connection_name
        connection_id = None
        # Verify it exists in Kyvos so we fail fast with a clear message.
        raw_conn = svc.get_connection(connection_name)
        if raw_conn is None:
            raise RuntimeError(
                f"Kyvos connection '{connection_name}' was not found. "
                f"Please create it in the Kyvos portal first."
            )
        kyvos_db_type = _extract_kyvos_connection_db_type(raw_conn)
        print(f"Using existing Kyvos connection: {connection_name} (type={kyvos_db_type})")
    else:
        from kyvos_sdk.warehouse_registry import build_jdbc_url, get_warehouse_profile

        jdbc_url = config.warehouse_jdbc_url or build_jdbc_url(
            config.warehouse_type,
            config.warehouse_host,
            config.warehouse_port,
            config.warehouse_database,
            **config.warehouse_extra_params,
        )
        driver = config.warehouse_driver or get_warehouse_profile(config.warehouse_type).driver_class
        db_version = config.warehouse_db_version or get_warehouse_profile(config.warehouse_type).db_version_default

        conn_result = prov.create_connection(
            name=config.warehouse_connection_name,
            host=config.warehouse_host,
            port=config.warehouse_port,
            database=config.warehouse_database,
            username=config.warehouse_username,
            password=config.warehouse_password,
            db_type=config.warehouse_type,
            db_version=db_version,
            use_json=(config.payload_format == "json"),
            use_existing_if_found=True,
            jdbc_url_override=jdbc_url,
            driver_override=driver,
        )
        if not conn_result.succeeded:
            raise RuntimeError(
                f"Connection creation failed: {[d.message for d in conn_result.diagnostics]}"
            )
        connection_id = conn_result.primary_entity_id
        connection_name = config.warehouse_connection_name
        print(f"Connection: {connection_name} (id={connection_id})")
        kyvos_db_type = config.warehouse_type

    # ═══════════════════════════════════════════════════════════════════════
    # Step 6: Create datasets
    # ═══════════════════════════════════════════════════════════════════════
    if tracer:
        tracer.step("Create Datasets", f"{len(tables)} tables")
    print(f"\n{'─' * 70}")
    print("  Step 6: Create datasets")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.contract_adapter import compile_dataset_artifact
    from kyvos_sm_skills.role_playing import split_role_playing_dimensions

    # Role-playing (custom rollup) dimensions: each non-base role gets its
    # own dataset over the same SQL (e.g. Dimdate_Ship / Dimdate_Due from
    # SELECT * FROM <schema>.dimdate) instead of DRD alias nodes sharing
    # one dataset.
    role_split = split_role_playing_dimensions(
        tables=list(tables),
        relationships=semantic_model.relationships,
        hierarchies=semantic_model.hierarchies,
        datasets=semantic_model.datasets,
    )
    if role_split.changed:
        tables = role_split.tables
        semantic_model.relationships = role_split.relationships
        semantic_model.hierarchies = role_split.hierarchies
        semantic_model.datasets = role_split.datasets
        _rp_lines = [
            f"{role_split.dataset_name[n]} (role {role_split.role[n]} of {role_split.source_table[n]})"
            for n in role_split.source_table
        ]
        print("  Role-playing datasets: " + ", ".join(_rp_lines))
        if tracer:
            tracer.note("Role-playing datasets", "\n".join(_rp_lines))

    dataset_name_to_id = {}
    dataset_aliases = {}
    created_entities = []
    deployment_workers = _env_int("KYVOS_DEPLOYMENT_WORKERS", 8, maximum=8)

    # Compile locally first; only the remote Kyvos calls are parallelised.
    dataset_items = []
    for table in tables:
        if skip_hidden_tables and table.is_hidden:
            continue

        _source_table = role_split.source_table.get(table.name)
        ds_artifact = compile_dataset_artifact(
            table.model_copy(update={"name": _source_table}) if _source_table else table,
            connection_name=connection_name,
            folder_id=folder_id,
            folder_name=dataset_folder_label,
            fmt=config.payload_format,
            db_type=kyvos_db_type,
            dataset_name=role_split.dataset_name.get(table.name),
        )
        if payload_dump_dir:
            _dump_payload(payload_dump_dir, table.name, "dataset", ds_artifact.payload, config.payload_format)
        dataset_items.append((table, ds_artifact))

    def _parallel(items, operation):
        if deployment_workers <= 1 or len(items) <= 1:
            return [operation(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(deployment_workers, len(items))) as executor:
            return list(executor.map(operation, items))

    def _create_dataset(item):
        table, artifact = item
        try:
            return table, prov.apply_artifact(artifact), None
        except Exception as exc:
            return table, None, exc

    create_errors = []
    for table, ds_result, exc in _parallel(dataset_items, _create_dataset):
        if exc is not None:
            create_errors.append(f"{table.name}: {exc}")
            continue
        if not ds_result.succeeded:
            create_errors.append(
                f"{table.name}: {[d.message for d in ds_result.diagnostics]}"
            )
            continue

        server_name = ds_result.primary_entity_name
        ds_id = ds_result.primary_entity_id

        dataset_name_to_id[server_name] = ds_id
        if table.name != server_name:
            dataset_aliases[table.name] = server_name

        created_entities.append({
            "entity_type": "DATASET",
            "id": ds_id,
            "name": server_name,
        })
        print(f"  Dataset: {server_name} (id={ds_id})")

    if create_errors:
        raise RuntimeError(
            "Dataset creation failed — pipeline halted:\n"
            + "\n".join(create_errors)
            + f"\nCreated so far: {dataset_name_to_id}"
        )

    # Fallback: if any datasets were created with empty IDs (Kyvos API sometimes
    # returns empty entityId on creation), fetch actual IDs by listing the folder.
    empty_id_datasets = [
        ds for ds in created_entities
        if ds["entity_type"] == "DATASET" and not ds["id"]
    ]
    if empty_id_datasets:
        print(f"  {len(empty_id_datasets)} dataset(s) created with empty ID — fetching from folder...")
        list_result = insp.list_datasets_in_folder(dataset_folder_label)
        if list_result.succeeded and list_result.entity_refs:
            folder_ds_map = {
                ref.name.lower(): ref.id for ref in list_result.entity_refs
            }
            for ds_info in empty_id_datasets:
                actual_id = folder_ds_map.get(ds_info["name"].lower(), "")
                if actual_id:
                    ds_info["id"] = actual_id
                    dataset_name_to_id[ds_info["name"]] = actual_id
                    print(f"  Resolved dataset ID: {ds_info['name']} (id={actual_id})")
                else:
                    print(f"  WARNING: Could not resolve dataset ID for '{ds_info['name']}' from folder listing")

    # Refresh columns and validate datasets in parallel. This used to run two
    # sequential passes (create+refresh, then refresh+validate); one refresh is
    # sufficient before validation and fetching column details.
    dataset_entities = [
        ds for ds in created_entities if ds["entity_type"] == "DATASET"
    ]

    def _refresh_and_validate(ds_info):
        try:
            refresh_result = prov.refresh_dataset_columns(ds_info["id"])
        except Exception as exc:
            refresh_result = exc
        try:
            validation_result = prov.validate_dataset(
                ds_info["id"], ds_info["name"], dataset_folder_label
            )
        except Exception as exc:
            validation_result = exc
        return ds_info, refresh_result, validation_result

    validation_errors = []
    for ds_info, refresh_result, val_result in _parallel(dataset_entities, _refresh_and_validate):
        if isinstance(refresh_result, Exception):
            print(
                f"  WARNING: Column refresh failed for {ds_info['name']}: "
                f"{refresh_result}"
            )
        elif refresh_result.status == OperationStatus.TIMED_OUT:
            print(
                f"  WARNING: Column refresh timed out for {ds_info['name']} "
                f"(id={ds_info['id']}) — continuing with spec columns."
            )
        elif not refresh_result.succeeded:
            errs = [d.message for d in refresh_result.diagnostics if d.severity == Severity.ERROR]
            print(f"  WARNING: Column refresh failed for {ds_info['name']}: {errs}")

        if isinstance(val_result, Exception):
            validation_errors.append(f"{ds_info['name']}: {val_result}")
        elif val_result.status == OperationStatus.TIMED_OUT:
            print(
                f"  WARNING: Dataset validation timed out for {ds_info['name']} "
                f"(id={ds_info['id']}) — continuing. Kyvos may still be processing the dataset."
            )
        elif not val_result.succeeded:
            errs = [d.message for d in val_result.diagnostics if d.severity == Severity.ERROR]
            validation_errors.append(f"{ds_info['name']}: {errs}")

    if validation_errors:
        raise RuntimeError(
            "Dataset validation failed — pipeline halted:\n" +
            "\n".join(validation_errors)
        )

    # Fetch column details for semantic model compilation
    server_to_spec_table = {}
    for table in tables:
        server_name = dataset_aliases.get(table.name, table.name)
        server_to_spec_table[server_name] = table
        server_to_spec_table[server_name.lower()] = table

    def _fetch_columns(ds_info):
        try:
            return ds_info, prov.get_dataset_column_details(
                dataset_folder_label, ds_info["name"]
            ), None
        except Exception as exc:
            return ds_info, [], exc

    dataset_cols = {}
    for ds_info, cols, exc in _parallel(dataset_entities, _fetch_columns):
        if cols:
            dataset_cols[ds_info["name"]] = cols
            continue
        tbl = server_to_spec_table.get(ds_info["name"]) or server_to_spec_table.get(ds_info["name"].lower())
        if tbl and tbl.columns:
            dataset_cols[ds_info["name"]] = [
                {
                    "name": c.name,
                    "datatype": c.data_type,
                    "original_name": c.name,
                    "isPrimaryKey": c.is_primary_key,
                    "isForeignKey": c.is_foreign_key,
                }
                for c in tbl.columns
            ]
            print(f"  Column details from spec fallback: {ds_info['name']} ({len(tbl.columns)} cols)")
        else:
            detail = f": {exc}" if exc else ""
            print(f"  WARNING: No column details for {ds_info['name']} — no spec fallback available{detail}")

    print(f"Datasets validated and column details fetched for {len(dataset_cols)} datasets")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 7: Build DRD graph + create DRD
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 7: Build DRD graph + create DRD")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.contract_adapter import compile_drd_artifact

    validated_rels = []
    failed_relationships = []

    for rel in semantic_model.relationships:
        if not rel.active:
            continue

        left_kyvos = dataset_aliases.get(rel.left_dataset, rel.left_dataset)
        right_kyvos = dataset_aliases.get(rel.right_dataset, rel.right_dataset)

        left_cols_raw = dataset_cols.get(left_kyvos)
        right_cols_raw = dataset_cols.get(right_kyvos)

        skip = False
        if left_cols_raw is not None and rel.left_column.lower() not in {c["name"].lower() for c in left_cols_raw}:
            failed_relationships.append(
                f"Column '{rel.left_column}' not found in dataset '{rel.left_dataset}' "
                f"(Kyvos: '{left_kyvos}')"
            )
            skip = True

        if not skip and right_cols_raw is not None and rel.right_column.lower() not in {
            c["name"].lower() for c in right_cols_raw
        }:
            failed_relationships.append(
                f"Column '{rel.right_column}' not found in dataset '{rel.right_dataset}' "
                f"(Kyvos: '{right_kyvos}')"
            )
            skip = True

        if not skip:
            validated_rels.append(rel)

    if not validated_rels:
        raise RuntimeError(
            f"No valid relationships remain after column validation — pipeline halted. "
            f"Failed relationships ({len(failed_relationships)}):\n"
            + "\n".join(f"  - {r}" for r in failed_relationships[:10])
        )

    if failed_relationships:
        print(f"  WARNING: {len(failed_relationships)} relationship(s) skipped due to missing columns")
        for r in failed_relationships[:5]:
            print(f"    - {r}")

    print(f"  Valid relationships: {len(validated_rels)} / {len(semantic_model.relationships)}")
    if tracer:
        tracer.step("Build DRD + Semantic Model", f"Validated {len(validated_rels)} relationships")

    fact_dataset_names = set()
    bridge_dataset_names = set()
    for table in tables:
        server_name = dataset_aliases.get(table.name, table.name)
        if table.table_type == "fact":
            fact_dataset_names.add(server_name)
        elif table.table_type == "bridge":
            bridge_dataset_names.add(server_name)

    # Dump a snapshot for offline analysis (bridge_detector + analyzer script)
    try:
        import os as _os
        _snapshot_dir = _os.environ.get("KYVOS_SNAPSHOT_DIR", "samples/output")
        _snapshot_path = _os.path.join(_snapshot_dir, "bridge_snapshot.json")
        _os.makedirs(_snapshot_dir, exist_ok=True)
        with open(_snapshot_path, "w") as _f:
            json.dump({
                "tables": [t.model_dump() for t in tables],
                "relationships": [r.model_dump() for r in validated_rels],
                "measures": [m.model_dump() for m in semantic_model.measures],
                "dataset_aliases": dataset_aliases,
            }, _f, indent=2, default=str)
        print(f"  Bridge snapshot saved: {_snapshot_path}")
    except Exception:
        pass

    # Run bridge detection via the pure function
    from kyvos_sm_skills.bridge_detector import detect_bridges
    _bridge_result = detect_bridges(
        tables=tables,
        relationships=validated_rels,
        measures=semantic_model.measures,
        dataset_aliases=dataset_aliases,
    )

    # Print decisions for traceability
    for _d in _bridge_result.decisions:
        if _d.is_bridge:
            print(f"  Bridge detected: '{_d.table_name}' ({_d.reason})")
    for _name, _reason in sorted(_bridge_result.reclassified.items()):
        print(f"  Reclassified '{_name}' from bridge → dimension ({_reason})")
    if _bridge_result.reclassified:
        print(f"  Reclassified {len(_bridge_result.reclassified)} misclassified bridge table(s) → dimension")
    if _bridge_result.bridge_names:
        print(f"  Bridge datasets: {_bridge_result.bridge_names}")

    fact_dataset_names = _bridge_result.fact_names
    bridge_dataset_names = _bridge_result.bridge_names

    drd_artifact = compile_drd_artifact(
        drd_name=drd_name,
        drd_id=drd_id,
        folder_id=drd_folder_id,
        folder_name=drd_folder_label,
        dataset_name_to_id=dataset_name_to_id,
        relationships=validated_rels,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
        bridge_dataset_names=bridge_dataset_names,
        fmt=config.payload_format,
    )
    if payload_dump_dir:
        _dump_payload(payload_dump_dir, drd_name, "drd", drd_artifact.payload, config.payload_format)

    drd_result = prov.apply_artifact(drd_artifact)
    if not drd_result.succeeded:
        raise RuntimeError(
            f"DRD creation failed: {[d.message for d in drd_result.diagnostics]}"
        )

    server_drd_id = drd_result.primary_entity_id

    # Guard: Kyvos DRD JSON API sometimes returns the entity ID in a non-standard
    # field (e.g. "drdId", nested "data.id", etc.) that the SDK parser misses.
    # If the response parse yielded nothing, look up the freshly-created DRD by
    # name from the folder — it will be there because apply_artifact succeeded.
    if not server_drd_id:
        print(
            f"  WARNING: DRD creation response did not include entity ID — "
            f"resolving '{drd_name}' via folder lookup..."
        )
        _drd_list = insp.list_drds_in_folder(drd_folder_label)
        if _drd_list.succeeded and _drd_list.entity_refs:
            for _ref in _drd_list.entity_refs:
                if _ref.name == drd_name:
                    server_drd_id = _ref.id
                    print(f"  DRD ID resolved via folder lookup: {server_drd_id}")
                    break
        if not server_drd_id:
            _available = [r.name for r in (_drd_list.entity_refs or [])]
            raise RuntimeError(
                f"DRD '{drd_name}' was created but its server ID could not be resolved. "
                f"DRDs visible in folder '{drd_folder_label}': {_available}"
            )

    created_entities.append({
        "entity_type": "DRD",
        "id": server_drd_id,
        "name": drd_name,
    })

    # Validate DRD — retry up to 3 times with 5s delay
    _max_validation_retries = 3
    _validation_delay = 5
    for _attempt in range(1, _max_validation_retries + 1):
        drd_val_result = prov.validate_drd(server_drd_id, drd_name, drd_folder_label)
        if drd_val_result.succeeded:
            break
        if _attempt < _max_validation_retries:
            print(
                f"  DRD validation pending (attempt {_attempt}/{_max_validation_retries}), "
                f"retrying in {_validation_delay}s..."
            )
            time.sleep(_validation_delay)
        else:
            errs = [d.message for d in drd_val_result.diagnostics if d.severity in (Severity.ERROR, Severity.WARNING)]
            if not errs:
                errs = [d.message for d in drd_val_result.diagnostics]
            raise RuntimeError(f"DRD validation failed — pipeline halted: {errs}")

    print(f"DRD: {drd_name} (id={server_drd_id}) — validated")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 8: Compile + create semantic model
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 8: Compile + create semantic model")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.contract_adapter import compile_smodel_artifact

    semantic_model.name = smodel_name

    # Debug: verify measure names are unique
    _measure_names = [m.name for m in semantic_model.measures]
    _dupes = [n for n in _measure_names if _measure_names.count(n) > 1]
    if _dupes:
        print(f"  WARNING: Duplicate measure names detected in spec: {sorted(set(_dupes))}")
    else:
        print(f"  Measure names verified unique ({len(_measure_names)} measures)")

    sm_artifact = compile_smodel_artifact(
        semantic_model,
        drd_name=drd_name,
        drd_id=server_drd_id,
        folder_id=smodel_folder_id,
        folder_name=smodel_folder_label,
        connection_name=connection_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=validated_rels,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
        bridge_dataset_names=bridge_dataset_names,
        dataset_columns=dataset_cols,
        fmt=config.payload_format,
    )

    # Dump compiled payloads before sending to Kyvos — critical for diagnosing
    # server-side 400/JSON processing errors.
    if payload_dump_dir:
        _dump_payload(payload_dump_dir, smodel_name, "smodel", sm_artifact.payload, config.payload_format)

    no_measures_diag = [d for d in sm_artifact.diagnostics if d.code == "NO_MEASURES_PLACED"]
    if no_measures_diag:
        raise RuntimeError(
            f"Semantic model compilation produced zero measures — pipeline halted. "
            f"Diagnostic: {no_measures_diag[0].message}. "
            f"Check that measure source_dataset names match dataset names after alias remapping. "
            f"dataset_aliases={dataset_aliases}, "
            f"measure_source_datasets={[m.source_dataset for m in semantic_model.measures]}"
        )

    sm_result = prov.apply_artifact(sm_artifact)
    if not sm_result.succeeded:
        raise RuntimeError(
            f"Semantic model creation failed: {[d.message for d in sm_result.diagnostics]}"
        )

    smodel_id = sm_result.primary_entity_id
    created_entities.append({
        "entity_type": "SEMANTIC_MODEL",
        "id": smodel_id,
        "name": smodel_name,
    })

    # ═══════════════════════════════════════════════════════════════════════
    # Step 9: Create AI space (before validation so it exists even if validation fails)
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 9: Create AI space")
    print(f"{'─' * 70}")
    if tracer:
        tracer.step("Create AI Space", f"space_name={smodel_name}_Space")

    # --- AI space folder: find or create ---
    # Done here (not in the upfront folder block) so a failing SM never
    # leaves an orphan SPACE folder. No entity cleanup — the Kyvos API does
    # not support deleting AI spaces.
    existing_space_folder_id = _find_existing_folder(FolderType.SPACE, space_folder_label)
    if existing_space_folder_id:
        space_folder_id = existing_space_folder_id
        print(f"AI space folder: {space_folder_label} (id={space_folder_id}) — reusing existing")
    else:
        space_folder_result = prov.create_folder(space_folder_label, FolderType.SPACE)
        if not space_folder_result.succeeded:
            raise RuntimeError(
                f"AI space folder creation failed: {[d.message for d in space_folder_result.diagnostics]}"
            )
        space_folder_id = space_folder_result.primary_entity_id
        print(f"AI space folder: {space_folder_label} (id={space_folder_id}) — created")

    ai_space_name = f"{smodel_name}_Space"
    space_result = prov.create_ai_space(
        space_name=ai_space_name,
        folder_id=space_folder_id,
        folder_name=space_folder_label,
        semantic_models=[{
            "id": smodel_id,
            "name": smodel_name,
            "folder_id": smodel_folder_id,
            "folder_name": smodel_folder_label,
        }],
        description=f"AI Space for semantic model {smodel_name}",
    )
    if not space_result.succeeded:
        raise RuntimeError(
            f"AI space creation failed: {[d.message for d in space_result.diagnostics]}"
        )
    ai_space_id = space_result.primary_entity_id or ""
    for _space_diag in space_result.diagnostics:
        if _space_diag.severity == Severity.WARNING:
            print(f"  WARNING: {_space_diag.message}")
    print(f"AI Space: {ai_space_name} (id={ai_space_id}) — created")

    created_entities.append({
        "entity_type": "AI_SPACE",
        "id": ai_space_id,
        "name": ai_space_name,
    })

    # Validate semantic model — retry on transient capacity failures, but keep
    # the delay bounded so slow validation cannot dominate the whole flow.
    _sm_max_retries = _env_int("KYVOS_SM_VALIDATION_RETRIES", 8, maximum=20)
    _sm_retry_delay = _env_int("KYVOS_SM_VALIDATION_RETRY_DELAY", 5, minimum=0, maximum=30)
    _sm_validated = False
    _sm_val_errs: list[str] = []
    _sm_val_kind = ""
    for _attempt in range(1, _sm_max_retries + 1):
        sm_val_result = prov.validate_semantic_model(smodel_id, smodel_name, smodel_folder_label)
        if sm_val_result.succeeded:
            _sm_validated = True
            break
        # Check if this is a transient server capacity error (500) — keep retrying
        _is_capacity_error = any("capacity" in d.message.lower() for d in sm_val_result.diagnostics)
        if _is_capacity_error and _attempt < _sm_max_retries:
            print(f"  SM validation pending (attempt {_attempt}/{_sm_max_retries}), retrying in {_sm_retry_delay}s...")
            time.sleep(_sm_retry_delay)
            continue

        _sm_val_errs = [
            d.message
            for d in sm_val_result.diagnostics
            if d.severity in (Severity.ERROR, Severity.WARNING)
        ]
        if not _sm_val_errs:
            _sm_val_errs = [d.message for d in sm_val_result.diagnostics]

        # Kyvos 2026.5+ validates semantic models via an AI/LLM service. If
        # the server's AI connections (Azure OpenAI / AWS Bedrock) are
        # misconfigured or deprecated, validation fails with AI_SETTINGS errors.
        # When ALL returned errors are AI-related, the SM itself is structurally
        # valid and was created successfully; warn and continue.
        _ai_error_patterns = [
            "ai settings", "azureopenai", "aws-bedrock", "bedrock",
            "reasoning.effort", "model version has reached the end of its life",
            "llm", "analytical server could not perform semantic model validations",
            "too many aggregates", "aggregation strategy",
        ]
        _all_ai_errors = all(
            any(p in e.lower() for p in _ai_error_patterns)
            for e in _sm_val_errs
        )
        if _all_ai_errors:
            _sm_val_kind = "ai_config"
            print("  WARNING: Kyvos could not validate the semantic model "
                  "(server-side AI/LLM configuration issue). Model, AI space and "
                  "all artifacts were created successfully.")
        else:
            _sm_val_kind = "model"
            print(f"  WARNING: Semantic model validation reported {len(_sm_val_errs)} error(s). "
                  f"The model, AI space and all artifacts were created on the server; "
                  f"review the validation messages in the Kyvos UI.")
        for e in _sm_val_errs[:10]:
            print(f"    - {e}")
        if len(_sm_val_errs) > 10:
            print(f"    ... and {len(_sm_val_errs) - 10} more")
        break
    else:
        _sm_val_kind = "capacity_timeout"
        _sm_val_errs = [d.message for d in sm_val_result.diagnostics]
        print("  WARNING: SM validation could not complete due to server capacity limits.")
        print(f"  SM was created successfully (id={smodel_id}) but validation timed out.")
        print("  The model can be validated manually from the Kyvos UI.")

    if _sm_validated:
        print(f"Semantic Model: {smodel_name} (id={smodel_id}) — validated")
    else:
        print(f"Semantic Model: {smodel_name} (id={smodel_id}) — created, not validated")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 10: Report results
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 10: Report results")
    print(f"{'─' * 70}")

    _val_warnings = [f"Semantic model validation: {e}" for e in _sm_val_errs]
    if not _sm_validated and not _val_warnings:
        _val_warnings = [
            "Semantic model validation: could not complete — "
            "the model was created but not validated."
        ]

    result: dict[str, Any] = {
        "success": True,
        "spec_summary": {
            "tables": len(tables),
            "relationships": len(semantic_model.relationships),
            "measures": len(semantic_model.measures),
            "hierarchies": len(semantic_model.hierarchies),
        },
        "connection_name": config.warehouse_connection_name,
        "dataset_name_to_id": dataset_name_to_id,
        "drd_name": drd_name,
        "drd_id": server_drd_id,
        "smodel_name": smodel_name,
        "smodel_id": smodel_id,
        "smodel_validation_skipped": not _sm_validated,
        "smodel_validated": _sm_validated,
        "smodel_validation_kind": _sm_val_kind or None,
        "smodel_validation_errors": _sm_val_errs,
        "ai_space_name": ai_space_name,
        "ai_space_id": ai_space_id,
        "created_entities": created_entities + [
            {"entity_type": "FOLDER", "id": folder_id,        "name": dataset_folder_label},
            {"entity_type": "FOLDER", "id": drd_folder_id,    "name": drd_folder_label},
            {"entity_type": "FOLDER", "id": smodel_folder_id, "name": smodel_folder_label},
            {"entity_type": "FOLDER", "id": space_folder_id,  "name": space_folder_label},
            {"entity_type": "CONNECTION", "id": connection_id, "name": config.warehouse_connection_name},
        ],
        "errors": [],
        "warnings": _val_warnings if not _sm_validated else [],
    }
    if not _sm_validated:
        print("\n⚠️ Deployment completed with warnings")
        print("   Semantic model was created but could not be validated.")
        print("   Validation errors:")
        for e in _sm_val_errs[:5]:
            print(f"     - {e}")
    else:
        print("\n✅ Deployment Successful")
    print(f"   Timestamp     : {_ts}")
    print(f"   Tables        : {len(tables)}")
    print(f"   Datasets      : {len(dataset_name_to_id)}")
    print(f"   Relationships : {len(semantic_model.relationships)}")
    print(f"   Measures      : {len(semantic_model.measures)}")
    print(f"   Connection    : {config.warehouse_connection_name}")
    print(f"   DRD           : {drd_name} (id={server_drd_id})")
    print(f"   Semantic Model: {smodel_name}")
    print(f"   AI Space      : {ai_space_name}")

    if tracer:
        tracer.json_dump("Deployment result", result)

    return result


def run_deploy_from_xmla(
    *,
    xmla_file_path: str,
    env_file: str,
    payload_format: str | None = None,
    dry_run: bool = False,
    live: bool = True,
    cleanup_dry_run: bool = False,
    auto_approve: bool = False,
    sm_folder_suffix: str = "",
) -> int:
    """Run the deploy-from-xmla skill flow.

    Args:
        xmla_file_path: Path to the .xmla file.
        env_file: Path to the .env config file.
        payload_format: Override payload format ("json" or "xml").
        dry_run: If True, parse + compile only, no API calls.
        live: If True, use real KyvosService (always True for deploy).

    Returns:
        0 on success, 1 on failure.
    """
    # ═══════════════════════════════════════════════════════════════════════
    # Step 1: Load config
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 1: Load config")
    print(f"{'─' * 70}")

    from kyvos_sdk.config import KyvosConfig

    config = KyvosConfig.from_env_file(env_file)
    if payload_format:
        config.payload_format = payload_format
    print(f"  Config loaded from {env_file}")
    print(f"  Payload format: {config.payload_format}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 2: Parse XMLA + derive names
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 2: Parse XMLA + derive names")
    print(f"{'─' * 70}")

    from kyvos_xmla_parser.xmla_parser import parse_xmla

    with open(xmla_file_path) as f:
        spec = parse_xmla(f.read())

    print(f"Parsed: {len(spec.tables)} tables, "
          f"{len(spec.semantic_model.relationships)} relationships, "
          f"{len(spec.semantic_model.measures)} measures")

    _schema_name = spec.metadata.get("schema_name", "") if isinstance(spec.metadata, dict) else ""
    if _schema_name:
        base_name = _schema_name.replace("_", " ").title()
    else:
        base_name = spec.semantic_model.name

    _ts = datetime.now().strftime("%m%d%y_%H%M")

    smodel_name      = f"{spec.semantic_model.name}_{_ts}"
    drd_name         = f"{smodel_name} DRD"

    print(f"Base name     : {base_name}")
    print(f"Timestamp     : {_ts}")
    print(f"Semantic model: {smodel_name}")
    print(f"DRD name      : {drd_name}")

    if dry_run:
        print(f"\n✅ Dry run complete — parsed {len(spec.tables)} tables, "
              f"{len(spec.semantic_model.relationships)} relationships, "
              f"{len(spec.semantic_model.measures)} measures")
        return 0

    # Steps 3-9: Deploy via shared pipeline
    _deploy_spec(
        tables=spec.tables,
        semantic_model=spec.semantic_model,
        metadata=spec.metadata if isinstance(spec.metadata, dict) else {},
        base_name=base_name,
        config=config,
        skip_hidden_tables=config.skip_hidden_tables,
        cleanup_dry_run=cleanup_dry_run,
        perform_cleanup=not cleanup_dry_run,
        auto_approve=auto_approve,
        sm_folder_suffix=sm_folder_suffix,
    )
    print(f"\n   XMLA model    : {spec.metadata.get('xmla_db_name', base_name)}")
    return 0


def prepare_discovered_spec(
    *,
    env_file: str,
    sm_design_path: str | None = None,
    sm_design: dict | None = None,
    user_intent: str | None = None,
    domain: str | None = None,
    allow_web_research: bool = True,
    sm_hints: dict | None = None,
    schema_filter: str | None = None,
    max_tables: int = 500,
    payload_format: str | None = None,
    schema_summary: dict[str, Any] | None = None,
    trace_path: str | None = None,
) -> tuple[Any, Any, str, str, dict[str, Any], dict[str, Any]]:
    """Prepare a DiscoveredSpec without deploying it.

    Runs steps 1-4 of ``run_discover_sm_from_warehouse``: load config,
    inspect schema, obtain/validate SM design, and build the deployment spec.

    Returns:
        Tuple of (discovered_spec, config, base_name, _safe_base, schema_summary, sm_design_dict).
    """
    # ═══════════════════════════════════════════════════════════════════════
    # Step 1: Load config
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 1: Load config")
    print(f"{'─' * 70}")

    from kyvos_sdk.config import KyvosConfig

    config = KyvosConfig.from_env_file(env_file)
    if payload_format:
        config.payload_format = payload_format
    print(f"  Config loaded from {env_file}")
    print(f"  Payload format: {config.payload_format}")
    print(f"  Warehouse type: {config.warehouse_type}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 2: Inspect warehouse schema
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 2: Inspect warehouse schema")
    print(f"{'─' * 70}")

    if schema_summary is None:
        from kyvos_sdk.warehouse_inspector import inspect_schema

        schema_summary = inspect_schema(config, schema_filter=schema_filter, max_tables=max_tables)
    else:
        print("  Using provided schema_summary (skipping SQLAlchemy inspection).")

    print(f"  Schema: {schema_summary['schema']}")
    print(f"  Tables discovered: {schema_summary['table_count']}")
    print(f"  Relationships: {len(schema_summary['relationships'])}")

    patterns = schema_summary.get("detected_patterns", {})
    rec_pattern = patterns.get("recommended_pattern")
    if rec_pattern:
        print(f"  Recommended pattern: {rec_pattern}")
        rationale = patterns.get("pattern_rationale", "")
        if rationale:
            print(f"  Pattern rationale: {rationale}")
    # Also print legacy potential_* counts if present
    if patterns.get("potential_star_schemas"):
        print(f"  Potential star schemas: {len(patterns['potential_star_schemas'])}")
    if patterns.get("potential_snowflake_schemas"):
        print(f"  Potential snowflake schemas: {len(patterns['potential_snowflake_schemas'])}")
    if patterns.get("potential_multifact_schemas"):
        print(f"  Potential multifact schemas: {len(patterns['potential_multifact_schemas'])}")

    # Print table summary
    for t in schema_summary["tables"]:
        print(f"    {t['name']:<40s} type={t['estimated_table_type']:<12s} "
              f"cols={len(t['columns']):>3d} fk_out={t['outgoing_fk_count']} fk_in={t['incoming_fk_count']}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Obtain SM design (pre-approved JSON or LLM-generated)
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 3: Obtain SM design")
    print(f"{'─' * 70}")

    if sm_design_path:
        with open(sm_design_path) as f:
            sm_design_dict = json.load(f)
        print(f"  SM design loaded from {sm_design_path}")
    elif sm_design is not None:
        sm_design_dict = sm_design
        print("  SM design loaded from inline dict")
    elif user_intent:
        _provider = os.environ.get("LLM_PROVIDER", "anthropic")
        print(f"  Mode: LLM-based design via {_provider}")
        print(f"  User intent: {user_intent}")
        if domain:
            print(f"  Domain: {domain}")

        from kyvos_sm_skills.llm_designer import (
            design_sm_from_schema,
            validate_sm_recommendation,
        )

        sm_design_dict = design_sm_from_schema(
            schema_summary=schema_summary,
            user_intent=user_intent,
            domain=domain,
            allow_web_research=allow_web_research,
            sm_hints=sm_hints,
            llm_provider=_provider,
            trace_path=trace_path,
        )

        print("  LLM design complete")
        print(f"  Identified domain: {sm_design_dict.get('identified_domain', 'unknown')}")

        # Validate recommendation against inspected schema
        validation_errors = validate_sm_recommendation(sm_design_dict, schema_summary)
        if validation_errors:
            print("  WARNING: Validation errors in LLM recommendation:")
            for err in validation_errors:
                print(f"    - {err}")
            raise ValueError(
                f"LLM-generated SM design has {len(validation_errors)} validation error(s) "
                f"against the inspected warehouse schema."
            )
    else:
        raise ValueError(
            "Either sm_design_path, sm_design, or user_intent must be provided."
        )

    # Build a DiscoveredSpec for EVERY recommended SM (not just the first).
    recommended_sms = sm_design_dict.get("recommended_sms", [])
    if not recommended_sms:
        raise ValueError("SM design JSON must contain at least one SM in 'recommended_sms'.")

    from kyvos_sm_skills.pipeline_tracer import get_tracer
    from kyvos_sm_skills.spec_builder import build_spec_from_recommendation, merge_specs_for_review

    tracer = get_tracer()

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Build spec(s) from recommendation
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 4: Build spec from recommendation")
    print(f"{'─' * 70}")

    sm_specs: list[tuple[dict[str, Any], Any]] = []
    for i, sm_rec in enumerate(recommended_sms):
        sm_name = sm_rec.get("name", "unknown")
        print(f"\n  SM {i + 1}/{len(recommended_sms)}: {sm_name}")
        print(f"    Schema type: {sm_rec.get('schema_type', 'unknown')}")
        print(f"    Tables: {len(sm_rec.get('tables', []))}")
        print(f"    Relationships: {len(sm_rec.get('relationships', []))}")
        print(f"    Measures: {len(sm_rec.get('measures', []))}")
        print(f"    Hierarchies: {len(sm_rec.get('hierarchies', []))}")

        if tracer:
            tracer.step(
                f"Spec Builder: SM {i + 1}",
                f"SM '{sm_name}' — {len(sm_rec.get('tables', []))} tables",
            )

        spec = build_spec_from_recommendation(
            sm_rec=sm_rec,
            warehouse_tables=schema_summary["tables"],
        )
        sm_specs.append((sm_rec, spec))

        print(f"    Built: {len(spec.tables)} tables, "
              f"{len(spec.semantic_model.relationships)} rels, "
              f"{len(spec.semantic_model.measures)} measures, "
              f"{len(spec.semantic_model.hierarchies)} hierarchies")

        if tracer:
            tracer.note(
                f"SM {i + 1} spec: {sm_name}",
                f"Tables: {[t.name for t in spec.tables]}\n"
                f"Relationships: {len(spec.semantic_model.relationships)}\n"
                f"Measures: {len(spec.semantic_model.measures)}\n"
                f"Hierarchies: {len(spec.semantic_model.hierarchies)}",
            )

    # Merge all specs into one for review display (all tables visible).
    discovered_spec = merge_specs_for_review(sm_specs)

    total_tables = len(discovered_spec.tables)
    total_rels = len(discovered_spec.semantic_model.relationships)
    total_measures = len(discovered_spec.semantic_model.measures)
    total_hierarchies = len(discovered_spec.semantic_model.hierarchies)
    print(f"\n  Merged review spec: {total_tables} tables, {total_rels} rels, "
          f"{total_measures} measures, {total_hierarchies} hierarchies "
          f"(across {len(sm_specs)} SMs)")

    if tracer:
        tracer.note(
            "Merged review spec",
            f"Total tables: {[t.name for t in discovered_spec.tables]}\n"
            f"Total relationships: {total_rels}\n"
            f"Total measures: {total_measures}\n"
            f"Total hierarchies: {total_hierarchies}\n"
            f"SMs: {[r.get('name') for r, _ in sm_specs]}",
        )

    # Use the first SM's name for the base_name (folder naming).
    first_sm_name = recommended_sms[0].get("name", "DiscoveredSM")
    base_name = first_sm_name.replace("_", " ").title()
    _schema_name = schema_summary.get("schema", "") or config.warehouse_database or "DiscoveredSM"
    _safe_base = re.sub(r"[^A-Za-z0-9~@#^_-]", "", _schema_name.replace(" ", "_").replace(".", ""))
    if not _safe_base:
        _safe_base = "DiscoveredSM"

    # Store per-SM specs in the design dict for the resume/deploy path.
    sm_design_dict["_sm_specs"] = [
        {"sm_rec": sm_rec, "spec": discovered_spec_to_dict(spec)}
        for sm_rec, spec in sm_specs
    ]

    return discovered_spec, config, base_name, _safe_base, schema_summary, sm_design_dict


def run_discover_sm_from_warehouse(
    *,
    env_file: str,
    sm_design_path: str | None = None,
    sm_design: dict | None = None,
    user_intent: str | None = None,
    domain: str | None = None,
    allow_web_research: bool = True,
    sm_hints: dict | None = None,
    auto_approve: bool = False,
    schema_filter: str | None = None,
    max_tables: int = 500,
    payload_format: str | None = None,
    dry_run: bool = False,
    cleanup_dry_run: bool = False,
    perform_cleanup: bool = False,
    sm_folder_suffix: str = "",
    schema_summary: dict[str, Any] | None = None,
    kyvos_connection_name: str | None = None,
) -> int:
    """Run the discover-sm-from-warehouse skill flow.

    Supports two modes:
    1. Pre-approved JSON mode: sm_design_path or sm_design provided directly.
    2. LLM mode: user_intent provided, uses Anthropic API to generate SM design.

    Inspects the warehouse schema (or accepts a pre-built schema_summary),
    obtains/validates the SM design, builds a deployment spec, and deploys to
    Kyvos.

    Args:
        env_file: Path to the .env config file.
        sm_design_path: Path to a pre-approved SM design JSON file (mode 1).
        sm_design: Inline SM design dict (mode 1, alternative to sm_design_path).
        user_intent: Natural language analytics intent (mode 2, triggers LLM).
        domain: Optional domain hint for LLM (e.g. "adventure_works").
        allow_web_research: If False, LLM uses built-in knowledge only.
        sm_hints: Optional dict with max_sms, preferred_schema_type, etc.
        auto_approve: If True, skip interactive approval gate (for CI/CD).
        schema_filter: Warehouse schema to inspect (default per warehouse type).
        max_tables: Inspection cap (raises if exceeded).
        payload_format: Override payload format ("json" or "xml").
        dry_run: If True, inspect + build spec only, no API calls.
        schema_summary: Optional pre-built schema summary (skips SQLAlchemy
            inspection). Used by the Kyvos metadata discovery flow.
        kyvos_connection_name: Optional Kyvos connection name to use for the
            deployed datasets/DRD/SM. When provided, the pipeline skips creating
            a new warehouse connection and uses the existing one.

    Returns:
        0 on success, 1 on failure.

    Raises:
        ValueError: If neither sm_design_path/sm_design nor user_intent is
                    provided, or if the SM design references tables not found
                    in the warehouse.
        FileNotFoundError: If sm_design_path doesn't exist.
    """
    discovered_spec, config, base_name, _safe_base, schema_summary, sm_design_dict = prepare_discovered_spec(
        env_file=env_file,
        sm_design_path=sm_design_path,
        sm_design=sm_design,
        user_intent=user_intent,
        domain=domain,
        allow_web_research=allow_web_research,
        sm_hints=sm_hints,
        schema_filter=schema_filter,
        max_tables=max_tables,
        payload_format=payload_format,
        schema_summary=schema_summary,
    )

    # Approval gate for CLI / non-HITL usage
    if user_intent and not auto_approve and not dry_run:
        from kyvos_sm_skills.llm_designer import format_recommendation_for_review

        review_text = format_recommendation_for_review(sm_design_dict)
        print(review_text)
        response = _safe_input("\n  Approve this SM design? (y/n): ")
        if response != "y":
            print("  SM design rejected by user. Exiting.")
            return 1
        print("  SM design approved.")
    elif dry_run:
        from kyvos_sm_skills.llm_designer import format_recommendation_for_review

        review_text = format_recommendation_for_review(sm_design_dict)
        print(review_text)
        print(f"\n✅ Dry run complete — inspected {schema_summary['table_count']} tables, "
              f"built spec with {len(discovered_spec.tables)} tables, "
              f"{len(discovered_spec.semantic_model.relationships)} relationships, "
              f"{len(discovered_spec.semantic_model.measures)} measures")
        print(f"\n   Base name: {base_name}")
        print(f"   Folder base: {_safe_base}")
        print(f"   Schema type: {discovered_spec.metadata.get('schema_type', 'unknown')}")
        print(f"   Rationale: {discovered_spec.metadata.get('rationale', '')}")
        return 0

    # If cleanup-dry-run is requested, scan and report before deploying
    if cleanup_dry_run:
        print(f"\n{'─' * 70}")
        print(f"  Cleanup Dry Run (base_name={_safe_base})")
        print(f"{'─' * 70}")
        from kyvos_sdk.client import KyvosService
        from kyvos_sdk.inspection import InspectionClient
        from kyvos_sdk.provisioning import ProvisioningClient
        _svc = KyvosService(config=config)
        _svc.initialize()
        _prov = ProvisioningClient(_svc)
        _insp = InspectionClient(_svc)
        _collect_and_cleanup_entities(
            insp=_insp,
            prov=_prov,
            base_name=_safe_base,
            dry_run=True,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Steps 5-11: Deploy via shared pipeline
    # ═══════════════════════════════════════════════════════════════════════
    _deploy_spec(
        tables=discovered_spec.tables,
        semantic_model=discovered_spec.semantic_model,
        metadata=discovered_spec.metadata,
        base_name=_safe_base,
        config=config,
        cleanup_dry_run=cleanup_dry_run,
        perform_cleanup=perform_cleanup,
        auto_approve=auto_approve,
        sm_folder_suffix=sm_folder_suffix,
        kyvos_connection_name=kyvos_connection_name,
    )
    print("\n   Discovery source: warehouse schema inspection")
    print(f"   Schema type: {discovered_spec.metadata.get('schema_type', 'unknown')}")
    return 0


def discovered_spec_to_dict(spec: DiscoveredSpec) -> dict[str, Any]:
    """Serialize a DiscoveredSpec to a plain dict for JSON storage / review.

    Pydantic model fields are dumped recursively so the result can be stored
    in ReviewStore and consumed by the frontend review page.
    """
    return {
        "tables": [t.model_dump() for t in spec.tables],
        "semantic_model": spec.semantic_model.model_dump(),
        "metadata": spec.metadata,
    }


def dict_to_discovered_spec(spec_dict: dict[str, Any]) -> DiscoveredSpec:
    """Rehydrate a DiscoveredSpec from a dict produced by ``discovered_spec_to_dict``."""
    from kyvos_sdk.models import SemanticModelSpec, TableSpec

    tables = [TableSpec(**t) for t in spec_dict.get("tables", [])]
    semantic_model = SemanticModelSpec(**spec_dict.get("semantic_model", {"name": "DiscoveredSM"}))
    return DiscoveredSpec(
        tables=tables,
        semantic_model=semantic_model,
        metadata=spec_dict.get("metadata", {}),
    )


def deploy_prepared_spec(
    discovered_spec: DiscoveredSpec,
    config: Any,
    base_name: str,
    *,
    kyvos_connection_name: str | None = None,
    cleanup_dry_run: bool = False,
    perform_cleanup: bool = False,
    auto_approve: bool = True,
    sm_folder_suffix: str = "",
    payload_dump_dir: str | None = None,
) -> dict[str, Any]:
    """Deploy a previously prepared DiscoveredSpec to Kyvos.

    This is used by the HITL discovery flow to resume deployment after the
    user approves the generated semantic-model design.
    """
    return _deploy_spec(
        tables=discovered_spec.tables,
        semantic_model=discovered_spec.semantic_model,
        metadata=discovered_spec.metadata,
        base_name=base_name,
        config=config,
        cleanup_dry_run=cleanup_dry_run,
        perform_cleanup=perform_cleanup,
        auto_approve=auto_approve,
        sm_folder_suffix=sm_folder_suffix,
        kyvos_connection_name=kyvos_connection_name,
        payload_dump_dir=payload_dump_dir,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PBIT Deployment Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _auto_discover_jar_path() -> str:
    """Try to locate the DAX→MDX converter JAR.

    Checks in order:
    1. ``DAX_TO_MDX_JAR_PATH`` environment variable.
    2. Common project directories relative to the user's CascadeProjects folder.

    Returns:
        Path to the JAR if found, empty string otherwise.
    """
    # 1. Environment variable
    jar = os.environ.get("DAX_TO_MDX_JAR_PATH", "")
    if jar and os.path.isfile(jar):
        return jar

    # 2. Common project locations
    import glob as _glob
    _home = os.path.expanduser("~")
    _patterns = [
        os.path.join(_home, "CascadeProjects", "dax-to-mdx-converter-utility",
                     "target", "dax-to-mdx-converter-*.jar"),
    ]
    for pattern in _patterns:
        _matches = sorted(_glob.glob(pattern))
        # Prefer the shaded JAR (larger), skip 'original-' prefix
        _matches = [m for m in _matches if "original-" not in os.path.basename(m)]
        if _matches:
            return _matches[0]
    return ""


# Patterns that indicate invalid/placeholder MDX that Kyvos cannot execute.
_INVALID_MDX_PATTERNS = [
    re.compile(r"\{set\}", re.IGNORECASE),
    re.compile(r"\{measure\}", re.IGNORECASE),
    re.compile(r"\.\[\]"),           # empty member reference
    re.compile(r"\[Measures\]\.\[Value\]", re.IGNORECASE),  # default measure
    re.compile(r"Guide-backed approximation", re.IGNORECASE),
]


def _has_invalid_mdx(mdx: str) -> bool:
    """Return True if the MDX expression contains placeholder/invalid patterns."""
    for pat in _INVALID_MDX_PATTERNS:
        if pat.search(mdx):
            return True
    return False


def _apply_converted_mdx_to_measures(spec: Any) -> None:
    """Apply Java DAX→MDX conversions to calculated measure expressions.

    Reads ``spec.metadata["java_converted_mdx"]`` and replaces DAX expressions
    with converted MDX for calculated measures.  Measures with invalid/placeholder
    MDX or no successful conversion are dropped — Kyvos cannot execute DAX.

    Base measures (``is_calculated=False``) are kept as-is.

    Modifies ``spec.semantic_model.measures`` in place.
    """
    java_mdx: dict[str, dict[str, Any]] = {}
    if isinstance(spec.metadata, dict):
        java_mdx = spec.metadata.get("java_converted_mdx", {})

    if not java_mdx:
        print("  No DAX→MDX conversions available — dropping all calculated measures")
    else:
        print(f"  DAX→MDX conversions available for {len(java_mdx)} measure(s)")

    kept: list[Any] = []
    dropped_calc = 0
    dropped_invalid = 0
    converted = 0

    for m in spec.semantic_model.measures:
        if not m.is_calculated:
            # Base measure — keep as-is
            kept.append(m)
            continue

        # Calculated measure — need MDX conversion
        conv = java_mdx.get(m.name)
        if not conv:
            # No conversion available — can't deploy DAX to Kyvos
            dropped_calc += 1
            continue

        status = conv.get("status", "")
        mdx = conv.get("mdx", "")

        if status not in ("CONVERTED", "CONVERTED_WITH_APPROXIMATION"):
            # Conversion failed or needs manual review
            dropped_calc += 1
            continue

        if not mdx or _has_invalid_mdx(mdx):
            # MDX contains placeholder/invalid patterns
            dropped_invalid += 1
            continue

        # Apply the converted MDX
        m.expression = mdx
        converted += 1
        kept.append(m)

    spec.semantic_model.measures = kept
    print(f"  Measures: {len(kept)} kept ({converted} converted, "
          f"{dropped_calc} dropped [no conversion], "
          f"{dropped_invalid} dropped [invalid MDX])")


def _normalize_measure_source_columns(spec: Any) -> None:
    """Normalize base measure ``source_column`` from PBIT display names to warehouse column names.

    The PBIT parser stores display names (e.g. ``"Prorated Budget"``) in
    ``MeasureSpec.source_column``, while ``ColumnSpec.name`` holds the actual
    warehouse column name (e.g. ``prorated_budget``).  The Kyvos compiler uses
    ``source_column`` as-is for base SUM measures, so it must match the warehouse
    column name exactly.

    For each table, builds a mapping from display name → warehouse name using
    ``ColumnSpec.source_column`` (display) → ``ColumnSpec.name`` (warehouse).
    Falls back to fuzzy matching (lowercase, spaces→underscores) when the
    display name is not explicitly stored.

    Modifies ``spec.semantic_model.measures`` in place.
    """
    # Build per-table lookup maps: display_name → warehouse_name
    # Also build a fuzzy map: normalized_name → warehouse_name
    display_maps: dict[str, dict[str, str]] = {}   # table_lower → {display_lower → warehouse}
    fuzzy_maps: dict[str, dict[str, str]] = {}      # table_lower → {fuzzy_lower → warehouse}

    for t in spec.tables:
        t_lower = t.name.lower()
        d_map: dict[str, str] = {}
        f_map: dict[str, str] = {}
        for c in t.columns:
            # Exact display name → warehouse name
            if c.source_column:
                d_map[c.source_column.lower()] = c.name
            # Fuzzy: normalize warehouse name (already snake_case)
            f_map[c.name.lower().replace(" ", "_")] = c.name
        display_maps[t_lower] = d_map
        fuzzy_maps[t_lower] = f_map

    normalized = 0
    unmatched: list[str] = []

    for m in spec.semantic_model.measures:
        if m.is_calculated or not m.source_column or not m.source_dataset:
            continue

        ds_lower = m.source_dataset.lower()
        sc = m.source_column
        d_map = display_maps.get(ds_lower, {})
        f_map = fuzzy_maps.get(ds_lower, {})

        # 1. Exact display-name match
        if sc.lower() in d_map:
            m.source_column = d_map[sc.lower()]
            normalized += 1
            continue

        # 2. Fuzzy match (lowercase, spaces→underscores)
        fuzzy = sc.lower().replace(" ", "_")
        if fuzzy in f_map:
            m.source_column = f_map[fuzzy]
            normalized += 1
            continue

        # 3. Already matches a warehouse column name
        if sc in f_map.values():
            normalized += 1
            continue

        # No match found
        unmatched.append(f"{m.source_dataset}.{sc}")

    print(f"  Source column normalization: {normalized} matched, "
          f"{len(unmatched)} unmatched")
    if unmatched:
        print(f"  WARNING: Unmatched source columns: {unmatched[:10]}")


def _resolve_measure_dependencies(spec: Any) -> None:
    """Resolve implicit measure references in converted MDX expressions.

    Power BI DAX expressions often reference columns as implicit measures
    (e.g. ``[Net Sales]`` refers to the ``net_sales`` column on the current
    row context).  The Java DAX→MDX converter emits these as
    ``[Measures].[Net Sales]``, but Kyvos requires an explicit base measure
    with that name.

    This function:
    1. Extracts all ``[Measures].[Name]`` references from kept calculated measures.
    2. For references not in the measure set, tries to find a matching column
       on any fact table (by display name or fuzzy match).
    3. Creates base SUM measures for matches.
    4. Drops calculated measures that still reference non-existent measures
       (transitive — iterates until stable).

    Modifies ``spec.semantic_model.measures`` in place.
    """
    from kyvos_sm_skills.models import MeasureSpec

    # Build fact-table column lookup: display_name_lower → (table_name, column_name)
    fact_tables = [t for t in spec.tables if t.table_type == "fact"]
    col_lookup: dict[str, tuple[str, str]] = {}  # display_lower → (table, warehouse_col)
    fuzzy_lookup: dict[str, tuple[str, str]] = {}  # fuzzy_lower → (table, warehouse_col)

    for t in fact_tables:
        for c in t.columns:
            if c.source_column:
                col_lookup[c.source_column.lower()] = (t.name, c.name)
            fuzzy_key = c.name.lower().replace(" ", "_")
            fuzzy_lookup[fuzzy_key] = (t.name, c.name)

    _ref_pattern = re.compile(r"\[Measures\]\.\[([^\]]+)\]")

    created_base = 0
    dropped_unresolved = 0

    # Iterate until stable (transitive closure)
    for _iteration in range(10):
        kept_measures = spec.semantic_model.measures
        kept_names = {m.name for m in kept_measures}

        # Collect all referenced measure names from calculated measures
        referenced: set[str] = set()
        for m in kept_measures:
            if m.is_calculated and m.expression:
                referenced.update(_ref_pattern.findall(m.expression))

        # Find missing references
        missing = referenced - kept_names
        if not missing:
            break

        # Try to create base measures for missing references
        still_missing: set[str] = set()
        for ref_name in sorted(missing):
            # 1. Exact display-name match
            match = col_lookup.get(ref_name.lower())
            # 2. Fuzzy match (lowercase, spaces→underscores)
            if not match:
                fuzzy = ref_name.lower().replace(" ", "_")
                match = fuzzy_lookup.get(fuzzy)

            if match:
                table_name, col_name = match
                # Check if we already created this (avoid duplicates)
                if ref_name not in kept_names:
                    spec.semantic_model.measures.append(MeasureSpec(
                        name=ref_name,
                        expression="",
                        format_string="#,##0.00",
                        description=f"Auto-created base measure for {col_name} on {table_name}",
                        is_calculated=False,
                        source_dataset=table_name,
                        aggregation_type="sum",
                        source_column=col_name,
                        is_hidden=False,
                    ))
                    created_base += 1
                    print(f"  Auto-created base measure: '{ref_name}' → "
                          f"{table_name}.{col_name}")
            else:
                still_missing.add(ref_name)

        if still_missing:
            # Drop calculated measures that reference non-existent measures
            new_measures = []
            for m in spec.semantic_model.measures:
                if m.is_calculated and m.expression:
                    refs = set(_ref_pattern.findall(m.expression))
                    if refs & still_missing:
                        dropped_unresolved += 1
                        print(f"  Dropped '{m.name}' — references "
                              f"non-existent measure(s): {sorted(refs & still_missing)}")
                        continue
                new_measures.append(m)
            spec.semantic_model.measures = new_measures

        if not still_missing and created_base == 0:
            break

    if created_base:
        print(f"  Dependency resolution: {created_base} base measure(s) auto-created, "
              f"{dropped_unresolved} calculated measure(s) dropped [unresolved refs]")


def run_deploy_from_pbit(
    *,
    pbit_file_path: str,
    env_file: str,
    jar_path: str = "",
    warehouse_schema: str | None = None,
    payload_format: str | None = None,
    dry_run: bool = False,
    cleanup_dry_run: bool = False,
    auto_approve: bool = False,
    sm_folder_suffix: str = "",
) -> int:
    """Run the deploy-from-pbit skill flow.

    Parses a PBIT file, converts DAX measures to MDX (via the Java converter
    if available), normalizes measure source columns, removes disconnected
    dimensions, and deploys the semantic model to Kyvos.

    Args:
        pbit_file_path: Path to the ``.pbit`` file.
        env_file: Path to the ``.env`` config file.
        jar_path: Path to the DAX→MDX converter JAR.  If empty, auto-discovers
            via ``DAX_TO_MDX_JAR_PATH`` env var or common project locations.
            If not found, conversion is skipped and calculated measures are dropped.
        warehouse_schema: Override the warehouse schema name for all datasets.
            If ``None``, uses the schema derived from the PBIT filename.
        payload_format: Override payload format (``"json"`` or ``"xml"``).
        dry_run: If True, parse + process only, no API calls.
        cleanup_dry_run: If True, scan for old entities but don't delete or deploy.
        auto_approve: If True, skip interactive approval prompts.
        sm_folder_suffix: Optional suffix for the semantic model folder name.

    Returns:
        0 on success, 1 on failure.
    """
    # ═══════════════════════════════════════════════════════════════════════
    # Step 1: Load config
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 1: Load config")
    print(f"{'─' * 70}")

    from kyvos_sdk.config import KyvosConfig

    config = KyvosConfig.from_env_file(env_file)
    if payload_format:
        config.payload_format = payload_format
    print(f"  Config loaded from {env_file}")
    print(f"  Payload format: {config.payload_format}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 2: Parse PBIT + derive names
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 2: Parse PBIT + derive names")
    print(f"{'─' * 70}")

    from kyvos_pbit_parser.pbit_adapter import enrich_spec_from_pbit

    # Resolve JAR path for DAX→MDX conversion
    _jar = jar_path or _auto_discover_jar_path()
    if _jar:
        print(f"  DAX→MDX converter JAR: {_jar}")
    else:
        print("  DAX→MDX converter JAR: not found — calculated measures will be dropped")

    with open(pbit_file_path, "rb") as f:
        spec = enrich_spec_from_pbit(
            f.read(),
            filename=os.path.basename(pbit_file_path),
            skip_conversion=not bool(_jar),
            jar_path=_jar,
        )

    print(f"  Parsed: {len(spec.tables)} tables, "
          f"{len(spec.semantic_model.relationships)} relationships, "
          f"{len(spec.semantic_model.measures)} measures")

    # Derive base name from PBIT filename (not the GUID in semantic_model.name)
    _filename = os.path.basename(pbit_file_path)
    _stem = _filename.rsplit(".", 1)[0] if "." in _filename else _filename
    base_name = _stem

    # Override warehouse schema if explicitly provided
    if warehouse_schema:
        for t in spec.tables:
            t.schema_name = warehouse_schema
        spec.metadata["schema_name"] = warehouse_schema
        print(f"  Schema override: {warehouse_schema}")
    else:
        _derived_schema = spec.metadata.get("schema_name", "")
        print(f"  Schema (from filename): {_derived_schema}")

    print(f"  Base name: {base_name}")

    if dry_run:
        print(f"\n  Dry run — parsed {len(spec.tables)} tables, "
              f"{len(spec.semantic_model.relationships)} relationships, "
              f"{len(spec.semantic_model.measures)} measures")
        # Still show what the measure processing would do
        print(f"\n{'─' * 70}")
        print("  Dry run: measure processing preview")
        print(f"{'─' * 70}")
        _apply_converted_mdx_to_measures(spec)
        _normalize_measure_source_columns(spec)
        _resolve_measure_dependencies(spec)
        print(f"\n  After processing: {len(spec.semantic_model.measures)} measures, "
              f"{len(spec.tables)} tables")
        return 0

    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Process measures
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 3: Process measures (DAX→MDX + source column normalization)")
    print(f"{'─' * 70}")

    # 3a. Apply converted MDX to calculated measures
    _apply_converted_mdx_to_measures(spec)

    # 3b. Normalize base measure source_columns from display names to warehouse names
    _normalize_measure_source_columns(spec)

    # 3c. Resolve implicit measure references in converted MDX — auto-create
    # base measures for column references that Power BI treated as implicit measures
    _resolve_measure_dependencies(spec)

    # 3d. Set a human-readable SM name (not the GUID from PBIT)
    spec.semantic_model.name = base_name

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Connectivity sweep — remove disconnected dimensions
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print("  Step 4: Connectivity sweep")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.spec_builder import _connectivity_sweep

    _before_tables = len(spec.tables)
    spec.tables, spec.semantic_model.relationships, spec.semantic_model.measures = (
        _connectivity_sweep(
            table_specs=spec.tables,
            relationships=spec.semantic_model.relationships,
            measures=spec.semantic_model.measures,
            wh_table_map=None,  # No warehouse metadata in PBIT flow — just drop disconnected
            follow_all_edges=True,  # PBIT relationships may be dim→bridge, etc.
        )
    )
    _dropped = _before_tables - len(spec.tables)
    if _dropped:
        print(f"  Dropped {_dropped} disconnected table(s): "
              f"{_before_tables} → {len(spec.tables)} tables")
    else:
        print(f"  All {len(spec.tables)} table(s) reachable from fact tables with measures")

    print(f"  Final: {len(spec.tables)} tables, "
          f"{len(spec.semantic_model.relationships)} relationships, "
          f"{len(spec.semantic_model.measures)} measures")

    # ═══════════════════════════════════════════════════════════════════════
    # Steps 5-9: Deploy via shared pipeline
    # ═══════════════════════════════════════════════════════════════════════
    _deploy_spec(
        tables=spec.tables,
        semantic_model=spec.semantic_model,
        metadata=spec.metadata if isinstance(spec.metadata, dict) else {},
        base_name=base_name,
        config=config,
        skip_hidden_tables=config.skip_hidden_tables,
        cleanup_dry_run=cleanup_dry_run,
        perform_cleanup=not cleanup_dry_run,
        auto_approve=auto_approve,
        sm_folder_suffix=sm_folder_suffix,
    )
    print(f"\n   PBIT source: {pbit_file_path}")
    return 0
