# Skill: Generate Semantic Model

## System Prompt

You are a Kyvos semantic model architect. Given a star/snowflake schema with tables, columns, relationships, measures, and hierarchies, you generate a complete Kyvos-compatible semantic model payload.

You understand:
- Fact tables contain measures (numeric columns) and foreign keys to dimensions
- Dimension tables contain descriptive attributes and hierarchies
- The semantic model binds datasets, dimensions, hierarchies, and measures into a queryable OLAP cube
- Kyvos supports both XML and JSON payload formats

## Input Schema

```json
{
  "connection_name": "string",
  "drd_id": "string",
  "drd_name": "string",
  "drd_xml": "string (XML from DRD generator)",
  "dataset_name_to_id": {"dataset_name": "dataset_id"},
  "dataset_columns": {
    "dataset_name": [
      {"name": "column_name", "datatype": "NUMBER|CHAR|DATE|BOOLEAN"}
    ]
  },
  "hierarchy_specs": [
    {
      "name": "hierarchy_name",
      "levels": ["level1", "level2"],
      "source_dataset": "dimension_dataset_name"
    }
  ],
  "semantic_measures": [
    {
      "name": "measure_name",
      "expression": "MDX expression (empty for base measures)",
      "format_string": "#,##0.00",
      "is_calculated": false,
      "source_dataset": "fact_dataset_name",
      "aggregation_type": "sum|average|count|minimum|maximum|distinct_count",
      "source_column": "column_name"
    }
  ],
  "fact_dataset_names": ["fact_dataset_name"],
  "connected_dim_names": ["dimension_dataset_name"]
}
```

## Output Schema

### JSON Format (Kyvos 2026.5+)

```json
{
  "name": "semantic_model_name",
  "folderName": "folder_name",
  "folderId": "folder_id",
  "specific": {
    "attrs": {
      "drdName": "string",
      "drdId": "string",
      "modelType": "BASE",
      "rawDataQuerying": "Disable",
      "rawDataConnectionMode": "MANUAL",
      "rawDataConnectionName": "connection_name"
    },
    "smObject": {
      "dimensions": [...],
      "measures": {"measure": [...]},
      "measureGroups": {"measureGroup": [...]}
    }
  }
}
```

### XML Format

Kyvos IRO XML with DIMENSIONS, MEASURE_GROUPS, and MEASURES sections.

## Example

**Input:**
```json
{
  "connection_name": "PostgresConnection",
  "drd_id": "drd_001",
  "drd_name": "SalesDRD",
  "dataset_name_to_id": {"FactSales": "ds_001", "DimProduct": "ds_002", "DimDate": "ds_003"},
  "dataset_columns": {
    "FactSales": [
      {"name": "sales_key", "datatype": "NUMBER"},
      {"name": "product_key", "datatype": "NUMBER"},
      {"name": "date_key", "datatype": "NUMBER"},
      {"name": "sales_amount", "datatype": "NUMBER"},
      {"name": "quantity", "datatype": "NUMBER"}
    ],
    "DimProduct": [
      {"name": "product_key", "datatype": "NUMBER"},
      {"name": "product_name", "datatype": "CHAR"},
      {"name": "category", "datatype": "CHAR"}
    ],
    "DimDate": [
      {"name": "date_key", "datatype": "NUMBER"},
      {"name": "year", "datatype": "CHAR"},
      {"name": "quarter", "datatype": "CHAR"},
      {"name": "month", "datatype": "CHAR"}
    ]
  },
  "hierarchy_specs": [
    {"name": "Calendar", "levels": ["year", "quarter", "month"], "source_dataset": "DimDate"}
  ],
  "semantic_measures": [
    {"name": "Total Sales", "expression": "", "source_dataset": "FactSales", "source_column": "sales_amount", "aggregation_type": "sum", "is_calculated": false},
    {"name": "Total Quantity", "expression": "", "source_dataset": "FactSales", "source_column": "quantity", "aggregation_type": "sum", "is_calculated": false}
  ],
  "fact_dataset_names": ["FactSales"],
  "connected_dim_names": ["DimProduct", "DimDate"]
}
```

**Output (JSON):** A semantic model with 2 dimensions (DimProduct, DimDate), 1 hierarchy (Calendar on DimDate), 2 base measures (Total Sales, Total Quantity), and 1 measure group (FactSales).

## Backend

Python generators:
- **JSON:** `kyvos_sm_skills.generators.smodel_json.SModelJsonGenerator`
- **XML:** `kyvos_sm_skills.generators.smodel_xml.SModelXmlGenerator`

```python
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator

gen = SModelJsonGenerator(
    folder_id="folder_123",
    folder_name="Semantic Models",
    smodel_name="SalesModel",
    connection_name="PostgresConnection",
    drd_id="drd_001",
    drd_name="SalesDRD",
    dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002"},
    dataset_columns={...},
    hierarchy_specs=[...],
    semantic_measures=[...],
    fact_dataset_names={"FactSales"},
    connected_dim_names={"DimProduct", "DimDate"},
)
payload = gen.generate()
```
