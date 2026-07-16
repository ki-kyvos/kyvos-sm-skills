"""Integration tests: full pipeline from schema to semantic model."""

import json

from kyvos_sm_skills.generators.connection_json import generate_connection_json
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import DrdXmlGenerator, SimpleRel
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.generators.smodel_xml import SModelXmlGenerator
from kyvos_sm_skills.models import ColumnSpec, HierarchySpec, MeasureSpec, TableSpec


def _build_schema():
    tables = [
        TableSpec(
            name="dim_customer",
            schema_name="test_schema",
            table_type="dimension",
            columns=[
                ColumnSpec(name="customer_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="customer_name", data_type="VARCHAR(200)"),
                ColumnSpec(name="region", data_type="VARCHAR(100)"),
            ],
        ),
        TableSpec(
            name="dim_date",
            schema_name="test_schema",
            table_type="dimension",
            columns=[
                ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="year", data_type="INTEGER"),
                ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
                ColumnSpec(name="month", data_type="VARCHAR(10)"),
            ],
        ),
        TableSpec(
            name="fact_sales",
            schema_name="test_schema",
            table_type="fact",
            columns=[
                ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="customer_fk", data_type="INTEGER", is_foreign_key=True,
                           references="test_schema.dim_customer.customer_pk"),
                ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                           references="test_schema.dim_date.date_pk"),
                ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="quantity", data_type="INTEGER"),
            ],
        ),
    ]
    return tables


def _format_name(name):
    return "".join(w.capitalize() for w in name.split("_"))


class TestFullPipeline:
    def test_end_to_end_json(self):
        tables = _build_schema()
        conn_name = "TestConnection"

        # 1. Connection
        conn = generate_connection_json(
            name=conn_name, host="localhost", port=5432,
            database="testdb", username="u", password="p",
        )
        assert conn["name"] == conn_name

        # 2. Datasets
        ds_gen = DatasetJsonGenerator(connection_name=conn_name)
        dataset_name_to_id = {}
        dataset_aliases = {}
        dataset_columns = {}
        fact_names = set()
        dim_names = set()

        for table in tables:
            ds_name = _format_name(table.name)
            ds_id = f"ds_{abs(hash(ds_name)) % 1000000:06d}"
            dataset_name_to_id[ds_name] = ds_id
            dataset_aliases[table.name] = ds_name

            from kyvos_sm_skills.type_mapping import resolve_sql_type, SQL_TO_KYVOS_XML_MAP
            cols = []
            for col in table.columns:
                canonical = resolve_sql_type(col.data_type)
                dt = SQL_TO_KYVOS_XML_MAP.get(canonical, ("CHAR", "1", "", "1"))[0]
                cols.append({"name": col.name, "datatype": dt})
            dataset_columns[ds_name] = cols

            if table.table_type == "fact":
                fact_names.add(ds_name)
            else:
                dim_names.add(ds_name)

            payload = ds_gen.generate_json_payload(table)
            assert payload["datasetName"] == ds_name

        # 3. DRD
        drd_xml_gen = DrdXmlGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
        drd_xml = drd_xml_gen.generate(
            drd_name="TestDRD",
            dataset_name_to_id=dataset_name_to_id,
            relationships=[
                SimpleRel("fact_sales", "customer_fk", "dim_customer", "customer_pk"),
                SimpleRel("fact_sales", "date_fk", "dim_date", "date_pk"),
            ],
            dataset_aliases=dataset_aliases,
            fact_dataset_names=fact_names,
        )
        assert len(drd_xml) > 0

        drd_json_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
        drd_json = drd_json_gen.generate(
            drd_name="TestDRD",
            dataset_name_to_id=dataset_name_to_id,
            relationships=[
                SimpleRel("fact_sales", "customer_fk", "dim_customer", "customer_pk"),
                SimpleRel("fact_sales", "date_fk", "dim_date", "date_pk"),
            ],
            dataset_aliases=dataset_aliases,
            fact_dataset_names=fact_names,
        )
        assert drd_json is not None

        # 4. Semantic Model JSON
        sm_json_gen = SModelJsonGenerator(
            folder_id="folder_002",
            folder_name="SMs",
            smodel_name="TestModel",
            connection_name=conn_name,
            drd_id="drd_001",
            drd_name="TestDRD",
            drd_xml=drd_xml,
            dataset_name_to_id=dataset_name_to_id,
            dataset_columns=dataset_columns,
            hierarchy_specs=[
                HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
            ],
            semantic_measures=[
                MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="sales_amount", aggregation_type="sum"),
                MeasureSpec(name="Total Quantity", expression="", source_dataset="FactSales", source_column="quantity", aggregation_type="sum"),
                MeasureSpec(name="Avg Sale", expression="[Measures].[Total Sales] / [Measures].[Total Quantity]", is_calculated=True, source_dataset="FactSales"),
            ],
            fact_dataset_names=fact_names,
            connected_dim_names=dim_names,
        )
        sm_payload = sm_json_gen.generate()
        assert sm_payload["name"] == "TestModel"
        assert "smObject" in sm_payload["specific"]

        # 5. Semantic Model XML
        sm_xml_gen = SModelXmlGenerator(
            folder_id="folder_003",
            folder_name="SMs",
            smodel_name="TestModel",
            connection_name=conn_name,
            drd_id="drd_001",
            drd_name="TestDRD",
            drd_xml=drd_xml,
            dataset_name_to_id=dataset_name_to_id,
            dataset_columns=dataset_columns,
            hierarchy_specs=[
                HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
            ],
            semantic_measures=[
                MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="sales_amount", aggregation_type="sum"),
                MeasureSpec(name="Total Quantity", expression="", source_dataset="FactSales", source_column="quantity", aggregation_type="sum"),
            ],
        )
        sm_xml = sm_xml_gen.generate()
        assert len(sm_xml) > 0

    def test_no_app_imports(self):
        """Verify no app.* imports exist in the package."""
        import kyvos_sm_skills
        import inspect
        import os

        pkg_dir = os.path.dirname(inspect.getfile(kyvos_sm_skills))
        for root, dirs, files in os.walk(pkg_dir):
            for fname in files:
                if fname.endswith(".py"):
                    fpath = os.path.join(root, fname)
                    with open(fpath) as f:
                        content = f.read()
                    assert "from app." not in content, f"Found 'from app.' in {fpath}"
                    assert "import app." not in content, f"Found 'import app.' in {fpath}"
