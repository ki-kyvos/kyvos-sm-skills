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

Complete JSON / `iro` wrapper produced by the SDK compiler:

```json
{
  "iro": {
    "name": "drd_name",
    "folderName": "folder_name",
    "folderId": "folder_id",
    "specific": {
      "drdObject": {
        "nodes": [
          {"relDataset": {"datasetId": "id", "aliasName": "name", "type": "FACT"}}
        ],
        "relations": [
          {"sourceId": "", "node1Id": "id", "node2Id": "id"}
        ]
      }
    }
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

Use the SDK's pure compiler to generate deterministic XML or JSON payloads. The compiler consumes a `DrdGraph` contract and returns a `CompiledArtifact`.

```python
from kyvos_sdk.compiler import compile_drd
from kyvos_sdk.contracts.artifacts import ArtifactFormat
from kyvos_sdk.contracts.identity import DrdGraph, DrdNode, DrdRelation

graph = DrdGraph(
    name="SalesDRD",
    folder_id="folder_123",
    folder_name="DRDs",
    nodes=[
        DrdNode(node_id="n1", dataset_id="ds_001", alias="FactSales", node_type="fact"),
        DrdNode(node_id="n2", dataset_id="ds_002", alias="DimProduct", node_type=""),
    ],
    relations=[
        DrdRelation(
            source_node_id="n1",
            target_node_id="n2",
            source_column="product_key",
            target_column="product_key",
        ),
    ],
)

artifact = compile_drd(graph, fmt=ArtifactFormat.JSON)  # or ArtifactFormat.XML
payload = artifact.payload
```
