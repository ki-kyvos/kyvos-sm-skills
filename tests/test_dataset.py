"""Tests for dataset generators (JSON + XML)."""

import pytest

from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.generators.dataset_xml import DatasetXmlGenerator
from kyvos_sm_skills.models import ColumnSpec, TableSpec


def _sample_fact_table():
    return TableSpec(
        name="fact_sales",
        schema_name="public",
        table_type="fact",
        columns=[
            ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="product_fk", data_type="INTEGER", is_foreign_key=True),
            ColumnSpec(name="amount", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="sale_date", data_type="DATE"),
        ],
    )


def _sample_dim_table():
    return TableSpec(
        name="dim_product",
        schema_name="public",
        table_type="dimension",
        columns=[
            ColumnSpec(name="product_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="product_name", data_type="VARCHAR(200)"),
        ],
    )


class TestDatasetJsonGenerator:
    def test_basic_generation(self):
        gen = DatasetJsonGenerator(connection_name="MyConn")
        payload = gen.generate_json_payload(_sample_fact_table())
        assert payload["datasetName"] == "FactSales"
        assert payload["datasetDetails"]["connectionName"] == "MyConn"
        assert payload["datasetDetails"]["inputType"] == "SQL"

    def test_sql_query(self):
        gen = DatasetJsonGenerator(connection_name="Conn")
        payload = gen.generate_json_payload(_sample_fact_table())
        sql = payload["datasetDetails"]["sqlDetails"]["sql"]
        assert "SELECT * FROM public.fact_sales" in sql

    def test_empty_columns_raises(self):
        gen = DatasetJsonGenerator(connection_name="Conn")
        empty_table = TableSpec(name="empty", columns=[])
        with pytest.raises(ValueError, match="columns is empty"):
            gen.generate_json_payload(empty_table)

    def test_dimension_table(self):
        gen = DatasetJsonGenerator(connection_name="Conn")
        payload = gen.generate_json_payload(_sample_dim_table())
        assert payload["datasetName"] == "DimProduct"

    def test_custom_folder(self):
        gen = DatasetJsonGenerator(connection_name="Conn", category_name="MyCategory", folder_id="folder_123")
        payload = gen.generate_json_payload(_sample_fact_table())
        assert payload["folderName"] == "MyCategory"
        assert payload["folderId"] == "folder_123"

    def test_safe_sql_ident_simple(self):
        assert DatasetJsonGenerator._safe_sql_ident("simple_name") == "simple_name"

    def test_safe_sql_ident_complex(self):
        result = DatasetJsonGenerator._safe_sql_ident("My Table")
        assert result.startswith('"')
        assert result.endswith('"')

    def test_format_table_name(self):
        gen = DatasetJsonGenerator(connection_name="Conn")
        assert gen._format_table_name("fact_sales") == "FactSales"
        assert gen._format_table_name("dim_product_category") == "DimProductCategory"


class TestDatasetXmlGenerator:
    def test_basic_generation(self, tmp_path):
        gen = DatasetXmlGenerator(connection_name="MyConn")
        xml_path = gen.generate_dataset_xml(_sample_fact_table(), tmp_path)
        assert xml_path.exists()
        xml = xml_path.read_text()
        assert len(xml) > 0

    def test_xml_has_qo(self, tmp_path):
        gen = DatasetXmlGenerator(connection_name="Conn")
        xml_path = gen.generate_dataset_xml(_sample_fact_table(), tmp_path)
        xml = xml_path.read_text()
        assert "QO" in xml or "TRANSFORMATION" in xml
