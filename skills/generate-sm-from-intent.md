# Skill: Generate SM from Intent

> **Reference:** `skills/_shared/sm-design-principles.md` — canonical enterprise-quality mandate, supported schema types, and design principles. This skill follows those principles.

## System Prompt

You are a Kyvos enterprise semantic model architect. Given a user's analytics intent and domain description, you conduct deep domain research (web search for industry-standard data models, KPIs, and schema patterns) before designing one or more semantic models from scratch. Your recommendations must be enterprise-quality — production-grade schemas with proper conformed dimensions, industry-standard measure definitions, correct fact table granularity, and named hierarchies that reflect real business rollups. For each SM, you choose the appropriate schema type (single table, star, snowflake, or multifact) based on domain research findings and best practices. You balance simplicity against normalization — avoid over-engineering when a simpler schema suffices, but do not compromise on enterprise standards when the domain requires them. You present your research findings and design recommendations to the user for approval before proceeding to data generation and deployment. After approval, you generate synthetic data, load it into the warehouse, and deploy the SM(s) to Kyvos.

You understand:
- Credentials are never in skill inputs — all config comes from `.env`
- `allow_web_research` controls whether domain details leave the local environment via web search
- User approval is required at 3 gates before proceeding
- Enterprise-quality mandate: see `skills/_shared/sm-design-principles.md`
- Data generation uses `kyvos_data_gen.generate_data()` which produces CSV files
- CSV files are loaded into the warehouse via SQLAlchemy `to_sql` or bulk-load
- Data volume is capped by `KYVOS_DEFAULT_SCALE` with explicit user override
- Deployment reuses the same SDK building blocks as `deploy-from-xmla`

## Input Schema

```json
{
  "user_intent": "string describing what analytics the user wants",
  "domain": "retail_banking|healthcare|retail_ecommerce|telecom|adventure_works|custom",
  "env_file": "string (default .env) — path to the .env config file",
  "allow_web_research": "bool (default true) — when false, domain research uses built-in knowledge only",
  "scale": "int (target fact rows, default 100000) — capped by KYVOS_DEFAULT_SCALE",
  "sm_hints": {
    "max_sms": "int (optional, e.g. 2 — prefer at most 2 SMs)",
    "preferred_schema_type": "single_table|star|snowflake|multifact (optional, applied when compatible with domain)",
    "complexity_preference": "simple|balanced|comprehensive (optional — simple favors fewer SMs and simpler schemas, comprehensive favors full normalization)"
  },
  "sm_overrides": {
    "force_sms": [
      {
        "name": "string",
        "schema_type": "single_table|star|snowflake|multifact",
        "tables": ["string"],
        "measures": [{"name": "string", "source_dataset": "string", "aggregation_type": "string"}]
      }
    ]
  }
}
```

**No credentials in inputs.** All warehouse connection parameters come from `.env`.

**Hints vs. overrides:**
- **Hints** (`sm_hints`): Soft guidance. The LLM considers them but may override based on domain knowledge.
- **Overrides** (`sm_overrides`): Hard constraints. The LLM must follow these exactly.
- If neither is provided, the LLM decides fully autonomously based on domain knowledge.

## Output Schema

```json
{
  "recommended_sms": [
    {
      "name": "string",
      "schema_type": "single_table|star|snowflake|multifact",
      "rationale": "why this schema type and grouping was chosen for this domain",
      "tables": ["table names included in this SM"],
      "relationships": [{"from_table": "string", "from_column": "string", "to_table": "string", "to_column": "string"}],
      "measures": [{"name": "string", "source_dataset": "string", "aggregation_type": "string"}],
      "hierarchies": [{"name": "string", "levels": ["string"], "source_dataset": "string"}]
    }
  ],
  "shared_dimensions": ["dimension table names shared across SMs, if multiple SMs"],
  "domain_research_summary": "summary of web search findings: industry-standard patterns, KPIs, and dimension structures identified for this domain",
  "domain_reasoning": "how domain research findings were applied to schema type and measure decisions",
  "data_generation_results": {
    "sms_generated": [{"name": "string", "csv_files": ["string"], "total_rows": "int"}],
    "total_rows": "int",
    "output_dir": "string"
  },
  "deployment_results": {
    "folder_id": "string",
    "connection_name": "string",
    "deployed_sms": [{"name": "string", "drd_name": "string", "smodel_name": "string"}]
  }
}
```

## Workflow (with user approval gates)

1. **Domain research phase** — LLM conducts web search on the domain, identifies industry-standard patterns, KPIs, and dimension structures. Presents research summary to user.
2. **⏸ User approval gate 1** — User reviews domain research findings and confirms/adjusts the analytical scope. LLM may ask clarifying questions (e.g., "Did you mean retail banking or investment banking?").
3. **SM design phase** — LLM recommends one or more SMs with schema types, tables, measures, hierarchies (as above). For each SM, uses `design-star-schema` skill → designs the schema (star, snowflake, multifact, or single table). Uses `design-measures` skill → designs enterprise-quality measures per SM.
4. **⏸ User approval gate 2** — User reviews the full SM design (schema types, table structures, measures, hierarchies, relationships). User can request changes (add/remove measures, change schema type, add dimensions). LLM iterates until user approves.
5. **Data generation phase** — Build `DomainDemoSpec` per SM from approved design. Call `kyvos_data_gen.generate_data(spec)` per SM → generate CSV files.
6. **⏸ User approval gate 3** — User reviews data generation results (row counts, data quality summary). User confirms before loading to warehouse.
7. **Load + deploy phase** — Load CSVs into warehouse (via SQLAlchemy). Run deployment pipeline per SM (same as `deploy-from-xmla` steps 3-7).
8. **Final report** — Claude reports: domain research summary, schema types chosen, SMs deployed, data generated, deployment results.

## Backend

### Step 1: Load config and enforce scale guardrail

```python
from kyvos_sdk.config import KyvosConfig

config = KyvosConfig.from_env_file(env_file)

# Enforce data volume guardrail
effective_scale = min(scale, config.default_scale)
if scale > config.default_scale:
    print(
        f"⚠️  Requested scale {scale} exceeds KYVOS_DEFAULT_SCALE cap "
        f"({config.default_scale}). Using {effective_scale}. "
        f"Set KYVOS_DEFAULT_SCALE in .env to raise the cap."
    )
```

### Step 2: Domain research (LLM — web search if allow_web_research is true)

The LLM conducts web search on the specified domain. When `allow_web_research` is `false`, uses built-in knowledge only — no domain details leave the local environment via web search. Present findings at Gate 1.

### Step 3: SM design (LLM — uses design-star-schema and design-measures skills)

The LLM recommends SMs based on domain research + user intent + `sm_hints`/`sm_overrides`. Present recommendations at Gate 2.

### Step 4: Build DomainDemoSpec per approved SM

```python
def build_spec_from_recommendation(
    sm_rec: dict,
    domain: str,
    scale: int,
) -> "DomainDemoSpec":
    """Construct a DomainDemoSpec from an LLM SM recommendation dict.

    Args:
        sm_rec: Approved SM recommendation with name, schema_type, tables,
                relationships, measures, hierarchies.
        domain: Domain vertical string (e.g., 'telecom').
        scale: Target fact row count.

    Returns:
        DomainDemoSpec suitable for kyvos_data_gen.generate_data().
    """
    from kyvos_data_gen.models import (
        DomainDemoSpec, TableSpec, ColumnSpec,
        SemanticModelSpec, RelationshipSpec, MeasureSpec, HierarchySpec,
        DomainVertical, DatabaseBackend,
    )

    # Build table specs from the SM recommendation
    selected_tables = []
    for table_def in sm_rec.get("table_definitions", []):
        columns = [
            ColumnSpec(
                name=c["name"],
                data_type=c["data_type"],
                is_primary_key=c.get("is_primary_key", False),
                is_foreign_key=c.get("is_foreign_key", False),
                references=c.get("references"),
            )
            for c in table_def.get("columns", [])
        ]
        row_count = scale if table_def.get("table_type") == "fact" else None
        selected_tables.append(TableSpec(
            name=table_def["name"],
            table_type=table_def.get("table_type", "dimension"),
            columns=columns,
            row_count_target=row_count,
            is_hidden=False,
        ))

    # Build relationships
    relationships = [
        RelationshipSpec(
            left_dataset=r["from_table"],
            left_column=r["from_column"],
            right_dataset=r["to_table"],
            right_column=r["to_column"],
            relationship_type="many_to_one",
        )
        for r in sm_rec.get("relationships", [])
    ]

    # Build measures
    measures = [
        MeasureSpec(
            name=m["name"],
            source_dataset=m["source_dataset"],
            aggregation_type=m.get("aggregation_type", "sum"),
        )
        for m in sm_rec.get("measures", [])
    ]

    # Build hierarchies
    hierarchies = [
        HierarchySpec(
            name=h["name"],
            levels=h["levels"],
            source_dataset=h["source_dataset"],
        )
        for h in sm_rec.get("hierarchies", [])
    ]

    semantic_model = SemanticModelSpec(
        name=sm_rec["name"],
        relationships=relationships,
        measures=measures,
        hierarchies=hierarchies,
    )

    # Map domain string to DomainVertical enum
    domain_map = {
        "retail_banking": DomainVertical.RETAIL_BANKING,
        "healthcare": DomainVertical.HEALTHCARE,
        "retail_ecommerce": DomainVertical.RETAIL,
        "telecom": DomainVertical.TELECOM,
        "adventure_works": DomainVertical.RETAIL,
        "custom": DomainVertical.RETAIL_BANKING,
    }

    return DomainDemoSpec(
        raw_instruction=sm_rec.get("rationale", ""),
        domain=domain_map.get(domain, DomainVertical.RETAIL_BANKING),
        scale=scale,
        database=DatabaseBackend.POSTGRES,
        tables=selected_tables,
        semantic_model=semantic_model,
        metadata={"source": "generate-sm-from-intent", "schema_type": sm_rec["schema_type"]},
    )
```

### Step 5: Generate synthetic data per SM

```python
from kyvos_data_gen import generate_data, DataGenerationResult
import pandas as pd
from pathlib import Path

generation_results = []
for sm_rec in recommended_sms:
    spec = build_spec_from_recommendation(sm_rec, domain, effective_scale)
    result = generate_data(spec)

    print(f"  SM '{sm_rec['name']}': {result.total_rows} rows, {len(result.csv_paths)} CSV files")
    for table_name, csv_path in result.csv_map.items():
        print(f"    {table_name} → {csv_path} ({result.row_counts.get(table_name, '?')} rows)")

    generation_results.append({
        "sm_name": sm_rec["name"],
        "result": result,
        "spec": spec,
    })

# Present generation results to user at Gate 3
```

### Step 6: Load CSVs into warehouse via SQLAlchemy

```python
from kyvos_sdk.warehouse_registry import build_sqlalchemy_url
from sqlalchemy import create_engine, text
import pandas as pd

sa_url = build_sqlalchemy_url(
    config.warehouse_type,
    config.warehouse_host,
    config.warehouse_port,
    config.warehouse_database,
    config.warehouse_username,
    config.warehouse_password,
    **config.warehouse_extra_params,
)
engine = create_engine(sa_url)
schema = config.warehouse_schema or {
    "POSTGRES": "public",
    "SNOWFLAKE": "PUBLIC",
    "MSSQL": "dbo",
    "REDSHIFT": "public",
}.get(config.warehouse_type, "public")

for gen_result in generation_results:
    spec = gen_result["spec"]
    for table in spec.tables:
        fqn = f"{table.schema_name}.{table.name}"
        csv_path = gen_result["result"].csv_map.get(fqn)
        if not csv_path or not Path(csv_path).exists():
            print(f"  ⚠️  No CSV for {fqn}, skipping load")
            continue

        df = pd.read_csv(csv_path)

        # Bulk load via SQLAlchemy to_sql
        df.to_sql(
            table.name,
            engine,
            schema=schema,
            if_exists="replace",
            index=False,
            chunksize=10000,
        )
        print(f"  Loaded {fqn}: {len(df)} rows")

engine.dispose()
```

**Per-warehouse-type load strategies:**

| Warehouse Type | Strategy | Notes |
|---------------|----------|-------|
| POSTGRES | `to_sql` with `chunksize=10000` | For large loads, use `COPY` via raw connection: `conn = engine.raw_connection(); cursor = conn.cursor(); cursor.copy_expert(...)` |
| SNOWFLAKE | `to_sql` then `PUT` + `COPY INTO` | `to_sql` works but is slow for large datasets; use Snowflake's bulk loader for production |
| BIGQUERY | `to_sql` via `pandas_gbq` or `load_data_from_dataframe` | BigQuery requires `google-cloud-bigquery` for bulk load |
| ORACLE | `to_sql` with `chunksize=5000` | Oracle has array binding limits; use smaller chunks |
| MSSQL | `to_sql` with `chunksize=10000` | Use `BULK INSERT` via raw connection for large datasets |
| REDSHIFT | `to_sql` then `COPY` from S3 | Redshift `to_sql` is slow; use S3 + `COPY` for production |

### Step 7: Deploy each SM to Kyvos (reuses deploy-from-xmla steps)

```python
from kyvos_sdk.client import KyvosService
from kyvos_sdk.provisioning import ProvisioningClient, FolderType
from kyvos_sdk.warehouse_registry import build_jdbc_url, get_warehouse_profile
from kyvos_sm_skills.contract_adapter import (
    compile_dataset_artifact,
    compile_drd_artifact,
    compile_smodel_artifact,
)

svc = KyvosService(config=config)
svc.initialize()
prov = ProvisioningClient(svc)

# Create folder
folder_result = prov.create_folder(config.folder_name, FolderType.RDATASET)
if not folder_result.succeeded:
    raise RuntimeError(
        f"Folder creation failed: {[d.message for d in folder_result.diagnostics]}"
    )
folder_id = folder_result.primary_entity_id

# Create connection
jdbc_url = config.warehouse_jdbc_url or build_jdbc_url(
    config.warehouse_type, config.warehouse_host, config.warehouse_port,
    config.warehouse_database, **config.warehouse_extra_params,
)
driver = config.warehouse_driver or get_warehouse_profile(config.warehouse_type).driver_class
db_version = config.warehouse_db_version or get_warehouse_profile(config.warehouse_type).db_version_default

conn_result = prov.create_connection(
    name=config.warehouse_connection_name,
    host=config.warehouse_host, port=config.warehouse_port,
    database=config.warehouse_database,
    username=config.warehouse_username, password=config.warehouse_password,
    db_type=config.warehouse_type, db_version=db_version,
    use_json=(config.payload_format == "json"),
    jdbc_url_override=jdbc_url, driver_override=driver,
)
if not conn_result.succeeded:
    raise RuntimeError(
        f"Connection creation failed: {[d.message for d in conn_result.diagnostics]}"
    )

# Deploy each SM
deployed_sms = []
for gen_result in generation_results:
    spec = gen_result["spec"]
    sm_name = gen_result["sm_name"]

    # Create datasets
    dataset_name_to_id = {}
    for table in spec.tables:
        ds_result = prov.create_dataset(
            table, config.warehouse_connection_name, folder_id, config.folder_name,
            use_json=(config.payload_format == "json"),
        )
        if not ds_result.succeeded:
            raise RuntimeError(
                f"Dataset creation failed for {table.name}: "
                f"{[d.message for d in ds_result.diagnostics]}"
            )
        dataset_name_to_id[table.name] = ds_result.primary_entity_id
        prov.refresh_dataset_columns(ds_result.primary_entity_id)

    # Create DRD
    drd_name = f"{sm_name}DRD"
    drd_id = f"drd_{sm_name}"

    drd_artifact = compile_drd_artifact(
        drd_name=drd_name, drd_id=drd_id,
        folder_id=folder_id, folder_name=config.folder_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=spec.semantic_model.relationships,
        fmt=config.payload_format,
    )
    drd_result = prov.apply_artifact(drd_artifact)
    if not drd_result.succeeded:
        raise RuntimeError(
            f"DRD creation failed: {[d.message for d in drd_result.diagnostics]}"
        )

    # Create semantic model
    sm_artifact = compile_smodel_artifact(
        spec.semantic_model,
        drd_name=drd_name, drd_id=drd_id,
        folder_id=folder_id, folder_name=config.folder_name,
        connection_name=config.warehouse_connection_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=spec.semantic_model.relationships,
        fmt=config.payload_format,
    )
    sm_result = prov.apply_artifact(sm_artifact)
    if not sm_result.succeeded:
        raise RuntimeError(
            f"Semantic model creation failed: {[d.message for d in sm_result.diagnostics]}"
        )

    deployed_sms.append({
        "name": sm_name,
        "drd_name": drd_name,
        "smodel_name": sm_name,
    })
    print(f"  SM deployed: {sm_name}")
```

### Step 8: Report results

```python
result = {
    "recommended_sms": recommended_sms,
    "shared_dimensions": shared_dimensions,
    "domain_research_summary": domain_research_summary,
    "domain_reasoning": domain_reasoning,
    "data_generation_results": {
        "sms_generated": [
            {"name": gr["sm_name"], "csv_files": gr["result"].csv_paths, "total_rows": gr["result"].total_rows}
            for gr in generation_results
        ],
        "total_rows": sum(gr["result"].total_rows for gr in generation_results),
    },
    "deployment_results": {
        "folder_id": folder_id,
        "connection_name": config.warehouse_connection_name,
        "deployed_sms": deployed_sms,
    },
}
print(f"\n✅ Generation + Deployment Complete")
print(f"   Domain: {domain}")
print(f"   SMs deployed: {len(deployed_sms)}")
for sm in deployed_sms:
    print(f"     - {sm['name']}")
print(f"   Total rows generated: {result['data_generation_results']['total_rows']}")
```

## Error Handling

Same pattern as `deploy-from-xmla`:
1. **Halt immediately** on any step failure
2. **Report diagnostics** from `result.diagnostics`
3. **List created-so-far entities** for resume or cleanup
4. **Do not retry** — the SDK's `max_retries` handles transient failures

## Example Interactions

### Telecom churn analysis

> "I want to analyze customer churn for a telecom company. Generate data and deploy a semantic model. My .env is at `/path/to/.env`."

Claude runs domain research on telecom → Gate 1 → designs star schema with `fact_churn`, `dim_customer`, `dim_plan`, `dim_date` → Gate 2 → generates 100K rows → Gate 3 → loads to PostgreSQL → deploys to Kyvos.

### Healthcare with web research disabled

> "Design a healthcare analytics SM for patient outcomes. Don't send any data to the web — use built-in knowledge only."

Claude sets `allow_web_research=false`, uses built-in healthcare domain knowledge, designs schema, generates data, loads, deploys.

### Multifact with overrides

> "Force a multifact SM with fact_sales and fact_inventory sharing dim_product and dim_date. Generate 500K rows."

Claude applies `sm_overrides.force_sms` with the specified multifact schema, generates 500K rows (if within `KYVOS_DEFAULT_SCALE` cap), loads, deploys.

## Dependencies

```bash
pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-data-gen kyvos-xmla-parser sqlalchemy pandas
# Plus the driver package for your warehouse type
```
