"""Tests for megaplan phases: intent template, spec builder fixes, sm_diff utility."""

from __future__ import annotations

import os
import tempfile

import pytest

from kyvos_sm_skills.intent_generator import (
    _fill_template,
    generate_intent,
    generate_intent_from_file,
)
from kyvos_sm_skills.models import (
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)
from kyvos_sm_skills.sm_diff import compare_specs
from kyvos_sm_skills.spec_builder import (
    DiscoveredSpec,
    _connectivity_sweep,
    _map_table_type,
    build_spec_from_recommendation,
)

# ── Test fixtures ──────────────────────────────────────────────────────────


def _make_warehouse_tables() -> list[dict]:
    """AdventureWorks-like schema for testing."""
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
                {"name": "sales_amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "order_quantity", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
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
            "name": "dim_geography",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 0,
            "columns": [
                {"name": "geography_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "city", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
    ]


def _make_star_schema_rec() -> dict:
    return {
        "name": "TestSM",
        "schema_type": "star",
        "rationale": "Standard star schema",
        "tables": ["fact_internet_sales", "dim_product", "dim_customer", "dim_date"],
        "relationships": [
            {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
            {"from_table": "fact_internet_sales", "from_column": "customer_key", "to_table": "dim_customer", "to_column": "customer_key"},
            {"from_table": "fact_internet_sales", "from_column": "order_date_key", "to_table": "dim_date", "to_column": "date_key"},
        ],
        "measures": [
            {"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum", "source_column": "sales_amount"},
            {"name": "OrderQuantity", "source_dataset": "fact_internet_sales", "aggregation_type": "sum", "source_column": "order_quantity"},
        ],
        "hierarchies": [
            {"name": "ProductCategory", "levels": ["category", "subcategory"], "source_dataset": "dim_product"},
        ],
    }


def _make_schema_summary() -> dict:
    return {
        "warehouse_type": "postgresql",
        "schema": "public",
        "table_count": 3,
        "tables": [
            {
                "name": "fact_sales",
                "estimated_table_type": "fact",
                "columns": [
                    {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "sales_amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
            {
                "name": "dim_product",
                "estimated_table_type": "dimension",
                "columns": [
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "product_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
        ],
        "relationships": [],
        "detected_patterns": {},
    }


# ── Phase 1: Intent template tests ─────────────────────────────────────────


class TestFillTemplate:
    def test_fill_template_returns_string(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain="adventure_works")
        assert result is not None
        assert isinstance(result, str)

    def test_fill_template_replaces_domain(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain="retail_ecommerce")
        assert "Retail Ecommerce" in result

    def test_fill_template_default_domain(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain=None)
        assert "The Specified Domain" in result

    def test_fill_template_replaces_kpi_categories(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain="test")
        assert "{{kpi_categories}}" not in result
        assert "{{domain}}" not in result

    def test_fill_template_detects_amount_columns(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain="test")
        assert "Revenue" in result

    def test_fill_template_deployment_constraints(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain="test")
        assert "Deployment Constraints" in result
        assert "fact" in result.lower()

    def test_fill_template_no_remaining_placeholders(self):
        schema = _make_schema_summary()
        result = _fill_template(schema, domain="test")
        import re
        assert not re.search(r"\{\{[^}]+\}\}", result)


class TestGenerateIntentWithTemplate:
    def test_generate_intent_uses_template_by_default(self):
        schema = _make_schema_summary()
        result = generate_intent(schema, domain="test_domain")
        assert "Deployment Constraints" in result
        assert "Test Domain" in result

    def test_generate_intent_template_no_api_key_needed(self):
        """Template path should not require any API key."""
        schema = _make_schema_summary()
        result = generate_intent(schema, domain="test")
        assert isinstance(result, str)
        assert len(result) > 100

    def test_generate_intent_from_file_saves_template(self):
        schema = _make_schema_summary()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name
        try:
            result = generate_intent_from_file(path, schema, domain="test")
            assert os.path.exists(path)
            with open(path) as f:
                saved = f.read()
            assert saved == result
            assert "Deployment Constraints" in saved
        finally:
            os.unlink(path)


# ── Phase 2: Spec builder tests ────────────────────────────────────────────


class TestNonFactMeasureDrop:
    def test_measure_on_dimension_table_is_dropped(self):
        """Measures referencing dimension tables should be dropped."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["measures"] = [
            {"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum", "source_column": "sales_amount"},
            {"name": "ProductNameMeasure", "source_dataset": "dim_product", "aggregation_type": "sum", "source_column": "product_key"},
        ]
        spec = build_spec_from_recommendation(rec, wh_tables)
        measure_names = {m.name for m in spec.semantic_model.measures}
        assert "SalesAmount" in measure_names
        assert "ProductNameMeasure" not in measure_names

    def test_measure_on_fact_table_is_kept(self):
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        spec = build_spec_from_recommendation(rec, wh_tables)
        measure_names = {m.name for m in spec.semantic_model.measures}
        assert "SalesAmount" in measure_names
        assert "OrderQuantity" in measure_names

    def test_non_fact_table_not_auto_added_for_measure(self):
        """When a measure references a non-fact table not in table_specs,
        the table should NOT be auto-added."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        rec["measures"] = [
            {"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum", "source_column": "sales_amount"},
            {"name": "GeographyMeasure", "source_dataset": "dim_geography", "aggregation_type": "sum", "source_column": "geography_key"},
        ]
        rec["tables"].append("dim_geography")
        spec = build_spec_from_recommendation(rec, wh_tables)
        table_names = {t.name for t in spec.tables}
        # dim_geography is disconnected and should be removed by connectivity sweep
        assert "dim_geography" not in table_names


class TestConnectivitySweep:
    def test_disconnected_dimension_removed(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_product", schema_name="public", table_type="dimension", columns=[]),
            TableSpec(name="dim_orphan", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact_sales", left_column="pk", right_dataset="dim_product", right_column="fk", relationship_type="many_to_one"),
        ]
        measures = [MeasureSpec(name="m1", expression="col1", source_dataset="fact_sales", aggregation_type="sum")]
        result_tables, result_rels, result_measures = _connectivity_sweep(tables, rels, measures)
        table_names = {t.name for t in result_tables}
        assert "fact_sales" in table_names
        assert "dim_product" in table_names
        assert "dim_orphan" not in table_names

    def test_connected_dimension_kept(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_product", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact_sales", left_column="pk", right_dataset="dim_product", right_column="fk", relationship_type="many_to_one"),
        ]
        measures = []
        result_tables, _, _ = _connectivity_sweep(tables, rels, measures)
        assert len(result_tables) == 2

    def test_no_fact_tables_returns_unchanged(self):
        tables = [
            TableSpec(name="dim_a", schema_name="public", table_type="dimension", columns=[]),
            TableSpec(name="dim_b", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = []
        measures = []
        result_tables, _, _ = _connectivity_sweep(tables, rels, measures)
        assert len(result_tables) == 2

    def test_transitive_dimension_connectivity_is_pruned(self):
        """fact → dim_a → dim_b is not a valid Kyvos measure path."""
        tables = [
            TableSpec(name="fact", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_a", schema_name="public", table_type="dimension", columns=[]),
            TableSpec(name="dim_b", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact", left_column="pk", right_dataset="dim_a", right_column="fk", relationship_type="many_to_one"),
            RelationshipSpec(left_dataset="dim_a", left_column="pk", right_dataset="dim_b", right_column="fk", relationship_type="many_to_one"),
        ]
        measures = []
        result_tables, _, _ = _connectivity_sweep(tables, rels, measures)
        assert {table.name for table in result_tables} == {"fact", "dim_a"}

    def test_measures_on_removed_table_are_dropped(self):
        tables = [
            TableSpec(name="fact", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_orphan", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = []
        measures = [
            MeasureSpec(name="m1", expression="col", source_dataset="fact", aggregation_type="sum"),
            MeasureSpec(name="m2", expression="col", source_dataset="dim_orphan", aggregation_type="sum"),
        ]
        _, _, result_measures = _connectivity_sweep(tables, rels, measures)
        measure_names = {m.name for m in result_measures}
        assert "m1" in measure_names
        assert "m2" not in measure_names

    def test_dimension_connected_only_to_measureless_fact_is_pruned(self):
        """A dimension connected to a fact table with no measures should be pruned."""
        tables = [
            TableSpec(name="fact_with_measures", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="fact_no_measures", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_connected_to_measureless", schema_name="public", table_type="dimension", columns=[]),
            TableSpec(name="dim_connected_to_measures", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact_with_measures", left_column="pk",
                             right_dataset="dim_connected_to_measures", right_column="fk",
                             relationship_type="many_to_one"),
            RelationshipSpec(left_dataset="fact_no_measures", left_column="pk",
                             right_dataset="dim_connected_to_measureless", right_column="fk",
                             relationship_type="many_to_one"),
        ]
        measures = [
            MeasureSpec(name="m1", expression="col", source_dataset="fact_with_measures", aggregation_type="sum"),
        ]
        result_tables, _, _ = _connectivity_sweep(tables, rels, measures)
        table_names = {t.name for t in result_tables}
        assert "fact_with_measures" in table_names
        assert "dim_connected_to_measures" in table_names
        assert "fact_no_measures" not in table_names
        assert "dim_connected_to_measureless" not in table_names

    def test_fall_back_to_all_facts_when_none_have_measures(self):
        """When no fact table has measures, fall back to all fact tables for BFS."""
        tables = [
            TableSpec(name="fact_a", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_a", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact_a", left_column="pk",
                             right_dataset="dim_a", right_column="fk",
                             relationship_type="many_to_one"),
        ]
        measures = []
        result_tables, _, _ = _connectivity_sweep(tables, rels, measures)
        table_names = {t.name for t in result_tables}
        assert "fact_a" in table_names
        assert "dim_a" in table_names

    def test_dimension_chain_is_not_a_valid_measure_path(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_employee", schema_name="public", table_type="dimension", columns=[]),
            TableSpec(name="sales_targets", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact_sales", left_column="employee_key",
                             right_dataset="dim_employee", right_column="employee_key",
                             relationship_type="many_to_one"),
            RelationshipSpec(left_dataset="dim_employee", left_column="employee_key",
                             right_dataset="sales_targets", right_column="employee_key",
                             relationship_type="many_to_one"),
        ]
        measures = [
            MeasureSpec(name="sales_amount", expression="amount", source_dataset="fact_sales", aggregation_type="sum"),
        ]
        result_tables, result_rels, _ = _connectivity_sweep(tables, rels, measures)
        assert {table.name for table in result_tables} == {"fact_sales", "dim_employee"}
        assert len(result_rels) == 1

    def test_bridge_chain_is_a_valid_measure_path(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="sales_reasons", schema_name="public", table_type="bridge", columns=[]),
            TableSpec(name="sales_reason", schema_name="public", table_type="dimension", columns=[]),
        ]
        rels = [
            RelationshipSpec(left_dataset="fact_sales", left_column="order_number",
                             right_dataset="sales_reasons", right_column="order_number",
                             relationship_type="many_to_one"),
            RelationshipSpec(left_dataset="sales_reasons", left_column="reason_key",
                             right_dataset="sales_reason", right_column="reason_key",
                             relationship_type="many_to_one"),
        ]
        measures = [
            MeasureSpec(name="sales_amount", expression="amount", source_dataset="fact_sales", aggregation_type="sum"),
        ]
        result_tables, _, _ = _connectivity_sweep(tables, rels, measures)
        assert {table.name for table in result_tables} == {"fact_sales", "sales_reasons", "sales_reason"}


class TestMapTableType:
    def test_known_fact(self):
        assert _map_table_type("fact") == "fact"

    def test_known_dimension(self):
        assert _map_table_type("dimension") == "dimension"

    def test_known_bridge(self):
        assert _map_table_type("bridge") == "bridge"

    def test_unknown_defaults_to_dimension(self):
        assert _map_table_type("unknown") == "dimension"

    def test_unknown_with_high_fk_count_classified_as_fact(self):
        wt = {"columns": [{"is_fk": True}, {"is_fk": True}, {"is_fk": True}, {"name": "x"}]}
        assert _map_table_type("unknown", wt) == "fact"

    def test_unknown_with_all_numeric_cols_classified_as_fact(self):
        wt = {"columns": [
            {"name": "c1", "data_type": "INTEGER"},
            {"name": "c2", "data_type": "NUMERIC(10,2)"},
            {"name": "c3", "data_type": "DECIMAL(5,2)"},
            {"name": "c4", "data_type": "BIGINT"},
            {"name": "c5", "data_type": "DOUBLE"},
        ]}
        assert _map_table_type("unknown", wt) == "fact"

    def test_unknown_with_mixed_cols_classified_as_dimension(self):
        wt = {"columns": [
            {"name": "c1", "data_type": "VARCHAR(100)"},
            {"name": "c2", "data_type": "INTEGER"},
        ]}
        assert _map_table_type("unknown", wt) == "dimension"

    def test_unknown_without_wt_defaults_to_dimension(self):
        assert _map_table_type("unknown", None) == "dimension"


class TestDimToDimGuard:
    def test_no_dim_to_dim_relationship_in_output(self):
        """The spec builder should not create dim→dim relationships."""
        wh_tables = _make_warehouse_tables()
        rec = _make_star_schema_rec()
        # Add dim_geography but don't connect it to any fact
        rec["tables"].append("dim_geography")
        spec = build_spec_from_recommendation(rec, wh_tables)
        for rel in spec.semantic_model.relationships:
            left_type = next((t.table_type for t in spec.tables if t.name.lower() == rel.left_dataset.lower()), "dimension")
            right_type = next((t.table_type for t in spec.tables if t.name.lower() == rel.right_dataset.lower()), "dimension")
            if left_type not in ("fact", "bridge") and right_type not in ("fact", "bridge"):
                pytest.fail(f"Dim→dim relationship found: {rel.left_dataset} → {rel.right_dataset}")


# ── Phase 4: sm_diff tests ─────────────────────────────────────────────────


class TestSmDiff:
    def test_identical_specs_are_compatible(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_product", schema_name="public", table_type="dimension", columns=[]),
        ]
        sm = SemanticModelSpec(
            name="test",
            relationships=[RelationshipSpec(left_dataset="fact_sales", left_column="pk", right_dataset="dim_product", right_column="fk", relationship_type="many_to_one")],
            measures=[],
            hierarchies=[],
        )
        spec_a = DiscoveredSpec(tables=tables, semantic_model=sm)
        spec_b = DiscoveredSpec(tables=tables, semantic_model=sm)
        result = compare_specs(spec_a, spec_b)
        assert result.is_compatible

    def test_different_table_sets_incompatible(self):
        tables_a = [TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[])]
        tables_b = [TableSpec(name="fact_orders", schema_name="public", table_type="fact", columns=[])]
        sm = SemanticModelSpec(name="test", relationships=[], measures=[], hierarchies=[])
        spec_a = DiscoveredSpec(tables=tables_a, semantic_model=sm)
        spec_b = DiscoveredSpec(tables=tables_b, semantic_model=sm)
        result = compare_specs(spec_a, spec_b)
        assert not result.is_compatible
        assert not result.table_set_match

    def test_table_type_mismatch_detected(self):
        tables_a = [TableSpec(name="t1", schema_name="public", table_type="fact", columns=[])]
        tables_b = [TableSpec(name="t1", schema_name="public", table_type="dimension", columns=[])]
        sm = SemanticModelSpec(name="test", relationships=[], measures=[], hierarchies=[])
        spec_a = DiscoveredSpec(tables=tables_a, semantic_model=sm)
        spec_b = DiscoveredSpec(tables=tables_b, semantic_model=sm)
        result = compare_specs(spec_a, spec_b)
        assert len(result.table_type_mismatches) == 1
        assert result.table_type_mismatches[0][0] == "t1"

    def test_dim_to_dim_detected(self):
        tables = [
            TableSpec(name="dim_a", schema_name="public", table_type="dimension", columns=[]),
            TableSpec(name="dim_b", schema_name="public", table_type="dimension", columns=[]),
        ]
        sm = SemanticModelSpec(
            name="test",
            relationships=[RelationshipSpec(left_dataset="dim_a", left_column="pk", right_dataset="dim_b", right_column="fk", relationship_type="many_to_one")],
            measures=[],
            hierarchies=[],
        )
        spec = DiscoveredSpec(tables=tables, semantic_model=sm)
        result = compare_specs(spec, spec)
        assert len(result.dim_to_dim_relationships) > 0

    def test_disconnected_dimension_detected(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_orphan", schema_name="public", table_type="dimension", columns=[]),
        ]
        sm = SemanticModelSpec(name="test", relationships=[], measures=[], hierarchies=[])
        spec = DiscoveredSpec(tables=tables, semantic_model=sm)
        result = compare_specs(spec, spec)
        assert len(result.disconnected_dimensions) > 0
        assert "dim_orphan" in result.disconnected_dimensions[0]

    def test_measure_on_non_fact_detected(self):
        tables = [
            TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[]),
            TableSpec(name="dim_product", schema_name="public", table_type="dimension", columns=[]),
        ]
        sm = SemanticModelSpec(
            name="test",
            relationships=[],
            measures=[MeasureSpec(name="bad_measure", expression="col", source_dataset="dim_product", aggregation_type="sum")],
            hierarchies=[],
        )
        spec = DiscoveredSpec(tables=tables, semantic_model=sm)
        result = compare_specs(spec, spec)
        assert len(result.measure_source_mismatches) > 0

    def test_summary_string(self):
        tables = [TableSpec(name="fact", schema_name="public", table_type="fact", columns=[])]
        sm = SemanticModelSpec(name="test", relationships=[], measures=[], hierarchies=[])
        spec = DiscoveredSpec(tables=tables, semantic_model=sm)
        result = compare_specs(spec, spec)
        summary = result.summary()
        assert "COMPATIBLE" in summary
