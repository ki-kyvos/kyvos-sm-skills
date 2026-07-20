#!/usr/bin/env python3
"""End-to-end skill flow validation using AdventureWorks XMLA.

This script mirrors the deploy-from-xmla.md skill flow **exactly** —
no custom logic, no special-casing.  It runs the same code snippets
Claude would run when executing the skill, just wrapped in a test
harness with a mock or live KyvosService.

By default it uses a mock KyvosService (no live server needed).
With --live, it uses the real KyvosService to create actual entities
on your Kyvos server.

Usage:
    # Mock dry-run (no server needed)
    python scripts/validate_skill_flow_adventureworks.py [--env-file .env] [--xmla-path PATH]

    # Live E2E (creates real entities on Kyvos server)
    python scripts/validate_skill_flow_adventureworks.py --live [--env-file .env] [--xmla-path PATH]

Prerequisites:
    pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Defaults ────────────────────────────────────────────────────────────────

_CASCADE_PROJECTS = Path(__file__).resolve().parents[2]
DEFAULT_XMLA_PATHS = [
    _CASCADE_PROJECTS / "agentic-ai-demo-automation" / "samples" / "xmla" / "AdventureWorks.xmla",
    _CASCADE_PROJECTS / "kyvos-sm-skills" / "samples" / "xmla" / "AdventureWorks.xmla",
]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _print_step(n, msg: str) -> None:
    print(f"\n{'─' * 70}")
    print(f"  Step {n}: {msg}")
    print(f"{'─' * 70}")


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def _info(msg: str) -> None:
    print(f"  ℹ️  {msg}")


# ── Mock KyvosService ───────────────────────────────────────────────────────


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to CamelCase (simulating Kyvos server naming)."""
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts if p)


class MockKyvosService:
    """Mock KyvosService that simulates API responses with entity IDs.

    Implements exactly the methods that ProvisioningClient and the skill
    flow call on KyvosService — no more, no less.
    """

    def __init__(self):
        self._counter = 0
        self.created_datasets: dict[str, str] = {}

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:05d}"

    def initialize(self) -> None:
        pass

    def ensure_authenticated(self) -> None:
        pass

    # ── Folder operations ──
    def create_folder(self, name, folder_type=None):
        fid = self._next_id("folder")
        return (fid, name)

    def list_folders(self, folder_type=None):
        return []

    def delete_folder(self, folder_id, folder_type=None):
        pass

    # ── Connection operations ──
    def create_or_update_connection_json(self, **kwargs):
        cid = self._next_id("conn")
        return (cid, True)

    def create_or_update_connection_xml(self, name=None, host=None, port=None,
                                        database=None, username=None, password=None,
                                        db_type=None, db_version=None,
                                        use_existing_if_found=False,
                                        jdbc_url_override="", driver_override=""):
        cid = self._next_id("conn")
        return (cid, True)

    # ── Dataset operations ──
    def create_dataset_json(self, table, connection_name, folder_id=None, folder_name=None):
        name = table.name if hasattr(table, "name") else str(table)
        server_name = _snake_to_camel(name)
        did = self._next_id("ds")
        self.created_datasets[server_name] = did
        return (server_name, did)

    def create_dataset_from_compiled_json(
        self, payload, *, dataset_name="", folder_name="",
    ):
        ds_name = dataset_name or payload.get("name", "")
        server_name = _snake_to_camel(ds_name) if ds_name else f"Dataset_{self._counter}"
        did = self._next_id("ds")
        self.created_datasets[server_name] = did
        return (server_name, did)

    def create_dataset_xml(self, xml, ds_name):
        did = self._next_id("ds")
        server_name = _snake_to_camel(ds_name)
        self.created_datasets[server_name] = did
        return did

    def create_dataset(self, table, connection_name, folder_name=None):
        name = table.name if hasattr(table, "name") else str(table)
        server_name = _snake_to_camel(name)
        did = self._next_id("ds")
        self.created_datasets[server_name] = did
        return (server_name, did)

    def list_registered_datasets_in_folder(self, folder_name):
        return dict(self.created_datasets)

    # ── Column operations ──
    def refresh_dataset_columns(self, dataset_id):
        return {"status": "ok"}

    def get_dataset_column_details(self, folder_label, dataset_name):
        return []

    # ── DRD operations ──
    def create_dataset_relationship_drd(self, drd_xml):
        drd_id = self._next_id("drd")
        return f'<RESPONSE><CODE>0</CODE><ID>{drd_id}</ID></RESPONSE>'

    def create_dataset_relationship_drd_json(self, payload):
        drd_id = self._next_id("drd")
        return json.dumps({"entityId": drd_id})

    # ── Semantic model operations ──
    def create_semantic_model_xml(self, smodel_xml):
        sm_id = self._next_id("sm")
        return f'<RESPONSE><CODE>0</CODE><ID>{sm_id}</ID></RESPONSE>'

    def create_semantic_model_json(self, payload):
        sm_id = self._next_id("sm")
        return json.dumps({"entityId": sm_id})

    # ── Validation operations ──
    def validate_dataset(self, dataset_id, dataset_name, folder_label):
        return {"valid": True, "errors": [], "warnings": []}

    def validate_dataset_relationship(self, drd_id, drd_name, folder_label):
        return {"valid": True, "errors": [], "warnings": []}

    def validate_semantic_model(self, sm_id, sm_name, folder_label, validate_dependent=True):
        return {"valid": True, "errors": [], "warnings": []}


# ── Main validation ─────────────────────────────────────────────────────────


def run_validation(env_file: str, xmla_path: str | None = None, live: bool = False) -> bool:
    """Run the skill flow exactly as deploy-from-xmla.md defines it.

    Returns True on success, False on failure.
    """
    failures: list[str] = []

    # ── Locate XMLA file ────────────────────────────────────────────────────
    candidate_paths = [xmla_path] + [str(p) for p in DEFAULT_XMLA_PATHS]
    xmla_file = None
    for p in candidate_paths:
        if p and Path(p).exists():
            xmla_file = Path(p)
            break

    if not xmla_file:
        _fail(f"AdventureWorks.xmla not found. Searched: {candidate_paths}")
        return False

    _info(f"XMLA file: {xmla_file}")
    _info(f"Mode: {'LIVE' if live else 'MOCK'}")

    # ═══════════════════════════════════════════════════════════════════════
    # Step 1: Load config  (skill Step 1 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(1, "Load config")
    try:
        from kyvos_sdk.config import KyvosConfig
        config = KyvosConfig.from_env_file(env_file)
        _ok(f"Config loaded from {env_file}")
    except Exception as exc:
        _fail(f"Config load failed: {exc}")
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 2: Parse XMLA + derive names  (skill Step 2 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(2, "Parse XMLA + derive names")
    try:
        from datetime import datetime
        from kyvos_xmla_parser.xmla_parser import parse_xmla

        xmla_file_path = str(xmla_file)
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
        _ok("XMLA parsed and names derived")
    except Exception as exc:
        _fail(f"XMLA parse failed: {exc}")
        import traceback
        traceback.print_exc()
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Initialize Kyvos client  (skill Step 3 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(3, "Initialize Kyvos client")
    try:
        from kyvos_sdk.client import KyvosService
        from kyvos_sdk.provisioning import ProvisioningClient
        from kyvos_sdk.contracts.identity import FolderType

        if live:
            svc = KyvosService(config=config)
            svc.initialize()
        else:
            svc = MockKyvosService()
        prov = ProvisioningClient(svc)
        _ok("Kyvos client initialized")
    except Exception as exc:
        _fail(f"Client init failed: {exc}")
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 4: Create folders  (skill Step 4 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(4, "Create folders")
    try:
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
        _ok("All 3 folders created")
    except Exception as exc:
        _fail(f"Folder creation failed: {exc}")
        failures.append("Step 4: Folder creation")
        _print_summary(failures)
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 5: Create connection  (skill Step 5 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(5, "Create connection")
    try:
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
        _ok("Connection created")
    except Exception as exc:
        _fail(f"Connection creation failed: {exc}")
        failures.append("Step 5: Connection creation")
        _print_summary(failures)
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 6: Create datasets  (skill Step 6 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(6, "Create datasets")
    try:
        from kyvos_sm_skills.contract_adapter import compile_dataset_artifact

        dataset_name_to_id = {}   # CamelCase server name → dataset ID
        dataset_aliases = {}       # XMLA snake_case name → CamelCase server name
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

            server_name = ds_result.primary_entity_name  # CamelCase (Kyvos-assigned)
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

        # Second refresh sweep + validate all datasets (pipeline gate)
        validation_errors = []
        for ds_info in created_entities:
            if ds_info["entity_type"] != "DATASET":
                continue
            prov.refresh_dataset_columns(ds_info["id"])  # second sweep
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
        # Build a reverse map: server dataset name → spec TableSpec for fallback
        server_to_spec_table = {}
        for table in spec.tables:
            server_name = dataset_aliases.get(table.name, table.name)
            server_to_spec_table[server_name] = table
            server_to_spec_table[server_name.lower()] = table

        dataset_cols = {}  # CamelCase dataset name → list of column dicts
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
                # Fallback: use column metadata from the spec's TableSpec
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
        _ok(f"{len(dataset_name_to_id)} datasets created and validated")
    except Exception as exc:
        _fail(f"Dataset creation/validation failed: {exc}")
        import traceback
        traceback.print_exc()
        failures.append("Step 6: Dataset creation")
        _print_summary(failures)
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 7: Build DRD graph + create DRD  (skill Step 7 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(7, "Build DRD graph + create DRD")
    try:
        from kyvos_sm_skills.contract_adapter import compile_drd_artifact

        # ── Validate relationship columns before DRD creation ──────────────────────
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

        # Detect fact datasets from spec.tables (table_type == "fact")
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

        # Validate DRD (pipeline gate) — retry up to 3 times with 5s delay
        import time as _time
        _max_validation_retries = 3
        _validation_delay = 5  # seconds between retries
        for _attempt in range(1, _max_validation_retries + 1):
            drd_val_result = prov.validate_drd(server_drd_id, drd_name, drd_folder_label)
            if drd_val_result.succeeded:
                break
            if _attempt < _max_validation_retries:
                print(f"  DRD validation pending (attempt {_attempt}/{_max_validation_retries}), retrying in {_validation_delay}s...")
                _time.sleep(_validation_delay)
            else:
                errs = [d.message for d in drd_val_result.diagnostics if d.severity == "ERROR"]
                raise RuntimeError(f"DRD validation failed — pipeline halted: {errs}")

        print(f"DRD: {drd_name} (id={server_drd_id}) — validated")
        _ok("DRD created and validated")
    except Exception as exc:
        _fail(f"DRD creation/validation failed: {exc}")
        import traceback
        traceback.print_exc()
        failures.append("Step 7: DRD creation")
        _print_summary(failures)
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 8: Compile + create semantic model  (skill Step 8 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(8, "Compile + create semantic model")
    try:
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

        # Post-compilation assertion: check for NO_MEASURES_PLACED diagnostic
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
                _time.sleep(_validation_delay)
            else:
                errs = [d.message for d in sm_val_result.diagnostics if d.severity == "ERROR"]
                raise RuntimeError(f"Semantic model validation failed: {errs}")

        print(f"Semantic Model: {smodel_name} (id={smodel_id}) — validated")
        _ok("Semantic model created and validated")
    except Exception as exc:
        _fail(f"SM creation/validation failed: {exc}")
        import traceback
        traceback.print_exc()
        failures.append("Step 8: SM creation")
        _print_summary(failures)
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Step 9: Report results  (skill Step 9 — verbatim)
    # ═══════════════════════════════════════════════════════════════════════
    _print_step(9, "Report results")
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

    _print_summary(failures)
    return True


def _print_summary(failures: list[str]) -> None:
    print(f"\n{'═' * 70}")
    if failures:
        print(f"  ❌ Validation FAILED — {len(failures)} step(s) failed:")
        for f in failures:
            print(f"     - {f}")
    else:
        print("  ✅ Skill flow validation PASSED — all steps completed successfully")
    print(f"{'═' * 70}")


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Skill flow validation using AdventureWorks XMLA (mirrors deploy-from-xmla.md exactly)",
    )
    parser.add_argument(
        "--env-file", default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--xmla-path", default=None,
        help="Path to AdventureWorks.xmla (auto-detected if omitted)",
    )
    parser.add_argument(
        "--live", action="store_true", default=False,
        help="Use live KyvosService (creates real entities on Kyvos server)",
    )
    args = parser.parse_args()

    # Try to find .env file
    env_candidates = [
        Path(args.env_file),
        Path(__file__).resolve().parents[1] / args.env_file,
        Path(__file__).resolve().parents[2] / "kyvos-sdk-python" / args.env_file,
    ]
    env_path = None
    for c in env_candidates:
        if c.exists():
            env_path = c
            break

    if not env_path:
        print(f"❌ .env file not found. Searched: {[str(c) for c in env_candidates]}")
        return 1

    print(f"Using .env: {env_path}")

    if args.live:
        print("⚠️  LIVE MODE: This will create real entities on your Kyvos server!")

    success = run_validation(
        env_file=str(env_path),
        xmla_path=args.xmla_path,
        live=args.live,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
