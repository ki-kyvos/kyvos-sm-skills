"""Tests for kyvos_sm_skills.spec_builder — converting LLM recommendations to typed specs."""

from __future__ import annotations

import pytest

from kyvos_sm_skills.spec_builder import (
    DiscoveredSpec,
    build_spec_from_recommendation,
)
from kyvos_sm_skills.models import (
    ColumnSpec,
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)


# ── Test fixtures ──────────────────────────────────────────────────────────


def _make_warehouse_tables() -> list[dict]:
    """Adventure Works-like schema for testing."""
    return [
        {
            "name": "fact_internet_sales",
            "schema": "public",
            "estimated_table_type": "fact",
            "outgoing_fk_count": 4,
            "incoming_fk_count": 0,
            "columns": [
                {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_product.product_key"},
                {"name": "customer_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_customer.customer_key"},
                {"name": "order_date_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_date.date_key"},
                {"name": "sales_territory_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_sales_territory.sales_territory_key"},
                {"name": "sales_amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "order_quantity", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "total_product_cost", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_product",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "product_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "category", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "subcategory", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_customer",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "customer_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "full_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_date",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "date_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "full_date", "data_type": "DATE", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "month", "data_type": "VARCHAR(20)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "quarter", "data_type": "VARCHAR(10)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "year", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_sales_territory",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "sales_territory_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "region", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
    ]


def _make_star_schema_rec() -> dict:
    """A star schema SM recommendation matching the warehouse tables."""
    return {
        "name": "AdventureWorksSales",
        "schema_type": "star",
        "rationale": "Standard star schema for sales analytics",
        "tables": ["fact_internet_sales", "dim_product", "dim_customer", "dim_date", "dim_sales_territory"],
        "relationships": [
            {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
            {"from_table": "fact_internet_sales", "from_column": "customer_key", "to_table": "dim_customer", "to_column": "customer_key"},
            {"from_table": "fact_internet_sales", "from_column": "order_date_key", "to_table": "dim_date", "to_column": "date_key"},
            {"from_table": "fact_internet_sales", "from_column": "sales_territory_key", "to_table": "dim_sales_territory", "to_column": "sales_territory_key"},
        ],
        "measures": [
            {"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"},
            {"name": "OrderQuantity", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"},
            {"name": "TotalProductCost", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"},
        ],
        "hierarchies": [
            {"name": "ProductCategory", "levels": ["product_key", "category", "subcategory"], "source_dataset": "dim_product"},
            {"name": "CalendarDate", "levels": ["date_key", "month", "quarter", "year"], "source_dataset": "dim_date"},
        ],
    }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestBuildSpecFromRecommendation:
    def test_basic_star_schema(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        assert isinstance(spec, DiscoveredSpec)
        assert len(spec.tables) == 5
        assert spec.semantic_model.name == "AdventureWorksSales"

    def test_table_filtering(self):
        """Only tables in the recommendation should be included."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["tables"] = ["fact_internet_sales", "dim_product"]  # Only 2 tables
        rec["relationships"] = [
            {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
        ]
        spec = build_spec_from_recommendation(rec, wh_tables)

        assert len(spec.tables) == 2
        table_names = {t.name for t in spec.tables}
        assert table_names == {"fact_internet_sales", "dim_product"}

    def test_table_types_mapped(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        fact_table = [t for t in spec.tables if t.name == "fact_internet_sales"][0]
        assert fact_table.table_type == "fact"

        dim_table = [t for t in spec.tables if t.name == "dim_product"][0]
        assert dim_table.table_type == "dimension"

    def test_column_metadata_preserved(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        fact_table = [t for t in spec.tables if t.name == "fact_internet_sales"][0]
        assert len(fact_table.columns) == 8

        pk_col = [c for c in fact_table.columns if c.name == "sales_key"][0]
        assert pk_col.is_primary_key is True

        fk_col = [c for c in fact_table.columns if c.name == "product_key"][0]
        assert fk_col.is_foreign_key is True
        assert fk_col.references == "dim_product.product_key"

    def test_relationships_built(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        assert len(spec.semantic_model.relationships) == 4
        rel = spec.semantic_model.relationships[0]
        assert isinstance(rel, RelationshipSpec)
        assert rel.left_dataset == "fact_internet_sales"
        assert rel.left_column == "product_key"
        assert rel.right_dataset == "dim_product"
        assert rel.right_column == "product_key"

    def test_measures_built(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        assert len(spec.semantic_model.measures) == 3
        measure = spec.semantic_model.measures[0]
        assert isinstance(measure, MeasureSpec)
        assert measure.name == "SalesAmount"
        assert measure.source_dataset == "fact_internet_sales"
        assert measure.aggregation_type == "sum"

    def test_measure_source_column_auto_detected(self):
        """When measure name matches a column in source_dataset, source_column is set."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        # Use a measure name that matches a column name exactly (case-insensitive)
        rec["measures"] = [
            {"name": "order_quantity", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"},
        ]
        spec = build_spec_from_recommendation(rec, wh_tables)

        measure = spec.semantic_model.measures[0]
        assert measure.source_column == "order_quantity"

    def test_hierarchies_built(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        assert len(spec.semantic_model.hierarchies) == 2
        h = spec.semantic_model.hierarchies[0]
        assert isinstance(h, HierarchySpec)
        assert h.name == "ProductCategory"
        assert h.levels == ["product_key", "category", "subcategory"]
        assert h.source_dataset == "dim_product"

    def test_metadata_populated(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)

        assert spec.metadata["schema_type"] == "star"
        assert spec.metadata["rationale"] == "Standard star schema for sales analytics"
        assert spec.metadata["source"] == "warehouse_discovery"

    def test_missing_table_raises_error(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["tables"] = ["fact_internet_sales", "nonexistent_table"]
        with pytest.raises(ValueError, match="not found in warehouse schema"):
            build_spec_from_recommendation(rec, wh_tables)

    def test_invalid_relationship_table_raises(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["relationships"] = [
            {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "nonexistent", "to_column": "id"},
        ]
        with pytest.raises(ValueError, match="unknown table.*nonexistent"):
            build_spec_from_recommendation(rec, wh_tables)

    def test_invalid_relationship_column_raises(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["relationships"] = [
            {"from_table": "fact_internet_sales", "from_column": "nonexistent_col", "to_table": "dim_product", "to_column": "product_key"},
        ]
        with pytest.raises(ValueError, match="not found in table"):
            build_spec_from_recommendation(rec, wh_tables)

    def test_invalid_measure_source_raises(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["measures"] = [
            {"name": "TestMeasure", "source_dataset": "nonexistent_table", "aggregation_type": "sum"},
        ]
        with pytest.raises(ValueError, match="unknown source_dataset"):
            build_spec_from_recommendation(rec, wh_tables)

    def test_empty_measures_handled(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["measures"] = []
        spec = build_spec_from_recommendation(rec, wh_tables)
        assert spec.semantic_model.measures == []

    def test_empty_hierarchies_handled(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["hierarchies"] = []
        spec = build_spec_from_recommendation(rec, wh_tables)
        assert spec.semantic_model.hierarchies == []

    def test_empty_relationships_handled(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["relationships"] = []
        spec = build_spec_from_recommendation(rec, wh_tables)
        assert spec.semantic_model.relationships == []

    def test_case_insensitive_table_matching(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["tables"] = ["FACT_Internet_Sales", "DIM_Product"]
        rec["relationships"] = [
            {"from_table": "FACT_Internet_Sales", "from_column": "product_key", "to_table": "DIM_Product", "to_column": "product_key"},
        ]
        rec["measures"] = [
            {"name": "SalesAmount", "source_dataset": "FACT_Internet_Sales", "aggregation_type": "sum"},
        ]
        rec["hierarchies"] = [
            {"name": "Test", "levels": ["product_key", "category"], "source_dataset": "DIM_Product"},
        ]
        spec = build_spec_from_recommendation(rec, wh_tables)
        assert len(spec.tables) == 2
        # Table names should preserve original warehouse casing
        assert {t.name for t in spec.tables} == {"fact_internet_sales", "dim_product"}

    def test_unknown_table_type_defaults_to_dimension(self):
        wh_tables = [
            {
                "name": "mystery_table",
                "schema": "public",
                "estimated_table_type": "unknown",
                "outgoing_fk_count": 0,
                "incoming_fk_count": 0,
                "columns": [{"name": "id", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""}],
            },
        ]
        rec = {
            "name": "TestSM",
            "schema_type": "single_table",
            "tables": ["mystery_table"],
            "relationships": [],
            "measures": [],
            "hierarchies": [],
        }
        spec = build_spec_from_recommendation(rec, wh_tables)
        assert spec.tables[0].table_type == "dimension"

    def test_hierarchy_missing_name_raises(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["hierarchies"] = [{"levels": ["a", "b"], "source_dataset": "dim_product"}]
        with pytest.raises(ValueError, match="missing 'name'"):
            build_spec_from_recommendation(rec, wh_tables)

    def test_hierarchy_no_levels_skipped(self):
        """Non-parent-child hierarchy with no levels should be skipped, not raise."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["hierarchies"] = [{"name": "EmptyH", "levels": [], "source_dataset": "dim_product"}]
        spec = build_spec_from_recommendation(rec, wh_tables)
        # Hierarchy with no levels is skipped — no hierarchies in the result
        assert len(spec.semantic_model.hierarchies) == 0
