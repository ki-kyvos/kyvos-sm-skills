"""Tests for kyvos_sm_skills.models — Pydantic model validation."""

from kyvos_sm_skills.models import (
    ColumnSpec,
    DatasetSpec,
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)


class TestColumnSpec:
    def test_minimal(self):
        col = ColumnSpec(name="id", data_type="INTEGER")
        assert col.name == "id"
        assert col.data_type == "INTEGER"
        assert col.nullable is True
        assert col.is_primary_key is False

    def test_primary_key(self):
        col = ColumnSpec(name="pk", data_type="BIGINT", is_primary_key=True, nullable=False)
        assert col.is_primary_key is True
        assert col.nullable is False

    def test_foreign_key_with_references(self):
        col = ColumnSpec(
            name="customer_fk",
            data_type="INTEGER",
            is_foreign_key=True,
            references="public.dim_customer.customer_pk",
        )
        assert col.is_foreign_key is True
        assert col.references == "public.dim_customer.customer_pk"

    def test_with_samples(self):
        col = ColumnSpec(name="status", data_type="VARCHAR(20)", column_samples=["active", "inactive"])
        assert len(col.column_samples) == 2


class TestTableSpec:
    def test_minimal(self):
        table = TableSpec(name="dim_date")
        assert table.name == "dim_date"
        assert table.schema_name == "public"
        assert table.table_type == "dimension"
        assert table.columns == []

    def test_fact_table(self):
        table = TableSpec(
            name="fact_sales",
            schema_name="sales",
            table_type="fact",
            columns=[
                ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="amount", data_type="NUMERIC(15,2)"),
            ],
        )
        assert table.table_type == "fact"
        assert len(table.columns) == 2
        assert table.columns[0].is_primary_key is True

    def test_with_row_count(self):
        table = TableSpec(name="fact_sales", table_type="fact", row_count_target=500000)
        assert table.row_count_target == 500000


class TestDatasetSpec:
    def test_minimal(self):
        ds = DatasetSpec(name="FactSales", source_table="fact_sales", connection_name="MyConn")
        assert ds.name == "FactSales"
        assert ds.source_table == "fact_sales"
        assert ds.connection_name == "MyConn"
        assert ds.columns == []


class TestRelationshipSpec:
    def test_minimal(self):
        rel = RelationshipSpec(
            left_dataset="fact_sales",
            left_column="product_fk",
            right_dataset="dim_product",
            right_column="product_pk",
        )
        assert rel.relationship_type == "many_to_one"
        assert rel.active is True

    def test_custom_type(self):
        rel = RelationshipSpec(
            left_dataset="fact_a",
            left_column="id",
            right_dataset="fact_b",
            right_column="id",
            relationship_type="one_to_one",
        )
        assert rel.relationship_type == "one_to_one"


class TestMeasureSpec:
    def test_base_measure(self):
        m = MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="sales_amount")
        assert m.is_calculated is False
        assert m.aggregation_type == "sum"
        assert m.format_string == "#,##0"

    def test_calculated_measure(self):
        m = MeasureSpec(
            name="Profit Margin",
            expression="[Measures].[Profit] / [Measures].[Revenue]",
            is_calculated=True,
            source_dataset="FactSales",
            format_string="0.00%",
        )
        assert m.is_calculated is True
        assert m.format_string == "0.00%"

    def test_distinct_count(self):
        m = MeasureSpec(
            name="Unique Customers",
            expression="",
            source_dataset="FactSales",
            source_column="customer_fk",
            aggregation_type="distinct_count",
        )
        assert m.aggregation_type == "distinct_count"


class TestHierarchySpec:
    def test_normal_hierarchy(self):
        h = HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate")
        assert h.is_parent_child is False
        assert h.has_alternate_path is False
        assert len(h.levels) == 3

    def test_parent_child_hierarchy(self):
        h = HierarchySpec(
            name="Org Chart",
            levels=["employee_id"],
            source_dataset="DimEmployee",
            is_parent_child=True,
            parent_column="manager_id",
            child_column="employee_id",
        )
        assert h.is_parent_child is True
        assert h.parent_column == "manager_id"


class TestSemanticModelSpec:
    def test_minimal(self):
        sm = SemanticModelSpec(name="SalesModel")
        assert sm.name == "SalesModel"
        assert sm.datasets == []
        assert sm.measures == []

    def test_full_model(self):
        sm = SemanticModelSpec(
            name="SalesModel",
            datasets=[DatasetSpec(name="FactSales", source_table="fact_sales", connection_name="Conn")],
            relationships=[
                RelationshipSpec(
                    left_dataset="FactSales",
                    left_column="product_fk",
                    right_dataset="DimProduct",
                    right_column="product_pk",
                )
            ],
            measures=[MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales")],
            hierarchies=[HierarchySpec(name="Calendar", levels=["year"], source_dataset="DimDate")],
        )
        assert len(sm.datasets) == 1
        assert len(sm.relationships) == 1
        assert len(sm.measures) == 1
        assert len(sm.hierarchies) == 1
