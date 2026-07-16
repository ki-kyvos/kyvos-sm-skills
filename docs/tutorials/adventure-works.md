# Tutorial: Adventure Works Semantic Model

## Overview

This tutorial walks through generating a Kyvos semantic model for the classic Adventure Works internet sales scenario.

## Prerequisites

```bash
pip install kyvos-sm-skills
```

## Step 1: Define the Schema

```python
from kyvos_sm_skills.models import TableSpec, ColumnSpec

tables = [
    TableSpec(
        name="dim_product",
        schema_name="adventure_works",
        table_type="dimension",
        columns=[
            ColumnSpec(name="product_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="product_name", data_type="VARCHAR(200)"),
            ColumnSpec(name="category", data_type="VARCHAR(100)"),
            ColumnSpec(name="subcategory", data_type="VARCHAR(100)"),
            ColumnSpec(name="color", data_type="VARCHAR(50)"),
            ColumnSpec(name="list_price", data_type="NUMERIC(12,2)"),
        ],
    ),
    TableSpec(
        name="dim_date",
        schema_name="adventure_works",
        table_type="dimension",
        columns=[
            ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="year", data_type="INTEGER"),
            ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
            ColumnSpec(name="month", data_type="VARCHAR(10)"),
            ColumnSpec(name="month_name", data_type="VARCHAR(20)"),
        ],
    ),
    TableSpec(
        name="fact_internet_sales",
        schema_name="adventure_works",
        table_type="fact",
        columns=[
            ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="product_fk", data_type="INTEGER", is_foreign_key=True,
                       references="adventure_works.dim_product.product_pk"),
            ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                       references="adventure_works.dim_date.date_pk"),
            ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="order_quantity", data_type="INTEGER"),
            ColumnSpec(name="unit_price", data_type="NUMERIC(12,2)"),
            ColumnSpec(name="discount_pct", data_type="NUMERIC(7,4)"),
        ],
    ),
]
```

## Step 2: Generate All Payloads

```python
from kyvos_sm_skills.generators.connection_json import generate_connection_json
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import SimpleRel
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.models import MeasureSpec, HierarchySpec

# Connection
conn = generate_connection_json(
    name="AdventureWorksConnection",
    host="localhost", port=5432, database="adventureworks",
    username="demo_user", password="demo_pass",
)

# Datasets
ds_gen = DatasetJsonGenerator(connection_name="AdventureWorksConnection")
for table in tables:
    ds_gen.generate_json_payload(table)

# DRD
drd_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
drd = drd_gen.generate(
    drd_name="AdventureWorksDRD",
    dataset_name_to_id={"DimProduct": "ds_001", "DimDate": "ds_002", "FactInternetSales": "ds_003"},
    relationships=[
        SimpleRel("fact_internet_sales", "product_fk", "dim_product", "product_pk"),
        SimpleRel("fact_internet_sales", "date_fk", "dim_date", "date_pk"),
    ],
    dataset_aliases={"fact_internet_sales": "FactInternetSales", "dim_product": "DimProduct", "dim_date": "DimDate"},
    fact_dataset_names={"FactInternetSales"},
)

# Semantic Model
sm_gen = SModelJsonGenerator(
    folder_id="folder_002", folder_name="SMs",
    smodel_name="AdventureWorksModel",
    connection_name="AdventureWorksConnection",
    drd_id="drd_001", drd_name="AdventureWorksDRD",
    dataset_name_to_id={"DimProduct": "ds_001", "DimDate": "ds_002", "FactInternetSales": "ds_003"},
    dataset_columns={
        "FactInternetSales": [{"name": "sales_amount", "datatype": "NUMBER"}, {"name": "order_quantity", "datatype": "NUMBER"}, {"name": "unit_price", "datatype": "NUMBER"}, {"name": "discount_pct", "datatype": "NUMBER"}],
        "DimProduct": [{"name": "category", "datatype": "CHAR"}, {"name": "subcategory", "datatype": "CHAR"}, {"name": "color", "datatype": "CHAR"}],
        "DimDate": [{"name": "year", "datatype": "CHAR"}, {"name": "quarter", "datatype": "CHAR"}, {"name": "month", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Product Category", levels=["category", "subcategory", "color"], source_dataset="DimProduct"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactInternetSales", source_column="sales_amount", aggregation_type="sum"),
        MeasureSpec(name="Total Order Quantity", expression="", source_dataset="FactInternetSales", source_column="order_quantity", aggregation_type="sum"),
        MeasureSpec(name="Avg Unit Price", expression="", source_dataset="FactInternetSales", source_column="unit_price", aggregation_type="average"),
        MeasureSpec(name="Avg Discount", expression="", source_dataset="FactInternetSales", source_column="discount_pct", aggregation_type="average"),
        MeasureSpec(name="Revenue per Unit", expression="[Measures].[Total Sales] / [Measures].[Total Order Quantity]", is_calculated=True, source_dataset="FactInternetSales"),
    ],
    fact_dataset_names={"FactInternetSales"},
    connected_dim_names={"DimProduct", "DimDate"},
)
payload = sm_gen.generate()
```

## Result

An Adventure Works semantic model with 2 dimensions, 1 fact, 5 measures (4 base + 1 calculated), and 2 hierarchies including a 3-level product category hierarchy.
