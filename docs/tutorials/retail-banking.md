# Tutorial: Retail Banking Semantic Model

## Overview

This tutorial walks through generating a complete Kyvos semantic model for a retail banking scenario with customer transactions.

## Prerequisites

```bash
pip install kyvos-sm-skills
```

## Step 1: Define the Schema

```python
from kyvos_sm_skills.models import TableSpec, ColumnSpec

tables = [
    TableSpec(
        name="dim_customer",
        schema_name="retail_banking",
        table_type="dimension",
        columns=[
            ColumnSpec(name="customer_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="customer_id", data_type="VARCHAR(20)"),
            ColumnSpec(name="customer_name", data_type="VARCHAR(200)"),
            ColumnSpec(name="segment", data_type="VARCHAR(50)"),
            ColumnSpec(name="region", data_type="VARCHAR(100)"),
        ],
    ),
    TableSpec(
        name="dim_date",
        schema_name="retail_banking",
        table_type="dimension",
        columns=[
            ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="year", data_type="INTEGER"),
            ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
            ColumnSpec(name="month", data_type="VARCHAR(10)"),
        ],
    ),
    TableSpec(
        name="fact_transactions",
        schema_name="retail_banking",
        table_type="fact",
        columns=[
            ColumnSpec(name="transaction_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="customer_fk", data_type="INTEGER", is_foreign_key=True,
                       references="retail_banking.dim_customer.customer_pk"),
            ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                       references="retail_banking.dim_date.date_pk"),
            ColumnSpec(name="transaction_amount", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="fee_amount", data_type="NUMERIC(15,2)"),
        ],
    ),
]
```

## Step 2: Generate Connection

```python
from kyvos_sm_skills.generators.connection_json import generate_connection_json

conn = generate_connection_json(
    name="RetailBankingConnection",
    host="localhost", port=5432, database="retailbanking",
    username="demo_user", password="demo_pass",
)
```

## Step 3: Generate Datasets

```python
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator

ds_gen = DatasetJsonGenerator(connection_name="RetailBankingConnection")
for table in tables:
    payload = ds_gen.generate_json_payload(table)
    print(f"Dataset: {payload['datasetName']}")
```

## Step 4: Generate DRD

```python
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import SimpleRel

drd_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
drd = drd_gen.generate(
    drd_name="RetailBankingDRD",
    dataset_name_to_id={"DimCustomer": "ds_001", "DimDate": "ds_002", "FactTransactions": "ds_003"},
    relationships=[
        SimpleRel("fact_transactions", "customer_fk", "dim_customer", "customer_pk"),
        SimpleRel("fact_transactions", "date_fk", "dim_date", "date_pk"),
    ],
    dataset_aliases={"fact_transactions": "FactTransactions", "dim_customer": "DimCustomer", "dim_date": "DimDate"},
    fact_dataset_names={"FactTransactions"},
)
```

## Step 5: Generate Semantic Model

```python
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.models import MeasureSpec, HierarchySpec

sm_gen = SModelJsonGenerator(
    folder_id="folder_002", folder_name="SMs",
    smodel_name="RetailBankingModel",
    connection_name="RetailBankingConnection",
    drd_id="drd_001", drd_name="RetailBankingDRD",
    dataset_name_to_id={"DimCustomer": "ds_001", "DimDate": "ds_002", "FactTransactions": "ds_003"},
    dataset_columns={
        "FactTransactions": [{"name": "transaction_amount", "datatype": "NUMBER"}, {"name": "fee_amount", "datatype": "NUMBER"}],
        "DimCustomer": [{"name": "segment", "datatype": "CHAR"}, {"name": "region", "datatype": "CHAR"}],
        "DimDate": [{"name": "year", "datatype": "CHAR"}, {"name": "quarter", "datatype": "CHAR"}, {"name": "month", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Customer Geography", levels=["region", "segment"], source_dataset="DimCustomer"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Transaction Amount", expression="", source_dataset="FactTransactions", source_column="transaction_amount", aggregation_type="sum"),
        MeasureSpec(name="Total Fees", expression="", source_dataset="FactTransactions", source_column="fee_amount", aggregation_type="sum"),
        MeasureSpec(name="Net Revenue", expression="[Measures].[Total Transaction Amount] + [Measures].[Total Fees]", is_calculated=True, source_dataset="FactTransactions"),
    ],
    fact_dataset_names={"FactTransactions"},
    connected_dim_names={"DimCustomer", "DimDate"},
)
payload = sm_gen.generate()
```

## Result

You now have a complete set of Kyvos payloads for a retail banking semantic model with 2 dimensions, 1 fact table, 3 measures (2 base + 1 calculated), and 2 hierarchies.
