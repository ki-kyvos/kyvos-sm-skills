# kyvos-sm-skills

Claude skills + payload generators for Kyvos semantic model creation.

## Overview

`kyvos-sm-skills` is a standalone toolkit for generating Kyvos-compatible semantic model payloads (XML + JSON) from table schemas, relationships, and measures. It includes:

- **Payload generators** for connections, datasets, DRDs, and semantic models (both XML and JSON formats)
- **Claude skill definitions** (prompt-based markdown files) that teach Claude how to design and generate Kyvos semantic models
- **Type mapping utilities** for SQL-to-Kyvos type conversion
- **Sample models** for 5 industry verticals
- **Comprehensive documentation** including quickstart, per-vertical tutorials, and API reference

## Installation

```bash
pip install kyvos-sm-skills
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

```python
from kyvos_sm_skills.generators.connection_json import generate_connection_json
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.models import TableSpec, ColumnSpec, MeasureSpec, HierarchySpec
from kyvos_sm_skills.generators.drd_xml import SimpleRel

# 1. Generate a connection payload
conn = generate_connection_json(
    name="MyConnection",
    host="localhost",
    port=5432,
    database="mydb",
    username="user",
    password="pass",
)

# 2. Generate dataset payloads
gen = DatasetJsonGenerator(connection_name="MyConnection")
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
dataset_payload = gen.generate_json_payload(table)

# 3. Generate DRD
drd_gen = DrdJsonGenerator(drd_folder_id="folder_123", drd_folder_name="DRDs")
drd_payload = drd_gen.generate(
    drd_name="SalesDRD",
    dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002"},
    relationships=[
        SimpleRel("fact_sales", "product_key", "dim_product", "product_key"),
    ],
    dataset_aliases={"fact_sales": "FactSales", "dim_product": "DimProduct"},
)

# 4. Generate semantic model
sm_gen = SModelJsonGenerator(
    folder_id="folder_456",
    folder_name="SMs",
    smodel_name="SalesModel",
    connection_name="MyConnection",
    drd_id="drd_001",
    drd_name="SalesDRD",
    dataset_name_to_id={"FactSales": "ds_001", "DimProduct": "ds_002"},
    dataset_columns={
        "FactSales": [{"name": "amount", "dataType": "NUMBER"}],
        "DimProduct": [{"name": "product_name", "dataType": "CHAR"}],
    },
    semantic_measures=[
        MeasureSpec(name="Total Sales", expression="", source_dataset="fact_sales", source_column="amount", aggregation_type="sum"),
    ],
    fact_dataset_names={"FactSales"},
    connected_dim_names={"DimProduct"},
)
sm_payload = sm_gen.generate()
```

## Claude Skills

The `skills/` directory contains 7 markdown skill files that can be loaded into Claude or any LLM:

| Skill | Description |
|-------|-------------|
| `generate-semantic-model.md` | Master skill: schema + relationships + measures → complete SM |
| `generate-dataset.md` | Table spec → Kyvos dataset payload |
| `generate-drd.md` | Relationships → DRD payload |
| `generate-connection.md` | DB params → connection payload |
| `convert-dax-to-mdx.md` | DAX expressions → MDX |
| `design-star-schema.md` | Domain description → star schema |
| `design-measures.md` | Schema + domain → measures with MDX |

See `docs/claude-skill-usage.md` for detailed usage instructions.

## Documentation

- [Quickstart](docs/quickstart.md)
- [Tutorials](docs/tutorials/)
- [API Reference](docs/api-reference.md)
- [Sample Gallery](docs/sample-gallery.md)
- [Claude Skill Usage](docs/claude-skill-usage.md)

## License

Apache 2.0 — See [LICENSE](LICENSE)
