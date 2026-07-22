# kyvos-sm-skills

Claude skills + payload generators for Kyvos semantic model creation.

## Overview

`kyvos-sm-skills` is a toolkit of Claude skill definitions (prompt-based markdown files) and contract adapters that teach Claude how to design, generate, and deploy Kyvos semantic models. It sits on top of `kyvos-sdk-python`:

- **Skill definitions** for schema design, data generation, and end-to-end Kyvos deployment
- **Contract adapters** (`kyvos_sm_skills.contract_adapter`) that wrap the SDK's pure compilers (`kyvos_sdk.compiler`) for backward-compatible inputs
- **Legacy payload generators** retained for compatibility but superseded by the SDK compilers

## Installation

```bash
pip install kyvos-sm-skills[sdk]
```

For development:

```bash
pip install -e ".[sdk,dev]"
```

## Quick Start

The recommended path is to use the SDK compilers directly (or via `kyvos_sm_skills.contract_adapter` for legacy-shaped inputs):

```python
from kyvos_sdk.compiler import compile_connection, compile_dataset, compile_drd
from kyvos_sdk.contracts.artifacts import ArtifactFormat
from kyvos_sdk.contracts.domain import ColumnSpec, TableSpec
from kyvos_sdk.contracts.identity import DrdGraph, DrdNode, DrdRelation

# 1. Generate a connection artifact
conn_artifact = compile_connection(
    name="MyConnection",
    host="localhost",
    port=5432,
    database="mydb",
    username="user",
    password="pass",
    fmt=ArtifactFormat.JSON,
)
conn_payload = conn_artifact.payload

# 2. Generate a dataset artifact
table = TableSpec(
    name="fact_sales",
    schema_name="public",
    table_type="fact",
    columns=[
        ColumnSpec(name="sales_id", data_type="INTEGER", is_primary_key=True),
        ColumnSpec(name="product_key", data_type="INTEGER", is_foreign_key=True),
        ColumnSpec(name="amount", data_type="NUMERIC(15,2)"),
    ],
)
dataset_artifact = compile_dataset(
    table,
    connection_name="MyConnection",
    folder_id="folder_123",
    folder_name="Datasets",
    fmt=ArtifactFormat.JSON,
)
dataset_payload = dataset_artifact.payload

# 3. Generate a DRD artifact
graph = DrdGraph(
    name="SalesDRD",
    folder_id="folder_456",
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
drd_artifact = compile_drd(graph, fmt=ArtifactFormat.JSON)
drd_payload = drd_artifact.payload
```

For end-to-end deployment orchestration, see the Claude skills under `skills/` (e.g. `deploy-from-xmla.md`).

## Claude Skills

The `skills/` directory contains markdown skill files that can be loaded into Claude or any LLM:

| Skill | Description |
|-------|-------------|
| `deploy-from-xmla.md` | End-to-end: XMLA file → folders, connection, datasets, DRD, semantic model on Kyvos |
| `deploy-from-pbit.md` | End-to-end: Power BI Template (.pbit) → Kyvos deployment |
| `discover-sm-from-warehouse.md` | Inspect warehouse schema → recommend + deploy semantic models |
| `generate-sm-from-intent.md` | Natural language intent → generated data + Kyvos semantic model |
| `generate-semantic-model.md` | Schema + relationships + measures → complete SM payload |
| `generate-dataset.md` | Table spec → Kyvos dataset payload |
| `generate-drd.md` | Relationships → DRD payload |
| `generate-connection.md` | DB params → connection payload |
| `convert-dax-to-mdx.md` | DAX expressions → MDX |
| `design-star-schema.md` | Domain description → star/snowflake/multifact schema |
| `design-measures.md` | Schema + domain → measures with MDX |
| `inspect-warehouse-schema.md` | Warehouse introspection → schema summary + pattern detection |

See `docs/claude-skill-usage.md` for detailed usage instructions.

## CLI

The `kyvos-skills` command-line tool is installed with the package:

```bash
# List available skills
kyvos-skills list

# Export skill files for Claude Code
kyvos-skills export-skill deploy-from-xmla
kyvos-skills export-skill --all -o ./skills

# Deploy an XMLA model (no Claude needed)
kyvos-skills deploy --xmla-path ./AdventureWorks.xmla --env-file ./.env

# Dry run (parse only)
kyvos-skills deploy --xmla-path ./AdventureWorks.xmla --env-file ./.env --dry-run

# Discover SM from warehouse (pre-approved JSON mode)
kyvos-skills discover --env-file ./.env --sm-design ./sm-design.json --dry-run

# Discover SM from warehouse (LLM mode via Anthropic API)
kyvos-skills discover --env-file ./.env --user-intent "I want sales analytics" --domain adventure_works --auto-approve

# Discover with schema filter and payload format override
kyvos-skills discover --env-file ./.env --sm-design ./sm-design.json --schema sales --payload-format json
```

See the [Deployment & Getting Started Guide](docs/deployment-guide.md) for full instructions.

## Discover SM from Warehouse

The discover flow inspects an existing warehouse schema, generates or loads a semantic model design, and deploys it to Kyvos — all without an XMLA file.

### Two Modes

1. **Pre-approved JSON mode** — Provide a pre-approved SM design JSON file directly. No LLM needed.
2. **LLM mode** — Provide a natural language `user_intent`; the flow uses the Anthropic API (Claude) to generate an SM design, validates it against the inspected schema, and presents it for approval before deploying.

### Programmatic API

```python
from kyvos_sm_skills.skill_runner import run_discover_sm_from_warehouse

# Pre-approved JSON mode
rc = run_discover_sm_from_warehouse(
    env_file=".env",
    sm_design_path="samples/adventureworks-sm-design.json",
    dry_run=True,
)

# LLM mode (requires anthropic + ANTHROPIC_API_KEY)
rc = run_discover_sm_from_warehouse(
    env_file=".env",
    user_intent="I want sales analytics for Adventure Works",
    domain="adventure_works",
    auto_approve=True,
)
```

### LLM Designer Module

The `kyvos_sm_skills.llm_designer` module provides the LLM-based SM design logic:

```python
from kyvos_sm_skills.llm_designer import design_sm_from_schema, validate_sm_recommendation

# Generate SM recommendation via Claude
recommendation = design_sm_from_schema(
    schema_summary=inspected_schema,
    user_intent="I want sales analytics",
    domain="adventure_works",
)

# Validate against warehouse schema
errors = validate_sm_recommendation(recommendation, inspected_schema)
```

### Spec Builder Module

The `kyvos_sm_skills.spec_builder` module converts SM recommendations + warehouse schema into typed deployment specs:

```python
from kyvos_sm_skills.spec_builder import build_spec_from_recommendation

discovered_spec = build_spec_from_recommendation(
    sm_rec=recommendation["recommended_sms"][0],
    warehouse_tables=inspected_schema["tables"],
)
```

### Warehouse Schema Inspector

The `kyvos_sdk.warehouse_inspector` module (in `kyvos-sdk-python[inspect]`) provides SQLAlchemy-based schema introspection:

```python
from kyvos_sdk.warehouse_inspector import inspect_schema
from kyvos_sdk.config import KyvosConfig

config = KyvosConfig.from_env_file(".env")
schema_summary = inspect_schema(config, schema_filter="sales", max_tables=500)
```

### Validation Scripts

```bash
# Sandbox validation (dry run, no server needed)
python scripts/validate_sandbox_discover.py \
    --env-file .env \
    --sm-design samples/adventureworks-sm-design.json

# Live integration test (requires warehouse + Kyvos server)
python scripts/test_live_discover.py \
    --env-file .env \
    --sm-design samples/adventureworks-sm-design.json
```

### Sample SM Design

A sample AdventureWorks SM design is provided at `samples/adventureworks-sm-design.json`.

### Installation

```bash
# Pre-approved JSON mode only
pip install kyvos-sm-skills[sdk] kyvos-sdk-python[inspect]

# With LLM mode (Anthropic API)
pip install kyvos-sm-skills[sdk,anthropic] kyvos-sdk-python[inspect]
```

## Documentation

- [Deployment & Getting Started Guide](docs/deployment-guide.md)
- [Quickstart](docs/quickstart.md)
- [Tutorials](docs/tutorials/)
- [API Reference](docs/api-reference.md)
- [Sample Gallery](docs/sample-gallery.md)
- [Claude Skill Usage](docs/claude-skill-usage.md)

## License

Apache 2.0 — See [LICENSE](LICENSE)
