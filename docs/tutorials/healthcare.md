# Tutorial: Healthcare Semantic Model

## Overview

This tutorial walks through generating a Kyvos semantic model for a healthcare patient admissions scenario.

## Prerequisites

```bash
pip install kyvos-sm-skills
```

## Step 1: Define the Schema

```python
from kyvos_sm_skills.models import TableSpec, ColumnSpec

tables = [
    TableSpec(
        name="dim_patient",
        schema_name="healthcare",
        table_type="dimension",
        columns=[
            ColumnSpec(name="patient_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="patient_id", data_type="VARCHAR(20)"),
            ColumnSpec(name="gender", data_type="VARCHAR(10)"),
            ColumnSpec(name="age_group", data_type="VARCHAR(20)"),
        ],
    ),
    TableSpec(
        name="dim_date",
        schema_name="healthcare",
        table_type="dimension",
        columns=[
            ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="year", data_type="INTEGER"),
            ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
            ColumnSpec(name="month", data_type="VARCHAR(10)"),
        ],
    ),
    TableSpec(
        name="fact_admissions",
        schema_name="healthcare",
        table_type="fact",
        columns=[
            ColumnSpec(name="admission_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="patient_fk", data_type="INTEGER", is_foreign_key=True,
                       references="healthcare.dim_patient.patient_pk"),
            ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                       references="healthcare.dim_date.date_pk"),
            ColumnSpec(name="admission_count", data_type="INTEGER"),
            ColumnSpec(name="length_of_stay", data_type="NUMERIC(8,2)"),
            ColumnSpec(name="total_cost", data_type="NUMERIC(15,2)"),
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
    name="HealthcareConnection",
    host="localhost", port=5432, database="healthcare",
    username="demo_user", password="demo_pass",
)

# Datasets
ds_gen = DatasetJsonGenerator(connection_name="HealthcareConnection")
for table in tables:
    ds_gen.generate_json_payload(table)

# DRD
drd_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
drd = drd_gen.generate(
    drd_name="HealthcareDRD",
    dataset_name_to_id={"DimPatient": "ds_001", "DimDate": "ds_002", "FactAdmissions": "ds_003"},
    relationships=[
        SimpleRel("fact_admissions", "patient_fk", "dim_patient", "patient_pk"),
        SimpleRel("fact_admissions", "date_fk", "dim_date", "date_pk"),
    ],
    dataset_aliases={"fact_admissions": "FactAdmissions", "dim_patient": "DimPatient", "dim_date": "DimDate"},
    fact_dataset_names={"FactAdmissions"},
)

# Semantic Model
sm_gen = SModelJsonGenerator(
    folder_id="folder_002", folder_name="SMs",
    smodel_name="HealthcareModel",
    connection_name="HealthcareConnection",
    drd_id="drd_001", drd_name="HealthcareDRD",
    dataset_name_to_id={"DimPatient": "ds_001", "DimDate": "ds_002", "FactAdmissions": "ds_003"},
    dataset_columns={
        "FactAdmissions": [{"name": "admission_count", "datatype": "NUMBER"}, {"name": "length_of_stay", "datatype": "NUMBER"}, {"name": "total_cost", "datatype": "NUMBER"}],
        "DimPatient": [{"name": "gender", "datatype": "CHAR"}, {"name": "age_group", "datatype": "CHAR"}],
        "DimDate": [{"name": "year", "datatype": "CHAR"}, {"name": "quarter", "datatype": "CHAR"}, {"name": "month", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Patient Demographics", levels=["age_group", "gender"], source_dataset="DimPatient"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Admissions", expression="", source_dataset="FactAdmissions", source_column="admission_count", aggregation_type="sum"),
        MeasureSpec(name="Avg Length of Stay", expression="", source_dataset="FactAdmissions", source_column="length_of_stay", aggregation_type="average"),
        MeasureSpec(name="Total Cost", expression="", source_dataset="FactAdmissions", source_column="total_cost", aggregation_type="sum"),
        MeasureSpec(name="Cost per Admission", expression="[Measures].[Total Cost] / [Measures].[Total Admissions]", is_calculated=True, source_dataset="FactAdmissions"),
    ],
    fact_dataset_names={"FactAdmissions"},
    connected_dim_names={"DimPatient", "DimDate"},
)
payload = sm_gen.generate()
```

## Result

A healthcare semantic model with 2 dimensions, 1 fact, 4 measures (3 base + 1 calculated), and 2 hierarchies.
