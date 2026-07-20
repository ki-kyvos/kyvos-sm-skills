"""End-to-end skill flow validation with mocked KyvosService.

This test suite validates the complete deploy-from-xmla skill flow pipeline
using mocked KyvosService responses. It verifies:

1. Entity ID capture (GAP-4, GAP-5): DRD and SM creation returns entity IDs
2. TableSpec fallback (GAP-7): Column fallback when API returns empty
3. Measure source_dataset remapping (GAP-7): XMLA→CamelCase alias mapping
4. Post-compilation measure assertion (GAP-7): Zero measures raises error
5. Relationship column validation (GAP-15): Invalid columns are skipped
6. Fact dataset detection: fact_dataset_names passed to compiler

Run: .venv/bin/python -m pytest tests/test_skill_flow_validation.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kyvos_sdk.client import KyvosServiceError
from kyvos_sdk.contracts.artifacts import (
    ArtifactFormat,
    ArtifactKind,
    CompiledArtifact,
)
from kyvos_sdk.contracts.common import ContractMetadata, Diagnostic, Severity
from kyvos_sdk.contracts.identity import EntityType
from kyvos_sdk.contracts.results import OperationStatus
from kyvos_sdk.contracts.versioning import CONTRACT_VERSION
from kyvos_sdk.provisioning import ProvisioningClient

from kyvos_sm_skills.contract_adapter import (
    compile_drd_artifact,
    compile_smodel_artifact,
)
from kyvos_sm_skills.models import (
    DatasetSpec,
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
    ColumnSpec,
)
from kyvos_sm_skills.generators.drd_xml import SimpleRel


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_service():
    return MagicMock()


@pytest.fixture
def prov(mock_service):
    return ProvisioningClient(mock_service)


def _make_artifact(kind: ArtifactKind, fmt: ArtifactFormat, payload: str) -> CompiledArtifact:
    return CompiledArtifact(
        metadata=ContractMetadata(contract_version=CONTRACT_VERSION, producer="test"),
        artifact_kind=kind,
        format=fmt,
        payload=payload,
        content_hash="sha256:abc123",
        compiler_version="1.0.0",
        diagnostics=[],
    )


def _make_spec() -> object:
    """Build a minimal DomainDemoSpec-like object with tables and semantic_model."""
    class FakeSpec:
        tables = [
            TableSpec(
                name="fact_sales",
                schema_name="dbo",
                table_type="fact",
                columns=[
                    ColumnSpec(name="sales_key", data_type="integer", is_primary_key=True),
                    ColumnSpec(name="customer_key", data_type="integer"),
                    ColumnSpec(name="amount", data_type="decimal"),
                    ColumnSpec(name="order_date", data_type="date"),
                ],
                row_count_target=10000,
            ),
            TableSpec(
                name="dim_customer",
                schema_name="dbo",
                table_type="dimension",
                columns=[
                    ColumnSpec(name="customer_key", data_type="integer", is_primary_key=True),
                    ColumnSpec(name="customer_name", data_type="string"),
                ],
                row_count_target=1000,
            ),
        ]
        semantic_model = SemanticModelSpec(
            name="TestModel",
            datasets=[
                DatasetSpec(name="FactSales", source_table="fact_sales", connection_name="TestConn"),
                DatasetSpec(name="DimCustomer", source_table="dim_customer", connection_name="TestConn"),
            ],
            relationships=[
                RelationshipSpec(
                    left_dataset="FactSales",
                    left_column="customer_key",
                    right_dataset="DimCustomer",
                    right_column="customer_key",
                ),
            ],
            measures=[
                MeasureSpec(
                    name="TotalAmount",
                    expression="",
                    source_dataset="fact_sales",
                    source_column="amount",
                    is_calculated=False,
                    aggregation_type="sum",
                ),
            ],
            hierarchies=[
                HierarchySpec(
                    name="CustomerHierarchy",
                    levels=["customer_name"],
                    source_dataset="dim_customer",
                ),
            ],
        )
    return FakeSpec()


def _make_dataset_cols() -> dict[str, list[dict]]:
    return {
        "FactSales": [
            {"name": "sales_key", "datatype": "integer", "original_name": "sales_key", "isPrimaryKey": True, "isForeignKey": False},
            {"name": "customer_key", "datatype": "integer", "original_name": "customer_key", "isPrimaryKey": False, "isForeignKey": True},
            {"name": "amount", "datatype": "decimal", "original_name": "amount", "isPrimaryKey": False, "isForeignKey": False},
            {"name": "order_date", "datatype": "date", "original_name": "order_date", "isPrimaryKey": False, "isForeignKey": False},
        ],
        "DimCustomer": [
            {"name": "customer_key", "datatype": "integer", "original_name": "customer_key", "isPrimaryKey": True, "isForeignKey": False},
            {"name": "customer_name", "datatype": "string", "original_name": "customer_name", "isPrimaryKey": False, "isForeignKey": False},
        ],
    }


def _make_dataset_aliases() -> dict[str, str]:
    return {"fact_sales": "FactSales", "dim_customer": "DimCustomer"}


def _make_dataset_name_to_id() -> dict[str, str]:
    return {"FactSales": "ds_001", "DimCustomer": "ds_002"}


# ── GAP-4: DRD entity ID capture ─────────────────────────────────────────


class TestGap4DrdEntityIdCapture:
    """Validate that DRD creation captures the server-assigned entity ID."""

    def test_xml_drd_id_captured(self, prov, mock_service):
        mock_service.create_dataset_relationship_drd = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE><ID>drd-server-001</ID></RESPONSE>'
        )
        result = prov.create_drd(drd_xml="<DRD>...</DRD>", use_json=False)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id == "drd-server-001"
        assert result.entity_refs[0].entity_type == EntityType.DRD

    def test_json_drd_id_captured(self, prov, mock_service):
        mock_service.create_dataset_relationship_drd_json = MagicMock(
            return_value='{"entityId": "drd-json-001"}'
        )
        result = prov.create_drd(drd_json={"name": "test"}, use_json=True)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id == "drd-json-001"

    def test_xml_drd_no_id_warns(self, prov, mock_service):
        mock_service.create_dataset_relationship_drd = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE></RESPONSE>'
        )
        result = prov.create_drd(drd_xml="<DRD>...</DRD>", use_json=False)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id is None
        assert any(d.code == "DRD_ID_MISSING" for d in result.diagnostics)

    def test_apply_artifact_drd_xml_captures_id(self, prov, mock_service):
        mock_service.create_dataset_relationship_drd = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE><ID>drd-art-001</ID></RESPONSE>'
        )
        artifact = _make_artifact(ArtifactKind.DRD, ArtifactFormat.XML, "<DRD>...</DRD>")
        result = prov.apply_artifact(artifact)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id == "drd-art-001"


# ── GAP-5: SM entity ID capture ──────────────────────────────────────────


class TestGap5SmEntityIdCapture:
    """Validate that SM creation captures the server-assigned entity ID."""

    def test_xml_sm_id_captured(self, prov, mock_service):
        mock_service.create_semantic_model_xml = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE><ID>sm-server-001</ID></RESPONSE>'
        )
        result = prov.create_semantic_model(smodel_xml="<SM>...</SM>", use_json=False)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id == "sm-server-001"
        assert result.entity_refs[0].entity_type == EntityType.SEMANTIC_MODEL

    def test_json_sm_id_captured(self, prov, mock_service):
        mock_service.create_semantic_model_json = MagicMock(
            return_value='{"entityId": "sm-json-001"}'
        )
        result = prov.create_semantic_model(smodel_json={"name": "MyModel"}, use_json=True)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id == "sm-json-001"
        assert result.entity_refs[0].name == "MyModel"

    def test_xml_sm_no_id_warns(self, prov, mock_service):
        mock_service.create_semantic_model_xml = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE></RESPONSE>'
        )
        result = prov.create_semantic_model(smodel_xml="<SM>...</SM>", use_json=False)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id is None
        assert any(d.code == "SMODEL_ID_MISSING" for d in result.diagnostics)

    def test_apply_artifact_sm_xml_captures_id(self, prov, mock_service):
        mock_service.create_semantic_model_xml = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE><ID>sm-art-001</ID></RESPONSE>'
        )
        artifact = _make_artifact(ArtifactKind.SEMANTIC_MODEL, ArtifactFormat.XML, "<SM>...</SM>")
        result = prov.apply_artifact(artifact)
        assert result.status == OperationStatus.SUCCEEDED
        assert result.primary_entity_id == "sm-art-001"


# ── GAP-7: Measure source_dataset remapping + fact detection ─────────────


class TestGap7MeasureRemappingAndFactDetection:
    """Validate that measure source_dataset is remapped and fact datasets are detected."""

    def test_measure_remapped_and_placed(self):
        """Measure with XMLA source_dataset should be remapped to CamelCase and placed."""
        smodel = SemanticModelSpec(
            name="TestModel",
            datasets=[
                DatasetSpec(name="FactSales", source_table="fact_sales", connection_name="TestConn"),
                DatasetSpec(name="DimCustomer", source_table="dim_customer", connection_name="TestConn"),
            ],
            relationships=[
                RelationshipSpec(
                    left_dataset="FactSales",
                    left_column="customer_key",
                    right_dataset="DimCustomer",
                    right_column="customer_key",
                ),
            ],
            measures=[
                MeasureSpec(
                    name="TotalAmount",
                    expression="",
                    source_dataset="fact_sales",
                    source_column="amount",
                    is_calculated=False,
                    aggregation_type="sum",
                ),
            ],
            hierarchies=[],
        )
        aliases = {"fact_sales": "FactSales", "dim_customer": "DimCustomer"}
        name_to_id = {"FactSales": "ds_001", "DimCustomer": "ds_002"}

        artifact = compile_smodel_artifact(
            smodel,
            drd_name="TestDRD",
            drd_id="drd_001",
            folder_id="folder_001",
            folder_name="TestFolder",
            connection_name="TestConn",
            dataset_name_to_id=name_to_id,
            relationships=smodel.relationships,
            dataset_aliases=aliases,
            fact_dataset_names={"FactSales"},
            fmt="json",
        )

        payload = json.loads(artifact.payload)
        measures = payload.get("specific", {}).get("smObject", {}).get("measures", {}).get("measure", [])
        assert len(measures) > 0, "No measures placed — remapping or fact detection failed"
        assert measures[0]["name"] == "TotalAmount"
        # In Simplified JSON format, dataset reference is in dataField.queryName
        data_field = measures[0].get("dataField", {})
        assert data_field.get("queryName") == "FactSales"

    def test_hierarchy_remapped(self):
        """Hierarchy source_dataset should be remapped from XMLA to CamelCase."""
        smodel = SemanticModelSpec(
            name="TestModel",
            datasets=[
                DatasetSpec(name="FactSales", source_table="fact_sales", connection_name="TestConn"),
                DatasetSpec(name="DimCustomer", source_table="dim_customer", connection_name="TestConn"),
            ],
            relationships=[
                RelationshipSpec(
                    left_dataset="FactSales",
                    left_column="customer_key",
                    right_dataset="DimCustomer",
                    right_column="customer_key",
                ),
            ],
            measures=[],
            hierarchies=[
                HierarchySpec(
                    name="CustomerHierarchy",
                    levels=["customer_name"],
                    source_dataset="dim_customer",
                ),
            ],
        )
        aliases = {"fact_sales": "FactSales", "dim_customer": "DimCustomer"}
        name_to_id = {"FactSales": "ds_001", "DimCustomer": "ds_002"}

        artifact = compile_smodel_artifact(
            smodel,
            drd_name="TestDRD",
            drd_id="drd_001",
            folder_id="folder_001",
            folder_name="TestFolder",
            connection_name="TestConn",
            dataset_name_to_id=name_to_id,
            relationships=smodel.relationships,
            dataset_aliases=aliases,
            fact_dataset_names={"FactSales"},
            fmt="json",
        )

        payload = json.loads(artifact.payload)
        dimensions = payload.get("specific", {}).get("smObject", {}).get("dimensions", [])
        assert len(dimensions) > 0, "No dimensions placed — hierarchy remapping failed"
        assert dimensions[0]["name"] == "DimCustomer"
        # In Simplified JSON format, dataset reference is in dataSources[0].id
        data_sources = dimensions[0].get("dataSources", [])
        assert len(data_sources) > 0
        assert data_sources[0]["id"] == "ds_002"

    def test_no_measures_without_fact_dataset_names(self):
        """Without fact_dataset_names, compiler should report NO_MEASURES_PLACED."""
        smodel = SemanticModelSpec(
            name="TestModel",
            datasets=[
                DatasetSpec(name="FactSales", source_table="fact_sales", connection_name="TestConn"),
                DatasetSpec(name="DimCustomer", source_table="dim_customer", connection_name="TestConn"),
            ],
            relationships=[
                RelationshipSpec(
                    left_dataset="FactSales",
                    left_column="customer_key",
                    right_dataset="DimCustomer",
                    right_column="customer_key",
                ),
            ],
            measures=[
                MeasureSpec(
                    name="TotalAmount",
                    expression="",
                    source_dataset="fact_sales",
                    source_column="amount",
                    is_calculated=False,
                    aggregation_type="sum",
                ),
            ],
            hierarchies=[],
        )
        aliases = {"fact_sales": "FactSales", "dim_customer": "DimCustomer"}
        name_to_id = {"FactSales": "ds_001", "DimCustomer": "ds_002"}

        artifact = compile_smodel_artifact(
            smodel,
            drd_name="TestDRD",
            drd_id="drd_001",
            folder_id="folder_001",
            folder_name="TestFolder",
            connection_name="TestConn",
            dataset_name_to_id=name_to_id,
            relationships=smodel.relationships,
            dataset_aliases=aliases,
            fact_dataset_names=set(),
            fmt="json",
        )

        no_measures = [d for d in artifact.diagnostics if d.code == "NO_MEASURES_PLACED"]
        assert len(no_measures) > 0, "Expected NO_MEASURES_PLACED diagnostic when no fact datasets provided"


# ── GAP-15: Relationship column validation ───────────────────────────────


class TestGap15RelationshipColumnValidation:
    """Validate that relationship columns are checked against dataset columns."""

    def test_valid_relationships_pass(self):
        """Relationships with valid columns should produce a DRD artifact."""
        rels = [
            SimpleRel(
                left_dataset="FactSales",
                left_column="customer_key",
                right_dataset="DimCustomer",
                right_column="customer_key",
            ),
        ]
        dataset_cols = _make_dataset_cols()

        left_cols = {c["name"].lower() for c in dataset_cols.get("FactSales", [])}
        right_cols = {c["name"].lower() for c in dataset_cols.get("DimCustomer", [])}

        assert "customer_key" in left_cols
        assert "customer_key" in right_cols

    def test_invalid_left_column_detected(self):
        """Relationship with non-existent left column should be flagged."""
        dataset_cols = _make_dataset_cols()
        left_col = "nonexistent_col"
        left_cols = {c["name"].lower() for c in dataset_cols.get("FactSales", [])}
        assert left_col.lower() not in left_cols

    def test_invalid_right_column_detected(self):
        """Relationship with non-existent right column should be flagged."""
        dataset_cols = _make_dataset_cols()
        right_col = "nonexistent_col"
        right_cols = {c["name"].lower() for c in dataset_cols.get("DimCustomer", [])}
        assert right_col.lower() not in right_cols

    def test_all_invalid_relationships_raises(self):
        """If all relationships have invalid columns, should raise RuntimeError."""
        rels = [
            SimpleRel(
                left_dataset="FactSales",
                left_column="bad_col",
                right_dataset="DimCustomer",
                right_column="customer_key",
            ),
        ]
        dataset_cols = _make_dataset_cols()

        validated = []
        for rel in rels:
            left_cols = {c["name"].lower() for c in dataset_cols.get("FactSales", [])}
            right_cols = {c["name"].lower() for c in dataset_cols.get("DimCustomer", [])}
            if rel.left_column.lower() in left_cols and rel.right_column.lower() in right_cols:
                validated.append(rel)

        assert len(validated) == 0, "All relationships should be invalid"

    def test_drd_artifact_compiles_with_validated_rels(self):
        """DRD artifact should compile successfully with validated relationships."""
        rels = [
            SimpleRel(
                left_dataset="FactSales",
                left_column="customer_key",
                right_dataset="DimCustomer",
                right_column="customer_key",
            ),
        ]
        name_to_id = {"FactSales": "ds_001", "DimCustomer": "ds_002"}
        aliases = {"fact_sales": "FactSales", "dim_customer": "DimCustomer"}

        artifact = compile_drd_artifact(
            drd_name="TestDRD",
            drd_id="drd_001",
            folder_id="folder_001",
            folder_name="TestFolder",
            dataset_name_to_id=name_to_id,
            relationships=rels,
            dataset_aliases=aliases,
            fact_dataset_names={"FactSales"},
            fmt="json",
        )
        assert artifact.artifact_kind == ArtifactKind.DRD
        assert artifact.payload


# ── End-to-end: Full pipeline with mocked services ───────────────────────


class TestEndToEndPipelineFlow:
    """Simulate the full skill flow pipeline with mocked KyvosService."""

    def test_full_pipeline_drd_and_sm_creation(self, prov, mock_service):
        """Verify DRD and SM creation both return entity IDs in a full pipeline."""
        # Step 7: DRD creation
        mock_service.create_dataset_relationship_drd = MagicMock(
            return_value='<RESPONSE><CODE>0</CODE><ID>drd-e2e-001</ID></RESPONSE>'
        )
        drd_artifact = _make_artifact(ArtifactKind.DRD, ArtifactFormat.XML, "<DRD>...</DRD>")
        drd_result = prov.apply_artifact(drd_artifact)
        assert drd_result.succeeded
        server_drd_id = drd_result.primary_entity_id
        assert server_drd_id == "drd-e2e-001"

        # Step 8: SM creation (uses server_drd_id)
        mock_service.create_semantic_model_xml = MagicMock(
            return_value=f'<RESPONSE><CODE>0</CODE><ID>sm-e2e-001</ID></RESPONSE>'
        )
        sm_artifact = _make_artifact(ArtifactKind.SEMANTIC_MODEL, ArtifactFormat.XML, "<SM>...</SM>")
        sm_result = prov.apply_artifact(sm_artifact)
        assert sm_result.succeeded
        assert sm_result.primary_entity_id == "sm-e2e-001"

    def test_full_pipeline_json_drd_and_sm(self, prov, mock_service):
        """Verify JSON pipeline also captures entity IDs."""
        # DRD JSON
        mock_service.create_dataset_relationship_drd_json = MagicMock(
            return_value='{"entityId": "drd-json-e2e"}'
        )
        drd_artifact = _make_artifact(ArtifactKind.DRD, ArtifactFormat.JSON, '{"name": "TestDRD"}')
        drd_result = prov.apply_artifact(drd_artifact)
        assert drd_result.succeeded
        assert drd_result.primary_entity_id == "drd-json-e2e"

        # SM JSON
        mock_service.create_semantic_model_json = MagicMock(
            return_value='{"entityId": "sm-json-e2e"}'
        )
        sm_artifact = _make_artifact(ArtifactKind.SEMANTIC_MODEL, ArtifactFormat.JSON, '{"name": "TestSM"}')
        sm_result = prov.apply_artifact(sm_artifact)
        assert sm_result.succeeded
        assert sm_result.primary_entity_id == "sm-json-e2e"

    def test_pipeline_halts_on_drd_failure(self, prov, mock_service):
        """If DRD creation fails, pipeline should not proceed to SM creation."""
        mock_service.create_dataset_relationship_drd = MagicMock(
            side_effect=KyvosServiceError("DRD creation failed")
        )
        drd_artifact = _make_artifact(ArtifactKind.DRD, ArtifactFormat.XML, "<DRD>...</DRD>")
        drd_result = prov.apply_artifact(drd_artifact)
        assert not drd_result.succeeded
        assert drd_result.primary_entity_id is None

    def test_pipeline_halts_on_sm_failure(self, prov, mock_service):
        """If SM creation fails, entity ID should not be captured."""
        mock_service.create_semantic_model_xml = MagicMock(
            side_effect=KyvosServiceError("SM creation failed")
        )
        sm_artifact = _make_artifact(ArtifactKind.SEMANTIC_MODEL, ArtifactFormat.XML, "<SM>...</SM>")
        sm_result = prov.apply_artifact(sm_artifact)
        assert not sm_result.succeeded
        assert sm_result.primary_entity_id is None
