"""Programmatic runner for the deploy-from-xmla skill flow.

This module mirrors the code in ``skills/deploy-from-xmla.md`` exactly —
no custom logic.  It allows running the full deployment pipeline without
Claude Code, using only the pip-installed packages.

Used by:
    - ``kyvos-skills deploy`` CLI command
    - ``validate_skill_flow_adventureworks.py`` script
    - Any Python script that wants to run the skill flow
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path


def run_deploy_from_xmla(
    *,
    xmla_file_path: str,
    env_file: str,
    payload_format: str | None = None,
    dry_run: bool = False,
    live: bool = True,
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

    dataset_folder_label = f"{base_name} {_ts}"
    drd_folder_label     = f"{base_name} DRD {_ts}"
    smodel_folder_label  = f"{base_name} SModel {_ts}"

    print(f"Base name     : {base_name}")
    print(f"Timestamp     : {_ts}")
    print(f"Semantic model: {smodel_name}")
    print(f"DRD name      : {drd_name}")
    print(f"Dataset folder: {dataset_folder_label}")
    print(f"DRD folder    : {drd_folder_label}")
    print(f"SModel folder : {smodel_folder_label}")

    if dry_run:
        print(f"\n✅ Dry run complete — parsed {len(spec.tables)} tables, "
              f"{len(spec.semantic_model.relationships)} relationships, "
              f"{len(spec.semantic_model.measures)} measures")
        return 0

    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Initialize Kyvos client
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 3: Initialize Kyvos client")
    print(f"{'─' * 70}")

    from kyvos_sdk.client import KyvosService
    from kyvos_sdk.provisioning import ProvisioningClient
    from kyvos_sdk.contracts.identity import FolderType

    svc = KyvosService(config=config)
    svc.initialize()
    prov = ProvisioningClient(svc)
    print("  Kyvos client initialized")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Create folders
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 4: Create folders")
    print(f"{'─' * 70}")

    dataset_folder_result = prov.create_folder(dataset_folder_label, FolderType.RDATASET)
    if not dataset_folder_result.succeeded:
        raise RuntimeError(
            f"Dataset folder creation failed: {[d.message for d in dataset_folder_result.diagnostics]}"
        )
    folder_id = dataset_folder_result.primary_entity_id
    print(f"Dataset folder: {dataset_folder_label} (id={folder_id})")

    drd_folder_result = prov.create_folder(drd_folder_label, FolderType.DATASET_RELATIONSHIP)
    if not drd_folder_result.succeeded:
        raise RuntimeError(
            f"DRD folder creation failed: {[d.message for d in drd_folder_result.diagnostics]}"
        )
    drd_folder_id = drd_folder_result.primary_entity_id
    print(f"DRD folder: {drd_folder_label} (id={drd_folder_id})")

    smodel_folder_result = prov.create_folder(smodel_folder_label, FolderType.SMODEL)
    if not smodel_folder_result.succeeded:
        raise RuntimeError(
            f"Semantic model folder creation failed: {[d.message for d in smodel_folder_result.diagnostics]}"
        )
    smodel_folder_id = smodel_folder_result.primary_entity_id
    print(f"Semantic model folder: {smodel_folder_label} (id={smodel_folder_id})")

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

    for table in spec.tables:
        if config.skip_hidden_tables and table.is_hidden:
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
            errs = [d.message for d in val_result.diagnostics if d.severity == "ERROR"]
            validation_errors.append(f"{ds_info['name']}: {errs}")

    if validation_errors:
        raise RuntimeError(
            f"Dataset validation failed — pipeline halted:\n" +
            "\n".join(validation_errors)
        )

    # Fetch column details for semantic model compilation
    server_to_spec_table = {}
    for table in spec.tables:
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

    for rel in spec.semantic_model.relationships:
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

    print(f"  Valid relationships: {len(validated_rels)} / {len(spec.semantic_model.relationships)}")

    fact_dataset_names = set()
    for table in spec.tables:
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
            errs = [d.message for d in drd_val_result.diagnostics if d.severity == "ERROR"]
            raise RuntimeError(f"DRD validation failed — pipeline halted: {errs}")

    print(f"DRD: {drd_name} (id={server_drd_id}) — validated")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 8: Compile + create semantic model
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 70}")
    print(f"  Step 8: Compile + create semantic model")
    print(f"{'─' * 70}")

    from kyvos_sm_skills.contract_adapter import compile_smodel_artifact

    spec.semantic_model.name = smodel_name

    sm_artifact = compile_smodel_artifact(
        spec.semantic_model,
        drd_name=drd_name,
        drd_id=server_drd_id,
        folder_id=smodel_folder_id,
        folder_name=smodel_folder_label,
        connection_name=config.warehouse_connection_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=spec.semantic_model.relationships,
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
            f"measure_source_datasets={[m.source_dataset for m in spec.semantic_model.measures]}"
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

    # Validate semantic model — retry up to 3 times with 5s delay
    for _attempt in range(1, _max_validation_retries + 1):
        sm_val_result = prov.validate_semantic_model(smodel_id, smodel_name, smodel_folder_label)
        if sm_val_result.succeeded:
            break
        if _attempt < _max_validation_retries:
            print(f"  SM validation pending (attempt {_attempt}/{_max_validation_retries}), retrying in {_validation_delay}s...")
            time.sleep(_validation_delay)
        else:
            errs = [d.message for d in sm_val_result.diagnostics if d.severity == "ERROR"]
            raise RuntimeError(f"Semantic model validation failed: {errs}")

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
            "tables": len(spec.tables),
            "relationships": len(spec.semantic_model.relationships),
            "measures": len(spec.semantic_model.measures),
            "hierarchies": len(spec.semantic_model.hierarchies),
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
    print(f"   XMLA model    : {spec.metadata.get('xmla_db_name', base_name)}")
    print(f"   Timestamp     : {_ts}")
    print(f"   Tables parsed : {len(spec.tables)}")
    print(f"   Datasets      : {len(dataset_name_to_id)}")
    print(f"   Relationships : {len(spec.semantic_model.relationships)}")
    print(f"   Measures      : {len(spec.semantic_model.measures)}")
    print(f"   Connection    : {config.warehouse_connection_name}")
    print(f"   DRD           : {drd_name} (id={server_drd_id})")
    print(f"   Semantic Model: {smodel_name}")

    return 0
