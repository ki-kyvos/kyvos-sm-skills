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
from datetime import datetime
from pathlib import Path
from typing import Any

from kyvos_sdk.contracts.common import Severity


_MIN_PREFIX_LEN = 8


def _safe_input(prompt: str) -> str:
    """Prompt for user input, returning empty string on EOF in non-interactive environments."""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        print(f"\n  (Non-interactive environment detected — defaulting to rejection.)")
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
        f.write(f"Cleanup Audit Log\n")
        f.write(f"Timestamp: {now.isoformat()}\n")
        f.write(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")
        f.write(f"Base name: {base_name}\n")
        f.write(f"Prefixes: {list(prefixes)}\n")
        f.write(f"Entities found: {len(targets)}\n")
        f.write(f"Entities deleted: {deleted}\n")
        f.write(f"\n--- Entity Details ---\n")
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
) -> bool:
    """Collect and optionally delete old entities matching the base_name prefixes.

    Scans all RDATASET, DATASET_RELATIONSHIP, and SMODEL folders for entities
    whose names start with any derived prefix.  Also matches folder names.

    Safety features:
    - Protected folders (from KYVOS_PROTECTED_FOLDERS env var) are never deleted.
    - Prefix collision warning aborts if a prefix is too generic.
    - Confirmation gate requires user input even with auto_approve for live deletes.
    - Audit log is written for every cleanup run.

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
        print(f"\n  ⚠️  PREFIX COLLISION WARNING:")
        for w in collision_warnings:
            print(f"    {w}")
        if not dry_run:
            print(f"  Aborting cleanup due to prefix collision risk.")
            return False

    # Merge skip_folders with protected folders
    protected = _get_protected_folders()
    if protected:
        print(f"  Protected folders: {sorted(protected)}")
    skip_folders = (skip_folders or set()) | protected

    def _matches(name: str) -> bool:
        lower = name.lower().lstrip()
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
            # Folder matches — collect entities inside it
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
        print(f"\n  DRY RUN: No entities were deleted.")
        # Write audit log even for dry runs
        log_path = _write_audit_log(targets, 0, base_name, prefixes, dry_run=True)
        print(f"  Audit log written to: {log_path}")
        return False

    # Confirmation gate — even with auto_approve, warn for live deletes
    if not auto_approve:
        print(f"\n  ⚠️  About to delete {len(targets)} entities. This cannot be undone.")
        response = _safe_input(f"  Type 'yes' to proceed: ")
        if response != "yes":
            print(f"  Cleanup aborted by user.")
            log_path = _write_audit_log(targets, 0, base_name, prefixes, dry_run=False)
            print(f"  Audit log written to: {log_path}")
            return False
    else:
        print(f"\n  Auto-approved: skipping confirmation gate for {len(targets)} entities.")

    print(f"\n  Performing cleanup...")
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
    from kyvos_sdk.config import KyvosConfig
    from kyvos_sdk.client import KyvosService
    from kyvos_sdk.provisioning import ProvisioningClient
    from kyvos_sdk.inspection import InspectionClient

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
        print(f"  Waiting 10s for server to process deletions...")
        time.sleep(10)

    return 0


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
) -> dict[str, Any]:
    """Shared deployment pipeline — steps 3-9 of the XMLA skill flow.

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
    print(f"  Step 3: Initialize Kyvos client")
    print(f"{'─' * 70}")

    from kyvos_sdk.client import KyvosService
    from kyvos_sdk.provisioning import ProvisioningClient
    from kyvos_sdk.inspection import InspectionClient
    from kyvos_sdk.contracts.identity import FolderType

    # Prevent XML fallback when JSON is configured — surface errors as-is
    if config.payload_format.lower() == "json":
        os.environ["KYVOS_DISABLE_JSON_FALLBACK"] = "1"

    svc = KyvosService(config=config)
    svc.initialize()
    prov = ProvisioningClient(svc)
    insp = InspectionClient(svc)
    print("  Kyvos client initialized")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Create or reuse folders + clean up existing entities
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 4: Create or reuse folders")
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
    def _cleanup_folder_entities(folder_type, folder_name):
        """Delete all entities in a folder before reusing it."""
        if folder_type == FolderType.RDATASET:
            list_result = insp.list_datasets_in_folder(folder_name)
            if list_result.succeeded and list_result.entity_refs:
                for ref in list_result.entity_refs:
                    print(f"    Deleting existing dataset: {ref.name} (id={ref.id})")
                    prov.delete_dataset(ref.id)
        elif folder_type == FolderType.DATASET_RELATIONSHIP:
            list_result = insp.list_drds_in_folder(folder_name)
            if list_result.succeeded and list_result.entity_refs:
                for ref in list_result.entity_refs:
                    print(f"    Deleting existing DRD: {ref.name} (id={ref.id})")
                    prov.delete_drd(ref.id)
        elif folder_type == FolderType.SMODEL:
            list_result = insp.list_smodels_in_folder(folder_name)
            if list_result.succeeded and list_result.entity_refs:
                for ref in list_result.entity_refs:
                    print(f"    Deleting existing SM: {ref.name} (id={ref.id})")
                    prov.delete_smodel(ref.id)

    # --- Clean up old entities from previous runs ---
    # Uses the shared helper with prefixes derived from base_name AND the SM name.
    # This catches entities from previous runs that may have used different
    # naming conventions (e.g., "AdventureWorks" prefix from older runs).
    # By default (perform_cleanup=True), old entities are deleted to avoid
    # global measure name conflicts on the Kyvos server.
    _stable_folder_names = {dataset_folder_label, drd_folder_label, smodel_folder_label}
    if sm_folder_suffix:
        # Protect the base (non-suffixed) folders so cleanup never touches other flows' entities
        _stable_folder_names.add(f"{base_name}")
        _stable_folder_names.add(f"{base_name}_DRD")
        _stable_folder_names.add(f"{base_name}_SModel")
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
    )
    if _did_cleanup:
        print(f"  Waiting 10s for server to process deletions...")
        time.sleep(10)

    # --- Dataset folder: find or create ---
    existing_ds_folder_id = _find_existing_folder(FolderType.RDATASET, dataset_folder_label)
    if existing_ds_folder_id:
        folder_id = existing_ds_folder_id
        print(f"Dataset folder: {dataset_folder_label} (id={folder_id}) — reusing existing")
        print(f"  Cleaning up existing datasets...")
        _cleanup_folder_entities(FolderType.RDATASET, dataset_folder_label)
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
        print(f"  Cleaning up existing DRDs...")
        _cleanup_folder_entities(FolderType.DATASET_RELATIONSHIP, drd_folder_label)
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
        print(f"  Cleaning up existing semantic models...")
        _cleanup_folder_entities(FolderType.SMODEL, smodel_folder_label)
    else:
        smodel_folder_result = prov.create_folder(smodel_folder_label, FolderType.SMODEL)
        if not smodel_folder_result.succeeded:
            raise RuntimeError(
                f"Semantic model folder creation failed: {[d.message for d in smodel_folder_result.diagnostics]}"
            )
        smodel_folder_id = smodel_folder_result.primary_entity_id
        print(f"Semantic model folder: {smodel_folder_label} (id={smodel_folder_id}) — created")

    # Brief delay after cleanup to let Kyvos server process deletions
    if existing_ds_folder_id or existing_drd_folder_id or existing_sm_folder_id:
        print(f"  Waiting 10s for server to process folder entity deletions...")
        time.sleep(10)

    # ═══════════════════════════════════════════════════════════════════════
    # Step 5: Create connection
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 5: Create connection")
    print(f"{'─' * 70}")

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
    print(f"Connection: {config.warehouse_connection_name} (id={connection_id})")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 6: Create datasets
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 6: Create datasets")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.contract_adapter import compile_dataset_artifact

    dataset_name_to_id = {}
    dataset_aliases = {}
    created_entities = []

    for table in tables:
        if skip_hidden_tables and table.is_hidden:
            continue

        ds_artifact = compile_dataset_artifact(
            table,
            connection_name=config.warehouse_connection_name,
            folder_id=folder_id,
            folder_name=dataset_folder_label,
            fmt=config.payload_format,
        )
        ds_result = prov.apply_artifact(ds_artifact)

        if not ds_result.succeeded:
            raise RuntimeError(
                f"Dataset creation failed for {table.name}: "
                f"{[d.message for d in ds_result.diagnostics]}\n"
                f"Created so far: {dataset_name_to_id}"
            )

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

        prov.refresh_dataset_columns(ds_id)
        print(f"  Dataset: {server_name} (id={ds_id})")

    # Second refresh sweep + validate all datasets
    validation_errors = []
    for ds_info in created_entities:
        if ds_info["entity_type"] != "DATASET":
            continue
        prov.refresh_dataset_columns(ds_info["id"])
        val_result = prov.validate_dataset(
            ds_info["id"], ds_info["name"], dataset_folder_label
        )
        if not val_result.succeeded:
            errs = [d.message for d in val_result.diagnostics if d.severity == Severity.ERROR]
            validation_errors.append(f"{ds_info['name']}: {errs}")

    if validation_errors:
        raise RuntimeError(
            f"Dataset validation failed — pipeline halted:\n" +
            "\n".join(validation_errors)
        )

    # Fetch column details for semantic model compilation
    server_to_spec_table = {}
    for table in tables:
        server_name = dataset_aliases.get(table.name, table.name)
        server_to_spec_table[server_name] = table
        server_to_spec_table[server_name.lower()] = table

    dataset_cols = {}
    for ds_info in created_entities:
        if ds_info["entity_type"] != "DATASET":
            continue
        try:
            cols = prov.get_dataset_column_details(dataset_folder_label, ds_info["name"])
            if cols:
                dataset_cols[ds_info["name"]] = cols
            else:
                raise ValueError("empty column list")
        except Exception:
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
                print(f"  WARNING: No column details for {ds_info['name']} — no spec fallback available")

    print(f"Datasets validated and column details fetched for {len(dataset_cols)} datasets")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 7: Build DRD graph + create DRD
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 7: Build DRD graph + create DRD")
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

        if not skip and right_cols_raw is not None and rel.right_column.lower() not in {c["name"].lower() for c in right_cols_raw}:
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

    fact_dataset_names = set()
    for table in tables:
        if table.table_type == "fact":
            server_name = dataset_aliases.get(table.name, table.name)
            fact_dataset_names.add(server_name)
    print(f"  Fact datasets: {fact_dataset_names}")

    drd_artifact = compile_drd_artifact(
        drd_name=drd_name,
        drd_id=drd_id,
        folder_id=drd_folder_id,
        folder_name=drd_folder_label,
        dataset_name_to_id=dataset_name_to_id,
        relationships=validated_rels,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
        fmt=config.payload_format,
    )

    drd_result = prov.apply_artifact(drd_artifact)
    if not drd_result.succeeded:
        raise RuntimeError(
            f"DRD creation failed: {[d.message for d in drd_result.diagnostics]}"
        )

    server_drd_id = drd_result.primary_entity_id
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
            print(f"  DRD validation pending (attempt {_attempt}/{_max_validation_retries}), retrying in {_validation_delay}s...")
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
    print(f"  Step 8: Compile + create semantic model")
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
        connection_name=config.warehouse_connection_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=semantic_model.relationships,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
        dataset_columns=dataset_cols,
        fmt=config.payload_format,
    )

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

    # Validate semantic model — retry up to 8 times with 30s delay (large models may hit server capacity limits)
    _sm_max_retries = 8
    _sm_retry_delay = 30
    for _attempt in range(1, _sm_max_retries + 1):
        sm_val_result = prov.validate_semantic_model(smodel_id, smodel_name, smodel_folder_label)
        if sm_val_result.succeeded:
            break
        # Check if this is a transient server capacity error (500) — keep retrying
        _is_capacity_error = any("capacity" in d.message.lower() for d in sm_val_result.diagnostics)
        if _is_capacity_error and _attempt < _sm_max_retries:
            print(f"  SM validation pending (attempt {_attempt}/{_sm_max_retries}), retrying in {_sm_retry_delay}s...")
            time.sleep(_sm_retry_delay)
        elif not _is_capacity_error:
            # Real validation errors — don't retry, report immediately
            errs = [d.message for d in sm_val_result.diagnostics if d.severity in (Severity.ERROR, Severity.WARNING)]
            if not errs:
                errs = [d.message for d in sm_val_result.diagnostics]
            print(f"  SM validation FAILED with {len(errs)} error(s):")
            for e in errs[:10]:
                print(f"    - {e}")
            if len(errs) > 10:
                print(f"    ... and {len(errs) - 10} more")
            raise RuntimeError(f"Semantic model validation failed with {len(errs)} error(s): {errs[:5]}")
        elif _attempt < _sm_max_retries:
            print(f"  SM validation pending (attempt {_attempt}/{_sm_max_retries}), retrying in {_sm_retry_delay}s...")
            time.sleep(_sm_retry_delay)
        else:
            errs = [d.message for d in sm_val_result.diagnostics]
            print(f"  WARNING: SM validation could not complete due to server capacity limits.")
            print(f"  SM was created successfully (id={smodel_id}) but validation timed out.")
            print(f"  The model can be validated manually from the Kyvos UI.")
            break

    print(f"Semantic Model: {smodel_name} (id={smodel_id}) — validated")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 9: Report results
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 9: Report results")
    print(f"{'─' * 70}")

    result = {
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
        "created_entities": created_entities + [
            {"entity_type": "FOLDER", "id": folder_id,        "name": dataset_folder_label},
            {"entity_type": "FOLDER", "id": drd_folder_id,    "name": drd_folder_label},
            {"entity_type": "FOLDER", "id": smodel_folder_id, "name": smodel_folder_label},
            {"entity_type": "CONNECTION", "id": connection_id, "name": config.warehouse_connection_name},
        ],
        "errors": [],
        "warnings": [],
    }
    print(f"\n✅ Deployment Successful")
    print(f"   Timestamp     : {_ts}")
    print(f"   Tables        : {len(tables)}")
    print(f"   Datasets      : {len(dataset_name_to_id)}")
    print(f"   Relationships : {len(semantic_model.relationships)}")
    print(f"   Measures      : {len(semantic_model.measures)}")
    print(f"   Connection    : {config.warehouse_connection_name}")
    print(f"   DRD           : {drd_name} (id={server_drd_id})")
    print(f"   Semantic Model: {smodel_name}")

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
    print(f"  Step 1: Load config")
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
    print(f"  Step 2: Parse XMLA + derive names")
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
    drd_id           = f"drd_{smodel_name}"

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
    result = _deploy_spec(
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
) -> int:
    """Run the discover-sm-from-warehouse skill flow.

    Supports two modes:
    1. Pre-approved JSON mode: sm_design_path or sm_design provided directly.
    2. LLM mode: user_intent provided, uses Anthropic API to generate SM design.

    Inspects the warehouse schema, obtains/validates the SM design, builds a
    deployment spec, and deploys to Kyvos.

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

    Returns:
        0 on success, 1 on failure.

    Raises:
        ValueError: If neither sm_design_path/sm_design nor user_intent is
                    provided, or if the SM design references tables not found
                    in the warehouse.
        FileNotFoundError: If sm_design_path doesn't exist.
    """
    # ═══════════════════════════════════════════════════════════════════════
    # Step 1: Load config
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 1: Load config")
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
    print(f"  Step 2: Inspect warehouse schema")
    print(f"{'─' * 70}")

    from kyvos_sdk.warehouse_inspector import inspect_schema

    schema_summary = inspect_schema(config, schema_filter=schema_filter, max_tables=max_tables)

    print(f"  Schema: {schema_summary['schema']}")
    print(f"  Tables discovered: {schema_summary['table_count']}")
    print(f"  Relationships: {len(schema_summary['relationships'])}")

    patterns = schema_summary["detected_patterns"]
    if patterns["potential_star_schemas"]:
        print(f"  Potential star schemas: {len(patterns['potential_star_schemas'])}")
    if patterns["potential_snowflake_schemas"]:
        print(f"  Potential snowflake schemas: {len(patterns['potential_snowflake_schemas'])}")
    if patterns["potential_multifact_schemas"]:
        print(f"  Potential multifact schemas: {len(patterns['potential_multifact_schemas'])}")

    # Print table summary
    for t in schema_summary["tables"]:
        print(f"    {t['name']:<40s} type={t['estimated_table_type']:<12s} "
              f"cols={len(t['columns']):>3d} fk_out={t['outgoing_fk_count']} fk_in={t['incoming_fk_count']}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Obtain SM design (pre-approved JSON or LLM-generated)
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 3: Obtain SM design")
    print(f"{'─' * 70}")

    if sm_design_path:
        with open(sm_design_path) as f:
            sm_design_dict = json.load(f)
        print(f"  SM design loaded from {sm_design_path}")
    elif sm_design is not None:
        sm_design_dict = sm_design
        print(f"  SM design loaded from inline dict")
    elif user_intent:
        _provider = os.environ.get("LLM_PROVIDER", "anthropic")
        print(f"  Mode: LLM-based design via {_provider}")
        print(f"  User intent: {user_intent}")
        if domain:
            print(f"  Domain: {domain}")

        from kyvos_sm_skills.llm_designer import (
            design_sm_from_schema,
            format_recommendation_for_review,
            validate_sm_recommendation,
        )

        sm_design_dict = design_sm_from_schema(
            schema_summary=schema_summary,
            user_intent=user_intent,
            domain=domain,
            allow_web_research=allow_web_research,
            sm_hints=sm_hints,
            llm_provider=_provider,
        )

        print(f"  LLM design complete")
        print(f"  Identified domain: {sm_design_dict.get('identified_domain', 'unknown')}")

        # Validate recommendation against inspected schema
        validation_errors = validate_sm_recommendation(sm_design_dict, schema_summary)
        if validation_errors:
            print(f"  WARNING: Validation errors in LLM recommendation:")
            for err in validation_errors:
                print(f"    - {err}")
            raise ValueError(
                f"LLM-generated SM design has {len(validation_errors)} validation error(s) "
                f"against the inspected warehouse schema."
            )

        # Approval gate
        if not auto_approve and not dry_run:
            review_text = format_recommendation_for_review(sm_design_dict)
            print(review_text)
            response = _safe_input("\n  Approve this SM design? (y/n): ")
            if response != "y":
                print("  SM design rejected by user. Exiting.")
                return 1
            print("  SM design approved.")
        elif dry_run:
            review_text = format_recommendation_for_review(sm_design_dict)
            print(review_text)
    else:
        raise ValueError(
            "Either sm_design_path, sm_design, or user_intent must be provided."
        )

    # Extract the first SM recommendation (the flow supports multiple, but we deploy one at a time)
    recommended_sms = sm_design_dict.get("recommended_sms", [])
    if not recommended_sms:
        raise ValueError("SM design JSON must contain at least one SM in 'recommended_sms'.")

    sm_rec = recommended_sms[0]
    print(f"  SM name: {sm_rec.get('name', 'unknown')}")
    print(f"  Schema type: {sm_rec.get('schema_type', 'unknown')}")
    print(f"  Tables: {len(sm_rec.get('tables', []))}")
    print(f"  Relationships: {len(sm_rec.get('relationships', []))}")
    print(f"  Measures: {len(sm_rec.get('measures', []))}")
    print(f"  Hierarchies: {len(sm_rec.get('hierarchies', []))}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Build spec from recommendation
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 4: Build spec from recommendation")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.spec_builder import build_spec_from_recommendation

    discovered_spec = build_spec_from_recommendation(
        sm_rec=sm_rec,
        warehouse_tables=schema_summary["tables"],
    )

    print(f"  Built spec: {len(discovered_spec.tables)} tables, "
          f"{len(discovered_spec.semantic_model.relationships)} relationships, "
          f"{len(discovered_spec.semantic_model.measures)} measures, "
          f"{len(discovered_spec.semantic_model.hierarchies)} hierarchies")

    base_name = sm_rec.get("name", "DiscoveredSM").replace("_", " ").title()
    # Derive a deterministic folder base name from the warehouse schema name.
    # This ensures folders are stable and reusable across runs, regardless of
    # the LLM-generated SM name (which changes each run).
    # The LLM-generated name is still used for the SM entity name (with timestamp).
    _schema_name = schema_summary.get("schema", "") or config.warehouse_database or "DiscoveredSM"
    _safe_base = re.sub(r"[^A-Za-z0-9~@#^_-]", "", _schema_name.replace(" ", "_").replace(".", ""))
    if not _safe_base:
        _safe_base = "DiscoveredSM"

    # If cleanup-dry-run is requested, scan and report before any dry-run exit
    if cleanup_dry_run:
        print(f"\n{'─' * 70}")
        print(f"  Cleanup Dry Run (base_name={_safe_base})")
        print(f"{'─' * 70}")
        from kyvos_sdk.client import KyvosService
        from kyvos_sdk.provisioning import ProvisioningClient
        from kyvos_sdk.inspection import InspectionClient
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

    if dry_run:
        print(f"\n✅ Dry run complete — inspected {schema_summary['table_count']} tables, "
              f"built spec with {len(discovered_spec.tables)} tables, "
              f"{len(discovered_spec.semantic_model.relationships)} relationships, "
              f"{len(discovered_spec.semantic_model.measures)} measures")
        print(f"\n   Base name: {base_name}")
        print(f"   Folder base: {_safe_base}")
        print(f"   Schema type: {discovered_spec.metadata.get('schema_type', 'unknown')}")
        print(f"   Rationale: {discovered_spec.metadata.get('rationale', '')}")
        return 0

    # ═══════════════════════════════════════════════════════════════════════
    # Steps 5-11: Deploy via shared pipeline
    # ═══════════════════════════════════════════════════════════════════════
    result = _deploy_spec(
        tables=discovered_spec.tables,
        semantic_model=discovered_spec.semantic_model,
        metadata=discovered_spec.metadata,
        base_name=_safe_base,
        config=config,
        cleanup_dry_run=cleanup_dry_run,
        perform_cleanup=perform_cleanup,
        auto_approve=auto_approve,
        sm_folder_suffix=sm_folder_suffix,
    )
    print(f"\n   Discovery source: warehouse schema inspection")
    print(f"   Schema type: {discovered_spec.metadata.get('schema_type', 'unknown')}")
    return 0
