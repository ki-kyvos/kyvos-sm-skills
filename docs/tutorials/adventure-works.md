# Tutorial: Adventure Works Semantic Model

## Overview

This tutorial walks through generating a Kyvos semantic model for the classic Adventure Works internet sales scenario.

## Prerequisites

```bash
pip install kyvos-sm-skills
```

## Step 1: Define the Schema

```python
from kyvos_sm_skills.models import TableSpec, ColumnSpec

tables = [
    TableSpec(
        name="dim_product",
        schema_name="adventure_works",
        table_type="dimension",
        columns=[
            ColumnSpec(name="product_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="product_name", data_type="VARCHAR(200)"),
            ColumnSpec(name="category", data_type="VARCHAR(100)"),
            ColumnSpec(name="subcategory", data_type="VARCHAR(100)"),
            ColumnSpec(name="color", data_type="VARCHAR(50)"),
            ColumnSpec(name="list_price", data_type="NUMERIC(12,2)"),
        ],
    ),
    TableSpec(
        name="dim_date",
        schema_name="adventure_works",
        table_type="dimension",
        columns=[
            ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
            ColumnSpec(name="year", data_type="INTEGER"),
            ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
            ColumnSpec(name="month", data_type="VARCHAR(10)"),
            ColumnSpec(name="month_name", data_type="VARCHAR(20)"),
        ],
    ),
    TableSpec(
        name="fact_internet_sales",
        schema_name="adventure_works",
        table_type="fact",
        columns=[
            ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
            ColumnSpec(name="product_fk", data_type="INTEGER", is_foreign_key=True,
                       references="adventure_works.dim_product.product_pk"),
            ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True,
                       references="adventure_works.dim_date.date_pk"),
            ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
            ColumnSpec(name="order_quantity", data_type="INTEGER"),
            ColumnSpec(name="unit_price", data_type="NUMERIC(12,2)"),
            ColumnSpec(name="discount_pct", data_type="NUMERIC(7,4)"),
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
    name="AdventureWorksConnection",
    host="localhost", port=5432, database="adventureworks",
    username="demo_user", password="demo_pass",
)

# Datasets
ds_gen = DatasetJsonGenerator(connection_name="AdventureWorksConnection")
for table in tables:
    ds_gen.generate_json_payload(table)

# DRD
drd_gen = DrdJsonGenerator(drd_folder_id="folder_001", drd_folder_name="DRDs")
drd = drd_gen.generate(
    drd_name="AdventureWorksDRD",
    dataset_name_to_id={"DimProduct": "ds_001", "DimDate": "ds_002", "FactInternetSales": "ds_003"},
    relationships=[
        SimpleRel("fact_internet_sales", "product_fk", "dim_product", "product_pk"),
        SimpleRel("fact_internet_sales", "date_fk", "dim_date", "date_pk"),
    ],
    dataset_aliases={"fact_internet_sales": "FactInternetSales", "dim_product": "DimProduct", "dim_date": "DimDate"},
    fact_dataset_names={"FactInternetSales"},
)

# Semantic Model
sm_gen = SModelJsonGenerator(
    folder_id="folder_002", folder_name="SMs",
    smodel_name="AdventureWorksModel",
    connection_name="AdventureWorksConnection",
    drd_id="drd_001", drd_name="AdventureWorksDRD",
    dataset_name_to_id={"DimProduct": "ds_001", "DimDate": "ds_002", "FactInternetSales": "ds_003"},
    dataset_columns={
        "FactInternetSales": [{"name": "sales_amount", "datatype": "NUMBER"}, {"name": "order_quantity", "datatype": "NUMBER"}, {"name": "unit_price", "datatype": "NUMBER"}, {"name": "discount_pct", "datatype": "NUMBER"}],
        "DimProduct": [{"name": "category", "datatype": "CHAR"}, {"name": "subcategory", "datatype": "CHAR"}, {"name": "color", "datatype": "CHAR"}],
        "DimDate": [{"name": "year", "datatype": "CHAR"}, {"name": "quarter", "datatype": "CHAR"}, {"name": "month", "datatype": "CHAR"}],
    },
    hierarchy_specs=[
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Product Category", levels=["category", "subcategory", "color"], source_dataset="DimProduct"),
    ],
    semantic_measures=[
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactInternetSales", source_column="sales_amount", aggregation_type="sum"),
        MeasureSpec(name="Total Order Quantity", expression="", source_dataset="FactInternetSales", source_column="order_quantity", aggregation_type="sum"),
        MeasureSpec(name="Avg Unit Price", expression="", source_dataset="FactInternetSales", source_column="unit_price", aggregation_type="average"),
        MeasureSpec(name="Avg Discount", expression="", source_dataset="FactInternetSales", source_column="discount_pct", aggregation_type="average"),
        MeasureSpec(name="Revenue per Unit", expression="[Measures].[Total Sales] / [Measures].[Total Order Quantity]", is_calculated=True, source_dataset="FactInternetSales"),
    ],
    fact_dataset_names={"FactInternetSales"},
    connected_dim_names={"DimProduct", "DimDate"},
)
payload = sm_gen.generate()
```

## Result

An Adventure Works semantic model with 2 dimensions, 1 fact, 5 measures (4 base + 1 calculated), and 2 hierarchies including a 3-level product category hierarchy.

---

## Discover Flow: Intent File vs Generate Intent

The `kyvos-skills discover` CLI supports two modes for designing a semantic model from a warehouse schema. This section covers both approaches and how to compare their outputs.

### Prerequisites

```bash
pip install kyvos-sm-skills[sdk] kyvos-sdk-python[env]
```

Ensure `.env` is configured:
```ini
KYVOS_BASE_URL=https://your-kyvos-server
KYVOS_WAREHOUSE_TYPE=POSTGRES
KYVOS_WAREHOUSE_HOST=localhost
KYVOS_WAREHOUSE_PORT=5432
KYVOS_WAREHOUSE_DATABASE=adventureworks
KYVOS_WAREHOUSE_USERNAME=your_user
KYVOS_WAREHOUSE_PASSWORD=your_pass
```

For LLM-based intent generation, also set:
```ini
ANTHROPIC_API_KEY=sk-ant-...
```

### Flow A: Intent File Mode

Uses a pre-written natural language intent document to guide the LLM in generating a semantic model design.

```bash
kyvos-skills discover \
  --env-file .env \
  --user-intent "$(cat intent-adventureworks.txt)" \
  --domain adventure_works \
  --auto-approve \
  --dry-run \
  --payload-format json
```

**Key files:**
- `intent-adventureworks.txt` — structured intent covering business context, fact tables, hierarchies, KPI requirements, and MDX syntax guidance
- `samples/adventureworks-sm-design.json` — pre-approved SM design (bypasses LLM if passed via `--sm-design`)

**With pre-approved SM design (skips LLM):**
```bash
kyvos-skills discover \
  --env-file .env \
  --sm-design samples/adventureworks-sm-design.json \
  --domain adventure_works \
  --auto-approve \
  --dry-run \
  --payload-format json
```

### Flow B: Generate Intent Mode

Auto-generates an intent document from warehouse schema analysis using an LLM, then uses that intent to generate the SM design.

```bash
kyvos-skills discover \
  --env-file .env \
  --generate-intent \
  --domain adventure_works \
  --auto-approve \
  --dry-run \
  --payload-format json \
  --intent-output generated-intent.txt
```

**What happens:**
1. Warehouse schema is inspected (`inspect_schema()`)
2. Schema summary + domain hint passed to `generate_intent_from_file()` which calls the LLM
3. Generated intent is saved to `generated-intent.txt`
4. Intent is passed to `design_sm_from_schema()` which calls the LLM again
5. SM design is validated against warehouse schema
6. Spec is built and compiled to Kyvos SM JSON

### Comparing Both Flows

Use the validation script to run both flows and compare:

```bash
# Mock mode (no warehouse needed)
python scripts/validate_adventureworks_discover.py \
  --mock-schema \
  --compare \
  --intent-file intent-adventureworks.txt \
  --domain adventure_works \
  --dry-run \
  --env-file /dev/null

# Live mode (real warehouse + Kyvos)
python scripts/validate_adventureworks_discover.py \
  --compare \
  --intent-file intent-adventureworks.txt \
  --domain adventure_works \
  --dry-run \
  --env-file .env
```

### Comparison Points

| Metric | Flow A (Intent File) | Flow B (Generate Intent) |
|--------|---------------------|-------------------------|
| Intent source | Manual, pre-written | LLM-generated from schema |
| Intent quality | High (domain expert) | Depends on schema + LLM |
| Table selection | Guided by intent | LLM infers from schema |
| Hierarchy levels | Explicit in intent | LLM infers from columns |
| Calculated KPIs | MDX expressions in intent | LLM generates MDX |
| Domain research | Built into intent | LLM web research |
| Reproducibility | High (fixed intent) | Variable (LLM-dependent) |

### Validation Checks

Both flows are validated against:

- **Top-level JSON structure**: `common.compatibilityVersion`, `specific.smObject`, `dimensions`, `measures`
- **Hierarchy conformity**: `defaultMemberUniqueName`, `displayFolder` on every hierarchy
- **Level conformity**: `dateDataType`, `dateFormat`, `format`, `fieldDataType` on every level
- **Measure conformity**: `actualSummaryFunction` on every measure
- **MDX validation**: Calculated measures use Kyvos MDX (no DAX functions)
- **Schema validation**: `validate_sm_recommendation()` returns zero errors
- **Diagnostics**: No blocking diagnostics (`NO_MEASURES_PLACED`, `MISSING_DATASET_ID`, `PC_DATA_TYPE_MISMATCH`)

### Automated Tests

```bash
# Run the E2E comparison test suite
.venv/bin/python -m pytest tests/test_adventureworks_e2e_comparison.py -v

# Run all tests
.venv/bin/python -m pytest tests/ -v
```

### Troubleshooting

- **`ValueError: Anthropic API key required`**: Set `ANTHROPIC_API_KEY` in `.env` or environment
- **`ValueError: table 'X' not found in warehouse schema`**: SM design references a table not in the warehouse — check table names match exactly (case-insensitive)
- **`NO_MEASURES_PLACED` diagnostic**: Measure `source_dataset` names don't match dataset names after alias remapping — check `dataset_aliases` mapping
- **MDX validation warnings**: Calculated measure expressions use DAX functions — replace with MDX equivalents (see [Kyvos MDX Functions Guide](https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1232535557/Kyvos+MDX+Functions+Guide))
