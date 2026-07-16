# Skill: Generate Dataset

## System Prompt

You are a Kyvos dataset designer. Given a table specification with columns and data types, you generate a Kyvos-compatible dataset payload that registers the table as a queryable dataset in Kyvos.

You understand:
- Fact tables have role "FACT", dimension tables have role "DIMENSION"
- Table names are converted to PascalCase for Kyvos (e.g. `fact_sales` → `FactSales`)
- SQL queries use `SELECT * FROM schema.table` format
- Column types are mapped from SQL types to Kyvos types

## Input Schema

```json
{
  "name": "table_name (snake_case)",
  "schema_name": "public",
  "table_type": "fact|dimension|bridge",
  "columns": [
    {"name": "column_name", "data_type": "SQL type (e.g. INTEGER, VARCHAR(100), NUMERIC(15,2))"}
  ],
  "description": "optional table description"
}
```

## Output Schema

### JSON Format (Kyvos 2026.5+)

```json
{
  "datasetName": "PascalCaseTableName",
  "datasetId": "auto_generated_id",
  "folderName": "folder_name",
  "folderId": "folder_id",
  "datasetDetails": {
    "connectionName": "connection_name",
    "inputType": "SQL",
    "sqlDetails": {"sql": "SELECT * FROM schema.table"},
    "parameters": [],
    "partitionDetails": {"metadataMode": "AUTO", ...}
  }
}
```

### XML Format

Kyvos IRO XML with QO > TRANSFORMATION > STEPS > FETCH section containing column definitions.

## Example

**Input:**
```json
{
  "name": "fact_sales",
  "schema_name": "public",
  "table_type": "fact",
  "columns": [
    {"name": "sales_key", "data_type": "INTEGER", "is_primary_key": true},
    {"name": "product_key", "data_type": "INTEGER", "is_foreign_key": true},
    {"name": "sales_amount", "data_type": "NUMERIC(15,2)"},
    {"name": "sales_date", "data_type": "DATE"}
  ]
}
```

**Output (JSON):**
```json
{
  "datasetName": "FactSales",
  "datasetId": "a1b2c3d4e5f6a7b8",
  "folderName": "Demo Automation",
  "folderId": "folder_123",
  "datasetDetails": {
    "connectionName": "PostgresConnection",
    "inputType": "SQL",
    "sqlDetails": {"sql": "SELECT * FROM public.fact_sales"},
    "parameters": [],
    "partitionDetails": {"metadataMode": "AUTO", "columnName": "", "tableName": "", "tableRecordCount": "", "numberOfPartitions": "", "columnMaxValue": "", "columnMinValue": ""}
  }
}
```

## Backend

Python generators:
- **JSON:** `kyvos_sm_skills.generators.dataset_json.DatasetJsonGenerator`
- **XML:** `kyvos_sm_skills.generators.dataset_xml.DatasetXmlGenerator`

```python
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.models import TableSpec, ColumnSpec

gen = DatasetJsonGenerator(connection_name="PostgresConnection")
table = TableSpec(name="fact_sales", schema_name="public", table_type="fact", columns=[
    ColumnSpec(name="sales_key", data_type="INTEGER", is_primary_key=True),
    ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
])
payload = gen.generate_json_payload(table)
```
