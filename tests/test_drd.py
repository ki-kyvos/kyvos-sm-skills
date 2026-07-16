"""Tests for DRD generators (JSON + XML)."""

from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import DrdXmlGenerator, SimpleRel


def _sample_relationships():
    return [
        SimpleRel("fact_sales", "product_fk", "dim_product", "product_pk"),
        SimpleRel("fact_sales", "date_fk", "dim_date", "date_pk"),
    ]


def _sample_aliases():
    return {
        "fact_sales": "FactSales",
        "dim_product": "DimProduct",
        "dim_date": "DimDate",
    }


def _sample_dataset_ids():
    return {
        "FactSales": "ds_001",
        "DimProduct": "ds_002",
        "DimDate": "ds_003",
    }


class TestSimpleRel:
    def test_construction(self):
        rel = SimpleRel("fact_sales", "product_fk", "dim_product", "product_pk")
        assert rel.left_dataset == "fact_sales"
        assert rel.left_column == "product_fk"
        assert rel.right_dataset == "dim_product"
        assert rel.right_column == "product_pk"


class TestDrdJsonGenerator:
    def test_basic_generation(self):
        gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
        payload = gen.generate(
            drd_name="SalesDRD",
            dataset_name_to_id=_sample_dataset_ids(),
            relationships=_sample_relationships(),
            dataset_aliases=_sample_aliases(),
            fact_dataset_names={"FactSales"},
        )
        assert payload is not None
        assert isinstance(payload, dict)

    def test_drd_name(self):
        gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
        payload = gen.generate(
            drd_name="MyDRD",
            dataset_name_to_id=_sample_dataset_ids(),
            relationships=_sample_relationships(),
            dataset_aliases=_sample_aliases(),
            fact_dataset_names={"FactSales"},
        )
        assert "MyDRD" in str(payload)


class TestDrdXmlGenerator:
    def test_basic_generation(self):
        gen = DrdXmlGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
        xml = gen.generate(
            drd_name="SalesDRD",
            dataset_name_to_id=_sample_dataset_ids(),
            relationships=_sample_relationships(),
            dataset_aliases=_sample_aliases(),
            fact_dataset_names={"FactSales"},
        )
        assert isinstance(xml, str)
        assert len(xml) > 0

    def test_xml_has_nodes(self):
        gen = DrdXmlGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
        xml = gen.generate(
            drd_name="DRD",
            dataset_name_to_id=_sample_dataset_ids(),
            relationships=_sample_relationships(),
            dataset_aliases=_sample_aliases(),
            fact_dataset_names={"FactSales"},
        )
        assert "NODE" in xml or "node" in xml.lower()
