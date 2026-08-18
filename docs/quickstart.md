# Quickstart

## Install

```bash
pip install kyvos-sm-skills[sdk]
```

The `[sdk]` extra pulls in `kyvos-sdk-python`, which provides the pure compilers and typed contracts used below.

## Generate a Connection

```python
from kyvos_sdk.compiler import compile_connection
from kyvos_sdk.contracts.artifacts import ArtifactFormat

artifact = compile_connection(
    name="MyConnection",
    host="localhost",
    port=5432,
    database="mydb",
    username="user",
    password="pass",
    db_type="POSTGRES",
    db_version="14",
    fmt=ArtifactFormat.JSON,  # or ArtifactFormat.XML
)
payload = artifact.payload
```

## Generate Datasets

```python
from kyvos_sdk.compiler import compile_dataset
from kyvos_sdk.contracts.artifacts import ArtifactFormat
from kyvos_sdk.contracts.domain import ColumnSpec, TableSpec

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
artifact = compile_dataset(
    table,
    connection_name="MyConnection",
    folder_id="folder_123",
    folder_name="Datasets",
    fmt=ArtifactFormat.JSON,
)
payload = artifact.payload
```

## Generate a DRD

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
artifact = compile_drd(graph, fmt=ArtifactFormat.JSON)
payload = artifact.payload
```

## Generate a Semantic Model

```python
from kyvos_sdk.compiler import compile_semantic_model
from kyvos_sdk.contracts.artifacts import ArtifactFormat
from kyvos_sdk.contracts.domain import SemanticModelSpec
from kyvos_sdk.contracts.identity import DrdGraph

model_spec = SemanticModelSpec(...)  # name, datasets, relationships, measures, hierarchies
graph = DrdGraph(...)  # nodes and relations for the DRD

artifact = compile_semantic_model(
    model_spec,
    graph,
    connection_name="MyConnection",
    fmt=ArtifactFormat.JSON,
)
payload = artifact.payload
```

## Deploy Artifacts

Use the SDK's `ProvisioningClient` to apply compiled artifacts to Kyvos:

```python
from kyvos_sdk import KyvosService, ProvisioningClient

svc = KyvosService(config=config)
svc.initialize()
prov = ProvisioningClient(svc)

result = prov.apply_artifact(artifact)
if not result.succeeded:
    raise RuntimeError(result.diagnostics)
```

See the `skills/` directory (e.g. `deploy-from-xmla.md`) for complete end-to-end deployment orchestration.

## Legacy Generators

The older `kyvos_sm_skills.generators.*` and `kyvos_sm_skills.models` APIs are still present for backward compatibility, but they are deprecated in favor of the SDK contracts and compilers.
