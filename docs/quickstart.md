# Quickstart

## Install

```bash
pip install kyvos-sm-skills
```

## Generate a Connection

```python
from kyvos_sm_skills.generators.connection_json import generate_connection_json

conn = generate_connection_json(
    name="MyConnection",
    host="localhost",
    port=5432,
    database="mydb",
    username="user",
    password="pass",
)
```

## Generate Datasets

```python
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.models import TableSpec, ColumnSpec

gen = DatasetJsonGenerator(connection_name="MyConnection")
table = TableSpec(
    name="fact_sales",
    schema_name="public",
    table_type="fact",
    columns=[
        ColumnSpec(name="sales_key", data_type="INTEGER", is_primary_key=True),
        ColumnSpec(name="product_key", data_type="INTEGER", is_foreign_key=True),
        ColumnSpec(name="amount", data_type="NUMERIC(15,2)"),
    ],
)
payload = gen.generate_json_payload(table)
```

## Generate a DRD

```python
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import SimpleRel

gen = DrdJsonGenerator(drd_folder_id="folder_123", drd_folder_name="DRDs")
payload = gen.generate(
    drd_name="SalesDRD",
    dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002"},
    relationships=[
        SimpleRel("fact_sales", "product_key", "dim_product", "product_key"),
    ],
    dataset_aliases={"fact_sales": "FactSales", "dim_product": "DimProduct"},
    fact_dataset_names={"FactSales"},
)
```

## Generate a Semantic Model

```python
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.models import MeasureSpec, HierarchySpec

gen = SModelJsonGenerator(
    folder_id="folder_456",
    folder_name="SMs",
    smodel_name="SalesModel",
    connection_name="MyConnection",
    drd_id="drd_001",
    drd_name="SalesDRD",
    dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002"},
    dataset_columns={
        "FactSales": [{"name": "amount", "datatype": "NUMBER"}],
        "DimProduct": [{"name": "product_name", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Product Hierarchy", levels=["category", "subcategory"], source_dataset="DimProduct"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="amount", aggregation_type="sum"),
    ],
    fact_dataset_names={"FactSales"},
    connected_dim_names={"DimProduct"},
)
payload = gen.generate()
```

## Generate Sample Models

```bash
pip install -e ".[dev]"
python scripts/generate_samples.py
```

This generates 5 sample models in `samples/models/<vertical>/` with connection, dataset, DRD, and semantic model payloads (both JSON and XML).
