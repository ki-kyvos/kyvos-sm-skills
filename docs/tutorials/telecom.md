# Tutorial: Telecom Semantic Model

## Overview

This tutorial walks through generating a Kyvos semantic model for a telecom network usage scenario with geographic and date dimensions.

## Prerequisites

```bash
pip install kyvos-sm-skills
```

## Step 1: Define the Schema

```python
from kyvos_sm_skills.models import TableSpec, ColumnSpec

tables = [
    TableSpec(
        name="dim_region",
        schema_name="telecom",
        table_type="dimension",
        columns=[
            ColumnSpec(name="region_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="region_name", data_type="VARCHAR(100)"),
            ColumnSpec(name="country", data_type="VARCHAR(50)"),
            ColumnSpec(name="city", data_type="VARCHAR(100)"),
        ],
    ),
    TableSpec(
        name="dim_date",
        schema_name="telecom",
        table_type="dimension",
        columns=[
            ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="year", data_type="INTEGER"),
            ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
            ColumnSpec(name="month", data_type="VARCHAR(10)"),
        ],
    ),
    TableSpec(
        name="fact_usage",
        schema_name="telecom",
        table_type="fact",
        columns=[
            ColumnSpec(name="usage_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="region_fk", data_type="INTEGER", is_foreign_key=True,
                       references="telecom.dim_region.region_pk"),
            ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                       references="telecom.dim_date.date_pk"),
            ColumnSpec(name="call_minutes", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="data_usage_mb", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="sms_count", data_type="INTEGER"),
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
    name="TelecomConnection",
    host="localhost", port=5432, database="telecom",
    username="demo_user", password="demo_pass",
)

# Datasets
ds_gen = DatasetJsonGenerator(connection_name="TelecomConnection")
for table in tables:
    ds_gen.generate_json_payload(table)

# DRD
drd_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
drd = drd_gen.generate(
    drd_name="TelecomDRD",
    dataset_name_to_id={"DimRegion": "ds_001", "DimDate": "ds_002", "FactUsage": "ds_003"},
    relationships=[
        SimpleRel("fact_usage", "region_fk", "dim_region", "region_pk"),
        SimpleRel("fact_usage", "date_fk", "dim_date", "date_pk"),
    ],
    dataset_aliases={"fact_usage": "FactUsage", "dim_region": "DimRegion", "dim_date": "DimDate"},
    fact_dataset_names={"FactUsage"},
)

# Semantic Model
sm_gen = SModelJsonGenerator(
    folder_id="folder_002", folder_name="SMs",
    smodel_name="TelecomModel",
    connection_name="TelecomConnection",
    drd_id="drd_001", drd_name="TelecomDRD",
    dataset_name_to_id={"DimRegion": "ds_001", "DimDate": "ds_002", "FactUsage": "ds_003"},
    dataset_columns={
        "FactUsage": [{"name": "call_minutes", "datatype": "NUMBER"}, {"name": "data_usage_mb", "datatype": "NUMBER"}, {"name": "sms_count", "datatype": "NUMBER"}],
        "DimRegion": [{"name": "country", "datatype": "CHAR"}, {"name": "region_name", "datatype": "CHAR"}, {"name": "city", "datatype": "CHAR"}],
        "DimDate": [{"name": "year", "datatype": "CHAR"}, {"name": "quarter", "datatype": "CHAR"}, {"name": "month", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Geography", levels=["country", "region_name", "city"], source_dataset="DimRegion"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Call Minutes", expression="", source_dataset="FactUsage", source_column="call_minutes", aggregation_type="sum"),
        MeasureSpec(name="Total Data Usage", expression="", source_dataset="FactUsage", source_column="data_usage_mb", aggregation_type="sum"),
        MeasureSpec(name="Total SMS", expression="", source_dataset="FactUsage", source_column="sms_count", aggregation_type="sum"),
        MeasureSpec(name="Avg Data per SMS", expression="[Measures].[Total Data Usage] / [Measures].[Total SMS]", is_calculated=True, source_dataset="FactUsage"),
    ],
    fact_dataset_names={"FactUsage"},
    connected_dim_names={"DimRegion", "DimDate"},
)
payload = sm_gen.generate()
```

## Result

A telecom semantic model with 2 dimensions, 1 fact, 4 measures (3 base + 1 calculated), and a 3-level geography hierarchy.
