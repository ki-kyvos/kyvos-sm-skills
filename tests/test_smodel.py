"""Tests for semantic model generators (JSON + XML)."""

from kyvos_sm_skills.generators.drd_xml import DrdXmlGenerator, SimpleRel
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.generators.smodel_xml import SModelXmlGenerator
from kyvos_sm_skills.models import HierarchySpec, MeasureSpec


def _drd_xml():
    gen = DrdXmlGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
    return gen.generate(
        drd_name="SalesDRD",
        dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002", "DimDate": "ds_003"},
        relationships=[
            SimpleRel("fact_sales", "product_fk", "dim_product", "product_pk"),
            SimpleRel("fact_sales", "date_fk", "dim_date", "date_pk"),
        ],
        dataset_aliases={"fact_sales": "FactSales", "dim_product": "DimProduct", "dim_date": "DimDate"},
        fact_dataset_names={"FactSales"},
    )


def _dataset_columns():
    return {
        "FactSales": [
            {"name": "sales_pk", "datatype": "NUMBER"},
            {"name": "product_fk", "datatype": "NUMBER"},
            {"name": "date_fk", "datatype": "NUMBER"},
            {"name": "sales_amount", "datatype": "NUMBER"},
            {"name": "quantity", "datatype": "NUMBER"},
        ],
        "DimProduct": [
            {"name": "product_pk", "datatype": "NUMBER"},
            {"name": "product_name", "datatype": "CHAR"},
            {"name": "category", "datatype": "CHAR"},
        ],
        "DimDate": [
            {"name": "date_pk", "datatype": "NUMBER"},
            {"name": "year", "datatype": "CHAR"},
            {"name": "quarter", "datatype": "CHAR"},
            {"name": "month", "datatype": "CHAR"},
        ],
    }


def _measures():
    return [
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="sales_amount", aggregation_type="sum"),
        MeasureSpec(name="Total Quantity", expression="", source_dataset="FactSales", source_column="quantity", aggregation_type="sum"),
        MeasureSpec(name="Avg Order Value", expression="[Measures].[Total Sales] / [Measures].[Total Quantity]", is_calculated=True, source_dataset="FactSales"),
    ]


def _hierarchies():
    return [
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Product Category", levels=["category"], source_dataset="DimProduct"),
    ]


def _common_kwargs():
    return dict(
        folder_id="folder_002",
        folder_name="SMs",
        smodel_name="SalesModel",
        connection_name="MyConn",
        drd_id="drd_001",
        drd_name="SalesDRD",
        drd_xml=_drd_xml(),
        dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002", "DimDate": "ds_003"},
        dataset_columns=_dataset_columns(),
        hierarchy_specs=_hierarchies(),
        semantic_measures=_measures(),
    )


class TestSModelJsonGenerator:
    def test_basic_generation(self):
        gen = SModelJsonGenerator(**_common_kwargs())
        payload = gen.generate()
        assert payload is not None
        assert isinstance(payload, dict)
        assert payload["name"] == "SalesModel"

    def test_has_attrs(self):
        gen = SModelJsonGenerator(**_common_kwargs())
        payload = gen.generate()
        assert "specific" in payload
        assert "attrs" in payload["specific"]
        assert payload["specific"]["attrs"]["drdName"] == "SalesDRD"

    def test_has_sm_object(self):
        gen = SModelJsonGenerator(**_common_kwargs())
        payload = gen.generate()
        assert "smObject" in payload["specific"]
        sm_obj = payload["specific"]["smObject"]
        assert "dimensions" in sm_obj
        assert "measures" in sm_obj
        assert "measureGroups" in sm_obj

    def test_measures_present(self):
        gen = SModelJsonGenerator(**_common_kwargs())
        payload = gen.generate()
        measures = payload["specific"]["smObject"]["measures"]["measure"]
        assert len(measures) >= 2

    def test_connection_name_in_attrs(self):
        gen = SModelJsonGenerator(**_common_kwargs())
        payload = gen.generate()
        assert payload["specific"]["attrs"]["rawDataConnectionName"] == "MyConn"


class TestSModelXmlGenerator:
    def test_basic_generation(self):
        gen = SModelXmlGenerator(**_common_kwargs())
        xml = gen.generate()
        assert isinstance(xml, str)
        assert len(xml) > 0

    def test_xml_has_cube(self):
        gen = SModelXmlGenerator(**_common_kwargs())
        xml = gen.generate()
        assert "CUBE" in xml or "cube" in xml.lower()

    def test_xml_has_measures(self):
        gen = SModelXmlGenerator(**_common_kwargs())
        xml = gen.generate()
        assert "MEASURE" in xml or "measure" in xml.lower()

    def test_xml_has_dimensions(self):
        gen = SModelXmlGenerator(**_common_kwargs())
        xml = gen.generate()
        assert "DIMENSION" in xml or "dimension" in xml.lower()
