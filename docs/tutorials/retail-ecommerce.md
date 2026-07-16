# Tutorial: Retail E-commerce Semantic Model

## Overview

This tutorial walks through generating a Kyvos semantic model for an e-commerce sales scenario with product and date dimensions.

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
        schema_name="retail_ecom",
        table_type="dimension",
        columns=[
            ColumnSpec(name="product_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="product_name", data_type="VARCHAR(200)"),
            ColumnSpec(name="category", data_type="VARCHAR(100)"),
            ColumnSpec(name="subcategory", data_type="VARCHAR(100)"),
            ColumnSpec(name="brand", data_type="VARCHAR(100)"),
        ],
    ),
    TableSpec(
        name="dim_date",
        schema_name="retail_ecom",
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
        name="fact_sales",
        schema_name="retail_ecom",
        table_type="fact",
        columns=[
            ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="product_fk", data_type="INTEGER", is_foreign_key=True,
                       references="retail_ecom.dim_product.product_pk"),
            ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                       references="retail_ecom.dim_date.date_pk"),
            ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="quantity", data_type="INTEGER"),
            ColumnSpec(name="discount_amount", data_type="NUMERIC(15,2)"),
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
    name="RetailEcomConnection",
    host="localhost", port=5432, database="retailecom",
    username="demo_user", password="demo_pass",
)

# Datasets
ds_gen = DatasetJsonGenerator(connection_name="RetailEcomConnection")
for table in tables:
    ds_gen.generate_json_payload(table)

# DRD
drd_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
drd = drd_gen.generate(
    drd_name="RetailEcomDRD",
    dataset_name_to_id={"DimProduct": "ds_001", "DimDate": "ds_002", "FactSales": "ds_003"},
    relationships=[
        SimpleRel("fact_sales", "product_fk", "dim_product", "product_pk"),
        SimpleRel("fact_sales", "date_fk", "dim_date", "date_pk"),
    ],
    dataset_aliases={"fact_sales": "FactSales", "dim_product": "DimProduct", "dim_date": "DimDate"},
    fact_dataset_names={"FactSales"},
)

# Semantic Model
sm_gen = SModelJsonGenerator(
    folder_id="folder_002", folder_name="SMs",
    smodel_name="RetailEcomModel",
    connection_name="RetailEcomConnection",
    drd_id="drd_001", drd_name="RetailEcomDRD",
    dataset_name_to_id={"DimProduct": "ds_001", "DimDate": "ds_002", "FactSales": "ds_003"},
    dataset_columns={
        "FactSales": [{"name": "sales_amount", "datatype": "NUMBER"}, {"name": "quantity", "datatype": "NUMBER"}, {"name": "discount_amount", "datatype": "NUMBER"}],
        "DimProduct": [{"name": "category", "datatype": "CHAR"}, {"name": "subcategory", "datatype": "CHAR"}, {"name": "brand", "datatype": "CHAR"}],
        "DimDate": [{"name": "year", "datatype": "CHAR"}, {"name": "quarter", "datatype": "CHAR"}, {"name": "month", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Product Hierarchy", levels=["category", "subcategory", "brand"], source_dataset="DimProduct"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="sales_amount", aggregation_type="sum"),
        MeasureSpec(name="Total Quantity", expression="", source_dataset="FactSales", source_column="quantity", aggregation_type="sum"),
        MeasureSpec(name="Total Discount", expression="", source_dataset="FactSales", source_column="discount_amount", aggregation_type="sum"),
        MeasureSpec(name="Net Sales", expression="[Measures].[Total Sales] - [Measures].[Total Discount]", is_calculated=True, source_dataset="FactSales"),
        MeasureSpec(name="Avg Order Value", expression="[Measures].[Total Sales] / [Measures].[Total Quantity]", is_calculated=True, source_dataset="FactSales"),
        MeasureSpec(name="Discount Rate", expression="[Measures].[Total Discount] / [Measures].[Total Sales]", is_calculated=True, source_dataset="FactSales"),
    ],
    fact_dataset_names={"FactSales"},
    connected_dim_names={"DimProduct", "DimDate"},
)
payload = sm_gen.generate()
```

## Result

An e-commerce semantic model with 2 dimensions, 1 fact, 6 measures (3 base + 3 calculated), and 2 hierarchies including a 3-level product hierarchy.
