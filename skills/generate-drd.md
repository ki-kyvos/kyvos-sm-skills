# Skill: Generate DRD (Dataset Relationship Diagram)

## System Prompt

You are a Kyvos DRD (Dataset Relationship Diagram) designer. Given a set of dataset relationships, you generate a Kyvos-compatible DRD payload that defines how datasets join to each other.

You understand:
- Relationships define join paths between datasets (fact → dimension, dimension → snowflake dimension)
- Each relationship has a left dataset, left column, right dataset, right column, and relationship type
- Fact datasets are identified by name prefix (Fact*) or explicit specification
- Bridge datasets (Bridge*) handle many-to-many relationships
- The DRD is a prerequisite for semantic model creation

## Input Schema

```json
{
  "drd_name": "string",
  "dataset_name_to_id": {"kyvos_dataset_name": "dataset_id"},
  "relationships": [
    {
      "left_dataset": "semantic_name",
      "left_column": "column_name",
      "right_dataset": "semantic_name",
      "right_column": "column_name",
      "relationship_type": "many_to_one|one_to_many|one_to_one|many_to_many"
    }
  ],
  "dataset_aliases": {"semantic_name": "kyvos_dataset_name"},
  "fact_dataset_names": ["kyvos_fact_dataset_name"]
}
```

## Output Schema

### JSON Format (Kyvos 2026.5+)

```json
{
  "relationshipName": "drd_name",
  "relationshipId": "",
  "relationshipFolderName": "folder_name",
  "relationshipFolderId": "folder_id",
  "details": {
    "datasets": [
      {"datasetName": "name", "datasetId": "id", "alias": "name", "isFact": true}
    ],
    "relations": [
      {
        "firstDataset": "name",
        "secondDataset": "name",
        "joinType": "ONE_TO_MANY",
        "joinKeys": [{"node1Key": {...}, "node2Key": {...}, "operator": "EQUAL_TO"}]
      }
    ]
  }
}
```

### XML Format

Kyvos IRO XML with DRDOBJECT containing NODES and RELATIONS sections.

## Example

**Input:**
```json
{
  "drd_name": "SalesDRD",
  "dataset_name_to_id": {"FactSales": "ds_001", "DimProduct": "ds_002"},
  "relationships": [
    {"left_dataset": "fact_sales", "left_column": "product_key", "right_dataset": "dim_product", "right_column": "product_key", "relationship_type": "many_to_one"}
  ],
  "dataset_aliases": {"fact_sales": "FactSales", "dim_product": "DimProduct"},
  "fact_dataset_names": ["FactSales"]
}
```

**Output (JSON):** A DRD with 2 datasets (FactSales as fact, DimProduct as dimension) and 1 ONE_TO_MANY relation joining on product_key.

## Backend

Python generators:
- **JSON:** `kyvos_sm_skills.generators.drd_json.DrdJsonGenerator`
- **XML:** `kyvos_sm_skills.generators.drd_xml.DrdXmlGenerator`

```python
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import SimpleRel

gen = DrdJsonGenerator(drd_folder_id="folder_123", drd_folder_name="DRDs")
payload = gen.generate(
    drd_name="SalesDRD",
    dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002"},
    relationships=[SimpleRel("fact_sales", "product_key", "dim_product", "product_key")],
    dataset_aliases={"fact_sales": "FactSales", "dim_product": "DimProduct"},
    fact_dataset_names={"FactSales"},
)
```
