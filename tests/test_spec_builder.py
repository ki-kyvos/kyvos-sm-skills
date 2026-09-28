"""Tests for kyvos_sm_skills.spec_builder — converting LLM recommendations to typed specs."""

from __future__ import annotations

import pytest

from kyvos_sm_skills.models import (
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
)
from kyvos_sm_skills.spec_builder import (
    DiscoveredSpec,
    build_spec_from_recommendation,
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
            {"name": "ProductCategory", "levels": ["category", "subcategory", "product_name"], "source_dataset": "dim_product"},
            {"name": "CalendarDate", "levels": ["quarter", "month"], "source_dataset": "dim_date"},
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
        assert h.levels == ["category", "subcategory", "product_name"]
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
        """Empty LLM measures get auto-created count measures for each fact table."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["measures"] = []
        spec = build_spec_from_recommendation(rec, wh_tables)
        # Auto-created count measure for the single fact table
        assert len(spec.semantic_model.measures) == 1
        assert spec.semantic_model.measures[0].aggregation_type == "count"
        assert spec.semantic_model.measures[0].source_dataset == "fact_internet_sales"

    def test_empty_hierarchies_handled(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["hierarchies"] = []
        spec = build_spec_from_recommendation(rec, wh_tables)
        assert spec.semantic_model.hierarchies == []

    def test_empty_relationships_auto_detected_from_fk(self):
        """When LLM provides no relationships, auto-detect from warehouse FK metadata."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["relationships"] = []
        spec = build_spec_from_recommendation(rec, wh_tables)
        # Auto-detection should create relationships from FK columns
        assert len(spec.semantic_model.relationships) > 0
        # All relationships should connect to fact tables
        rel_tables = set()
        for rel in spec.semantic_model.relationships:
            rel_tables.add(rel.left_dataset.lower())
            rel_tables.add(rel.right_dataset.lower())
        assert "fact_internet_sales" in rel_tables

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
            {"name": "Test", "levels": ["category", "subcategory"], "source_dataset": "DIM_Product"},
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

    def test_bridge_relationship_direction_normalized(self):
        """LLM-generated bridge -> fact relationships are flipped to fact -> bridge.

        This keeps the bridge dimension reachable from the measure fact in the
        connectivity sweep, preventing SM validation errors like *"Dimension ...
        is invalid because it does not have valid relation with any measure"*.
        """
        wh_tables = [
            {
                "name": "fact_internet_sales",
                "schema": "public",
                "estimated_table_type": "fact",
                "columns": [
                    {"name": "sales_key", "data_type": "INTEGER"},
                    {"name": "sales_order_number", "data_type": "VARCHAR"},
                    {"name": "sales_amount", "data_type": "NUMERIC"},
                ],
            },
            {
                "name": "fact_internet_sales_reason",
                "schema": "public",
                "estimated_table_type": "bridge",
                "columns": [
                    {"name": "sales_order_number", "data_type": "VARCHAR"},
                    {"name": "sales_reason_key", "data_type": "INTEGER"},
                ],
            },
            {
                "name": "dim_sales_reason",
                "schema": "public",
                "estimated_table_type": "dimension",
                "columns": [
                    {"name": "sales_reason_key", "data_type": "INTEGER"},
                    {"name": "reason_name", "data_type": "VARCHAR"},
                ],
            },
        ]
        rec = {
            "name": "InternetSalesWithReasons",
            "schema_type": "star",
            "rationale": "Star schema with many-to-many bridge to sales reasons",
            "tables": ["fact_internet_sales", "fact_internet_sales_reason", "dim_sales_reason"],
            "relationships": [
                # LLM emits bridge -> fact (wrong direction)
                {
                    "from_table": "fact_internet_sales_reason",
                    "from_column": "sales_order_number",
                    "to_table": "fact_internet_sales",
                    "to_column": "sales_order_number",
                    "relationship_type": "many_to_many",
                },
                # bridge -> dimension (correct direction)
                {
                    "from_table": "fact_internet_sales_reason",
                    "from_column": "sales_reason_key",
                    "to_table": "dim_sales_reason",
                    "to_column": "sales_reason_key",
                    "relationship_type": "many_to_one",
                },
            ],
            "measures": [{"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"}],
            "hierarchies": [],
        }
        spec = build_spec_from_recommendation(rec, wh_tables)

        rel_pairs = {
            (rel.left_dataset.lower(), rel.right_dataset.lower())
            for rel in spec.semantic_model.relationships
        }
        # Fact -> bridge (normalized from bridge -> fact)
        assert ("fact_internet_sales", "fact_internet_sales_reason") in rel_pairs
        # Bridge -> dimension (preserved)
        assert ("fact_internet_sales_reason", "dim_sales_reason") in rel_pairs
        # The reverse/wrong direction must not survive
        assert ("fact_internet_sales_reason", "fact_internet_sales") not in rel_pairs

        # Bridge and its dimension must remain in the spec (reachable from fact)
        remaining = {t.name.lower() for t in spec.tables}
        assert "fact_internet_sales_reason" in remaining
        assert "dim_sales_reason" in remaining


class TestFactToFactRejection:
    """Fact-to-fact relationships should be silently dropped by the spec builder."""

    def test_fact_to_fact_relationship_dropped(self):
        wh_tables = [
            {
                "name": "fact_internet_sales",
                "schema": "dbo",
                "estimated_table_type": "fact",
                "columns": [
                    {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True,
                     "references": "dim_product.product_key"},
                    {"name": "amount", "data_type": "DECIMAL", "is_pk": False, "is_fk": False},
                ],
                "outgoing_fk_count": 1, "incoming_fk_count": 0,
            },
            {
                "name": "fact_reseller_sales",
                "schema": "dbo",
                "estimated_table_type": "fact",
                "columns": [
                    {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True,
                     "references": "dim_product.product_key"},
                    {"name": "amount", "data_type": "DECIMAL", "is_pk": False, "is_fk": False},
                ],
                "outgoing_fk_count": 1, "incoming_fk_count": 0,
            },
            {
                "name": "dim_product",
                "schema": "dbo",
                "estimated_table_type": "dimension",
                "columns": [
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                    {"name": "product_name", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
                ],
                "outgoing_fk_count": 0, "incoming_fk_count": 2,
            },
        ]
        rec = {
            "name": "MultiFact SM",
            "schema_type": "multifact",
            "tables": ["fact_internet_sales", "fact_reseller_sales", "dim_product"],
            "relationships": [
                {"from_table": "fact_internet_sales", "from_column": "product_key",
                 "to_table": "dim_product", "to_column": "product_key"},
                # This fact-to-fact join should be silently dropped
                {"from_table": "fact_internet_sales", "from_column": "sales_key",
                 "to_table": "fact_reseller_sales", "to_column": "sales_key"},
                {"from_table": "fact_reseller_sales", "from_column": "product_key",
                 "to_table": "dim_product", "to_column": "product_key"},
            ],
            "measures": [
                {"name": "Internet Amount", "source_dataset": "fact_internet_sales",
                 "aggregation_type": "sum", "source_column": "amount"},
                {"name": "Reseller Amount", "source_dataset": "fact_reseller_sales",
                 "aggregation_type": "sum", "source_column": "amount"},
            ],
            "hierarchies": [],
        }
        spec = build_spec_from_recommendation(rec, wh_tables)

        # Fact-to-fact join should be dropped
        rel_pairs = {
            (rel.left_dataset.lower(), rel.right_dataset.lower())
            for rel in spec.semantic_model.relationships
        }
        assert ("fact_internet_sales", "fact_reseller_sales") not in rel_pairs
        assert ("fact_reseller_sales", "fact_internet_sales") not in rel_pairs
        # Valid fact->dim relationships should remain
        assert ("fact_internet_sales", "dim_product") in rel_pairs
        assert ("fact_reseller_sales", "dim_product") in rel_pairs

    def test_llm_table_classifications_used(self):
        """LLM table_classifications should override warehouse estimated_table_type."""
        wh_tables = [
            {
                "name": "factinternetsalesreason",
                "schema": "dbo",
                "estimated_table_type": "fact",  # warehouse says fact
                "columns": [
                    {"name": "salesordernumber", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
                    {"name": "salesreasonkey", "data_type": "INTEGER", "is_pk": False, "is_fk": False},
                ],
                "outgoing_fk_count": 0, "incoming_fk_count": 0,
            },
            {
                "name": "factinternetsales",
                "schema": "dbo",
                "estimated_table_type": "fact",
                "columns": [
                    {"name": "salesordernumber", "data_type": "VARCHAR", "is_pk": True, "is_fk": False},
                    {"name": "salesamount", "data_type": "DECIMAL", "is_pk": False, "is_fk": False},
                ],
                "outgoing_fk_count": 0, "incoming_fk_count": 0,
            },
        ]
        rec = {
            "name": "Sales SM",
            "schema_type": "star",
            "tables": ["factinternetsales", "factinternetsalesreason"],
            "table_classifications": {
                "factinternetsales": "fact",
                "factinternetsalesreason": "bridge",  # LLM overrides to bridge
            },
            "relationships": [
                {"from_table": "factinternetsales", "from_column": "salesordernumber",
                 "to_table": "factinternetsalesreason", "to_column": "salesordernumber"},
            ],
            "measures": [
                {"name": "Sales Amount", "source_dataset": "factinternetsales",
                 "aggregation_type": "sum", "source_column": "salesamount"},
            ],
            "hierarchies": [],
        }
        spec = build_spec_from_recommendation(rec, wh_tables)

        # factinternetsalesreason should be classified as bridge, not fact
        type_map = {t.name.lower(): t.table_type for t in spec.tables}
        assert type_map["factinternetsalesreason"] == "bridge"
        assert type_map["factinternetsales"] == "fact"

        # The relationship should NOT be dropped (fact->bridge is valid)
        rel_pairs = {
            (rel.left_dataset.lower(), rel.right_dataset.lower())
            for rel in spec.semantic_model.relationships
        }
        assert ("factinternetsales", "factinternetsalesreason") in rel_pairs


class TestMergeSpecsForReview:
    """Tests for merging multiple per-SM specs into one for review display."""

    def _make_two_sm_warehouse(self):
        """Warehouse with tables for two separate SMs."""
        return _make_warehouse_tables() + [
            {
                "name": "fact_inventory",
                "schema": "public",
                "estimated_table_type": "fact",
                "outgoing_fk_count": 2,
                "incoming_fk_count": 0,
                "columns": [
                    {"name": "inventory_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_product.product_key"},
                    {"name": "date_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_date.date_key"},
                    {"name": "units_in", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
        ]

    def test_merges_tables_from_multiple_sms(self):
        """All tables from all SMs should appear in the merged spec."""
        wh = self._make_two_sm_warehouse()

        sm1 = _make_star_schema_rec()
        sm2 = {
            "name": "Inventory",
            "schema_type": "star",
            "tables": ["fact_inventory", "dim_product", "dim_date"],
            "relationships": [
                {"from_table": "fact_inventory", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
                {"from_table": "fact_inventory", "from_column": "date_key", "to_table": "dim_date", "to_column": "date_key"},
            ],
            "measures": [
                {"name": "UnitsIn", "source_dataset": "fact_inventory", "aggregation_type": "sum", "source_column": "units_in"},
            ],
            "hierarchies": [],
        }

        from kyvos_sm_skills.spec_builder import merge_specs_for_review

        spec1 = build_spec_from_recommendation(sm1, wh)
        spec2 = build_spec_from_recommendation(sm2, wh)
        merged = merge_specs_for_review([(sm1, spec1), (sm2, spec2)])

        table_names = {t.name for t in merged.tables}
        # Should contain all tables from both SMs
        assert "fact_internet_sales" in table_names
        assert "fact_inventory" in table_names
        assert "dim_product" in table_names
        assert "dim_date" in table_names
        # Shared dims appear once
        assert len([t for t in merged.tables if t.name == "dim_product"]) == 1
        assert len([t for t in merged.tables if t.name == "dim_date"]) == 1
        # Measures from both SMs
        measure_names = {m.name for m in merged.semantic_model.measures}
        assert "SalesAmount" in measure_names
        assert "UnitsIn" in measure_names

    def test_dedup_shared_dimensions(self):
        """Shared dimensions appear once in the merged spec."""
        wh = self._make_two_sm_warehouse()
        sm1 = _make_star_schema_rec()
        sm2 = {
            "name": "Alt",
            "schema_type": "star",
            "tables": ["fact_inventory", "dim_product"],
            "relationships": [
                {"from_table": "fact_inventory", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
            ],
            "measures": [
                {"name": "X", "source_dataset": "fact_inventory", "aggregation_type": "sum", "source_column": "units_in"},
            ],
            "hierarchies": [],
        }

        from kyvos_sm_skills.spec_builder import merge_specs_for_review

        spec1 = build_spec_from_recommendation(sm1, wh)
        spec2 = build_spec_from_recommendation(sm2, wh)
        merged = merge_specs_for_review([(sm1, spec1), (sm2, spec2)])

        # dim_product appears once
        dp_entries = [t for t in merged.tables if t.name == "dim_product"]
        assert len(dp_entries) == 1
        assert dp_entries[0].table_type == "dimension"


class TestEnsurePkColumns:
    """Tests for auto-assigning PK to bridge tables with no PK."""

    def test_bridge_gets_composite_pk(self):
        """A bridge table with no PK gets FK columns marked as composite PK."""
        wh = _make_warehouse_tables() + [{
            "name": "bridge_reasons",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 2,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "order_id", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "fact_internet_sales.sales_key"},
                {"name": "reason_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_date.date_key"},
            ],
        }]
        rec = {
            "name": "WithBridge",
            "schema_type": "star",
            "tables": ["fact_internet_sales", "bridge_reasons", "dim_date"],
            "table_classifications": {"bridge_reasons": "bridge"},
            "relationships": [
                {"from_table": "fact_internet_sales", "from_column": "sales_key", "to_table": "bridge_reasons", "to_column": "order_id"},
                {"from_table": "bridge_reasons", "from_column": "reason_key", "to_table": "dim_date", "to_column": "date_key"},
            ],
            "measures": [
                {"name": "Sales", "source_dataset": "fact_internet_sales", "aggregation_type": "sum", "source_column": "sales_amount"},
            ],
            "hierarchies": [],
        }
        spec = build_spec_from_recommendation(rec, wh)
        bridge = [t for t in spec.tables if t.name == "bridge_reasons"][0]
        pk_cols = [c.name for c in bridge.columns if c.is_primary_key]
        assert len(pk_cols) >= 1  # At least one column marked as PK
