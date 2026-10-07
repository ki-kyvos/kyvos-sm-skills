"""Tests for the AI-space step in ``_deploy_spec`` (skill_runner).

Runs the shared deployment pipeline with a fully mocked Kyvos SDK surface
(KyvosService / ProvisioningClient / InspectionClient) and stubbed artifact
compilers, asserting the AI space is created after the semantic model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

try:
    import kyvos_sdk  # noqa: F401

    _has_sdk = True
except ImportError:
    _has_sdk = False

_sdk_required = pytest.mark.skipif(not _has_sdk, reason="requires kyvos-sdk-python")

if _has_sdk:
    from kyvos_sdk.config import KyvosConfig
    from kyvos_sdk.contracts.common import (
        ContractMetadata,
        CorrelationContext,
        Diagnostic,
        Severity,
    )
    from kyvos_sdk.contracts.identity import EntityRef, EntityType, FolderType
    from kyvos_sdk.contracts.results import OperationKind, OperationResult, OperationStatus

from kyvos_sm_skills.models import (
    ColumnSpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)
from kyvos_sm_skills.skill_runner import _deploy_spec

BASE_NAME = "Test"


def _ok(entity_type, entity_id, name, kind=OperationKind.CREATE):
    return OperationResult(
        metadata=ContractMetadata(contract_version="1.0", producer="test"),
        status=OperationStatus.SUCCEEDED,
        operation_kind=kind,
        entity_refs=[EntityRef(entity_type=entity_type, id=entity_id, name=name)],
        correlation=CorrelationContext(correlation_id="test"),
    )


def _ok_empty(kind=OperationKind.INSPECTION):
    return OperationResult(
        metadata=ContractMetadata(contract_version="1.0", producer="test"),
        status=OperationStatus.SUCCEEDED,
        operation_kind=kind,
        entity_refs=[],
        correlation=CorrelationContext(correlation_id="test"),
    )


def _fake_artifact(kind: str, name: str) -> MagicMock:
    art = MagicMock()
    art.diagnostics = []
    art.payload = ""
    art._entity_kind = kind
    art._entity_name = name
    return art


def _compile_dataset_artifact(table, **kw):
    return _fake_artifact("DATASET", table.name)


def _compile_drd_artifact(**kw):
    return _fake_artifact("DRD", kw["drd_name"])


def _compile_smodel_artifact(smodel, **kw):
    return _fake_artifact("SEMANTIC_MODEL", smodel.name)


def _apply_artifact(artifact, **kw):
    kind = artifact._entity_kind
    name = artifact._entity_name
    etype = {
        "DATASET": EntityType.DATASET,
        "DRD": EntityType.DRD,
        "SEMANTIC_MODEL": EntityType.SEMANTIC_MODEL,
    }[kind]
    return _ok(etype, f"{kind.lower()}_id_{name}", name)


def _make_prov(*, ai_space_result: OperationResult | None = None) -> MagicMock:
    prov = MagicMock()
    prov.create_folder = MagicMock(
        side_effect=lambda name, ftype: _ok(EntityType.FOLDER, f"folder_id_{name}", name)
    )
    prov.create_connection = MagicMock(
        side_effect=lambda **kw: _ok(EntityType.CONNECTION, "conn_1", kw.get("name", "conn"))
    )
    prov.apply_artifact = MagicMock(side_effect=_apply_artifact)
    prov.refresh_dataset_columns = MagicMock(
        side_effect=lambda ds_id: _ok(EntityType.DATASET, ds_id, ds_id, kind=OperationKind.REFRESH)
    )
    prov.validate_dataset = MagicMock(
        side_effect=lambda *a, **kw: _ok_empty(OperationKind.VALIDATION)
    )
    prov.validate_drd = MagicMock(
        side_effect=lambda *a, **kw: _ok_empty(OperationKind.VALIDATION)
    )
    prov.validate_semantic_model = MagicMock(
        side_effect=lambda *a, **kw: _ok_empty(OperationKind.VALIDATION)
    )
    prov.get_dataset_column_details = MagicMock(return_value=[])
    prov.create_ai_space = MagicMock(
        return_value=ai_space_result
        or _ok(EntityType.AI_SPACE, "space_123", "unused")
    )
    return prov


def _make_insp() -> MagicMock:
    insp = MagicMock()
    insp.list_folders = MagicMock(side_effect=lambda ft: _ok_empty())
    insp.list_datasets_in_folder = MagicMock(side_effect=lambda fn: _ok_empty())
    insp.list_drds_in_folder = MagicMock(side_effect=lambda fn: _ok_empty())
    insp.list_smodels_in_folder = MagicMock(side_effect=lambda fn: _ok_empty())
    return insp


def _make_spec() -> tuple[list[TableSpec], SemanticModelSpec]:
    fact = TableSpec(
        name="fact_sales",
        table_type="fact",
        columns=[
            ColumnSpec(name="sale_id", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="prod_id", data_type="INTEGER", is_foreign_key=True),
            ColumnSpec(name="amount", data_type="NUMERIC(10,2)"),
        ],
    )
    dim = TableSpec(
        name="dim_product",
        table_type="dimension",
        columns=[
            ColumnSpec(name="prod_id", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="product_name", data_type="VARCHAR(100)"),
        ],
    )
    sm = SemanticModelSpec(
        name="TestSM",
        relationships=[
            RelationshipSpec(
                left_dataset="fact_sales",
                left_column="prod_id",
                right_dataset="dim_product",
                right_column="prod_id",
            )
        ],
        measures=[
            MeasureSpec(
                name="TotalAmount",
                expression="",
                source_dataset="fact_sales",
                source_column="amount",
                aggregation_type="sum",
            )
        ],
    )
    return [fact, dim], sm


def _run_deploy(prov: MagicMock, tmp_path, monkeypatch):
    monkeypatch.setenv("KYVOS_SNAPSHOT_DIR", str(tmp_path))
    config = KyvosConfig(
        base_url="http://test",
        username="u",
        password="p",
        warehouse_type="POSTGRES",
        warehouse_host="wh",
        warehouse_port=5432,
        warehouse_database="db",
        warehouse_username="wu",
        warehouse_password="wp",
        warehouse_connection_name="conn",
        payload_format="json",
    )
    tables, smodel = _make_spec()
    with (
        patch("kyvos_sdk.client.KyvosService", return_value=MagicMock()),
        patch("kyvos_sdk.provisioning.ProvisioningClient", return_value=prov),
        patch("kyvos_sdk.inspection.InspectionClient", return_value=_make_insp()),
        patch(
            "kyvos_sm_skills.contract_adapter.compile_dataset_artifact",
            side_effect=_compile_dataset_artifact,
        ),
        patch(
            "kyvos_sm_skills.contract_adapter.compile_drd_artifact",
            side_effect=_compile_drd_artifact,
        ),
        patch(
            "kyvos_sm_skills.contract_adapter.compile_smodel_artifact",
            side_effect=_compile_smodel_artifact,
        ),
    ):
        return _deploy_spec(
            tables=tables,
            semantic_model=smodel,
            metadata={},
            base_name=BASE_NAME,
            config=config,
            perform_cleanup=False,
            kyvos_connection_name="existing_conn",
        )


@_sdk_required
class TestDeploySpecAiSpace:
    def test_ai_space_created_after_smodel(self, tmp_path, monkeypatch):
        prov = _make_prov()
        result = _run_deploy(prov, tmp_path, monkeypatch)

        assert result["success"] is True
        assert result["ai_space_id"] == "space_123"
        assert result["ai_space_name"].endswith("_Space")
        assert result["ai_space_name"].startswith(result["smodel_name"])

        # Each dataset is refreshed once and validated once (not twice).
        assert prov.refresh_dataset_columns.call_count == 2
        assert prov.validate_dataset.call_count == 2

        prov.create_ai_space.assert_called_once()
        _, kwargs = prov.create_ai_space.call_args
        assert kwargs["folder_name"] == f"{BASE_NAME}_Space"
        assert kwargs["folder_id"] == f"folder_id_{BASE_NAME}_Space"
        assert kwargs["space_name"] == result["ai_space_name"]
        assert len(kwargs["semantic_models"]) == 1
        sm_entry = kwargs["semantic_models"][0]
        assert sm_entry["id"] == result["smodel_id"]
        assert sm_entry["name"] == result["smodel_name"]
        assert sm_entry["folder_name"] == f"{BASE_NAME}_SModel"

        ai_entries = [
            e for e in result["created_entities"] if e["entity_type"] == "AI_SPACE"
        ]
        assert len(ai_entries) == 1
        assert ai_entries[0]["id"] == "space_123"
        assert ai_entries[0]["name"] == result["ai_space_name"]

        space_folder_entries = [
            e
            for e in result["created_entities"]
            if e["entity_type"] == "FOLDER" and e["name"] == f"{BASE_NAME}_Space"
        ]
        assert len(space_folder_entries) == 1

        # SPACE folder created via FolderType.SPACE
        folder_calls = [c.args[1] for c in prov.create_folder.call_args_list]
        assert FolderType.SPACE in folder_calls

    def test_ai_space_failure_raises(self, tmp_path, monkeypatch):
        failed = OperationResult(
            metadata=ContractMetadata(contract_version="1.0", producer="test"),
            status=OperationStatus.FAILED,
            operation_kind=OperationKind.CREATE,
            diagnostics=[
                Diagnostic(code="PROVISIONING_ERROR", severity=Severity.ERROR, message="boom")
            ],
            correlation=CorrelationContext(correlation_id="test"),
        )
        prov = _make_prov(ai_space_result=failed)
        with pytest.raises(RuntimeError, match="AI space creation failed"):
            _run_deploy(prov, tmp_path, monkeypatch)

    def test_ai_space_created_before_smodel_validation(self, tmp_path, monkeypatch):
        """SM validation failure is a warning, not fatal — the AI space and
        all artifacts were already created, so the deploy still succeeds."""
        invalid = OperationResult(
            metadata=ContractMetadata(contract_version="1.0", producer="test"),
            status=OperationStatus.FAILED,
            operation_kind=OperationKind.VALIDATION,
            diagnostics=[
                Diagnostic(code="VALIDATION_ERROR", severity=Severity.ERROR, message="bad measure")
            ],
            correlation=CorrelationContext(correlation_id="test"),
        )
        prov = _make_prov()
        prov.validate_semantic_model = MagicMock(return_value=invalid)
        result = _run_deploy(prov, tmp_path, monkeypatch)
        prov.create_ai_space.assert_called_once()
        assert result["success"] is True
        assert result["smodel_validated"] is False
        assert result["smodel_validation_skipped"] is True
        assert result["smodel_validation_kind"] == "model"
        assert result["smodel_validation_errors"] == ["bad measure"]
        assert result["errors"] == []
        assert result["warnings"] == ["Semantic model validation: bad measure"]

    def test_smodel_validation_success(self, tmp_path, monkeypatch):
        """When validation succeeds, the result carries no warnings."""
        prov = _make_prov()
        result = _run_deploy(prov, tmp_path, monkeypatch)
        assert result["success"] is True
        assert result["smodel_validated"] is True
        assert result["smodel_validation_skipped"] is False
        assert result["smodel_validation_kind"] is None
        assert result["smodel_validation_errors"] == []
        assert result["errors"] == []
        assert result["warnings"] == []
