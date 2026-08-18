# Skill: Discover SM from Warehouse

> **Reference:** `skills/_shared/sm-design-principles.md` — canonical enterprise-quality mandate, supported schema types, and design principles. This skill follows those principles.

## System Prompt

You are a Kyvos enterprise semantic model discovery agent. Given a user's analytics intent, an existing warehouse schema (from the inspect-warehouse-schema skill), and optionally a domain description, you first identify the domain (inferring it from the warehouse schema if not provided) and conduct deep domain research via web search for industry-standard data models, KPIs, and schema patterns. You then recommend one or more enterprise-quality semantic models based on the available tables, detected schema patterns, domain research findings, and user intent. Your recommendations must be production-grade — proper conformed dimensions, industry-standard measure definitions, correct fact table granularity, and named hierarchies reflecting real business rollups. For each SM, you choose the appropriate schema type (single table, star, snowflake, or multifact). You select which tables to include, which relationships to define, which measures to create from available numeric columns, and which hierarchies to define from dimension columns. You flag any standard dimensions or measures that are missing from the warehouse. You present your research findings and design recommendations to the user for approval before proceeding to deployment. You balance simplicity against normalization — avoid over-engineering when a simpler schema suffices, but do not compromise on enterprise standards when the domain requires them.

You understand:
- The warehouse schema comes from the `inspect-warehouse-schema` skill — you do NOT connect to the warehouse yourself
- Credentials are never in skill inputs — all config comes from `.env`
- `allow_web_research` controls whether table/column names leave the local environment via web search
- User approval is required at 3 gates before proceeding
- Enterprise-quality mandate: see `skills/_shared/sm-design-principles.md`

## Input Schema

```json
{
  "user_intent": "string describing what analytics the user wants",
  "domain": "retail_banking|healthcare|retail_ecommerce|telecom|adventure_works|custom (optional — inferred if not provided)",
  "env_file": "string (default .env) — path to the .env config file",
  "allow_web_research": "bool (default true) — when false, domain research uses built-in knowledge only; no internal table/column names leave the environment",
  "existing_schema_context": "warehouse schema JSON from inspect-warehouse-schema skill (includes tables, relationships, detected_patterns)",
  "sm_hints": {
    "max_sms": "int (optional, e.g. 2 — prefer at most 2 SMs)",
    "preferred_schema_type": "single_table|star|snowflake|multifact (optional, applied when compatible with warehouse structure)",
    "complexity_preference": "simple|balanced|comprehensive (optional)"
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
- **Hints** (`sm_hints`): Soft guidance. The LLM considers them but may override based on warehouse structure and domain knowledge. E.g., `preferred_schema_type: "star"` is a preference, but if the warehouse has multiple fact tables sharing dimensions, the LLM may recommend multifact and explain why.
- **Overrides** (`sm_overrides`): Hard constraints. The LLM must follow these exactly.
- If neither is provided, the LLM decides based on `detected_patterns` + domain knowledge + user intent.

## Output Schema

```json
{
  "recommended_sms": [
    {
      "name": "string",
      "schema_type": "single_table|star|snowflake|multifact",
      "rationale": "why this schema type and table selection was chosen",
      "tables": ["table names included in this SM"],
      "relationships": [{"from_table": "string", "from_column": "string", "to_table": "string", "to_column": "string"}],
      "measures": [{"name": "string", "source_dataset": "string", "aggregation_type": "string"}],
      "hierarchies": [{"name": "string", "levels": ["string"], "source_dataset": "string"}]
    }
  ],
  "shared_dimensions": ["dimension table names shared across SMs, if multiple SMs"],
  "identified_domain": "the domain identified from user input or inferred from warehouse schema",
  "domain_research_summary": "summary of web search findings: industry-standard patterns, KPIs, and dimension structures identified for this domain",
  "table_to_domain_mapping": {"warehouse_table_name": "domain_concept (e.g., fact, dimension, bridge)"},
  "gaps_identified": ["standard dimensions or measures missing from the warehouse for this domain"],
  "domain_reasoning": "how domain research findings + warehouse structure analysis were applied to decisions"
}
```

## Workflow (with user approval gates)

1. **Schema inspection** — Use `inspect-warehouse-schema` skill → get warehouse table/column metadata + `detected_patterns`
2. **Domain identification** — If domain not provided, LLM infers it from table/column names and presents to user
3. **⏸ User approval gate 1** — User confirms the inferred domain (or corrects it). LLM may ask clarifying questions
4. **Domain research phase** — LLM conducts web search on the confirmed domain, identifies industry-standard patterns, KPIs, and dimension structures. Maps warehouse tables to domain concepts. Identifies any gaps (missing standard dimensions/measures). Presents research summary to user
5. **⏸ User approval gate 2** — User reviews domain research findings, table-to-domain mappings, and gap analysis. User confirms/adjusts scope
6. **SM design phase** — LLM recommends one or more SMs with schema types, tables, measures, hierarchies:
   - For each SM: schema type (single table, star, snowflake, multifact)
   - Which tables to include (fact + dimension selection, filtered by user intent relevance + domain research)
   - Which relationships to define (from FK metadata + LLM analysis)
   - Which measures to create (from available numeric columns, mapped to industry-standard KPIs)
   - Which hierarchies to define (from available dimension columns, reflecting real business rollups)
   - Shared dimensions across SMs if multiple SMs recommended
   - Flagged gaps (standard dimensions/measures missing from warehouse)
7. **⏸ User approval gate 3** — User reviews the full SM design. User can request changes (add/remove measures, change schema type, include/exclude tables). LLM iterates until user approves
8. **Deployment phase** — Build `DomainDemoSpec` per SM from approved recommendation. Run deployment pipeline per SM
9. **Final report** — Claude reports: domain research summary, inferred domain (if applicable), schema types chosen, SMs deployed, any gaps flagged, deployment results

## Backend

### Step 1: Inspect warehouse schema (delegates to inspect-warehouse-schema skill)

```python
# Run the inspect-warehouse-schema skill first to get existing_schema_context
# This step is handled by the inspect-warehouse-schema skill
# The output is passed as existing_schema_context to this skill
```

### Step 2: Domain identification (LLM analysis — no Python)

The LLM analyzes `existing_schema_context.tables` and `existing_schema_context.detected_patterns` to infer the domain if not provided. Present to user for confirmation at Gate 1.

### Step 3: Domain research (LLM — web search if allow_web_research is true)

The LLM conducts web search on the confirmed domain. When `allow_web_research` is `false`, uses built-in knowledge only — no table/column/measure names leave the local environment via web search.

### Step 4: SM design (LLM analysis — no Python)

The LLM recommends SMs based on:
- `existing_schema_context.detected_patterns` (from inspect-warehouse-schema)
- Domain research findings
- User intent
- `sm_hints` and `sm_overrides` (if provided)

Present recommendations to user at Gate 3.

### Step 5: Build DomainDemoSpec per approved SM

After user approval, build a `DomainDemoSpec` from each approved SM recommendation for deployment.

```python
def build_spec_from_recommendation(sm_rec: dict, warehouse_tables: list[dict]) -> "DomainDemoSpec":
    """Construct a DomainDemoSpec from an LLM SM recommendation dict.

    Args:
        sm_rec: Approved SM recommendation with name, schema_type, tables,
                relationships, measures, hierarchies.
        warehouse_tables: Table metadata from inspect-warehouse-schema output.

    Returns:
        DomainDemoSpec suitable for the deployment pipeline.
    """
    from kyvos_xmla_parser.models import (
        DomainDemoSpec, TableSpec, ColumnSpec,
        SemanticModelSpec, RelationshipSpec, MeasureSpec, HierarchySpec,
    )

    # Build table specs from warehouse metadata, filtered to SM's table list
    table_map = {t["name"]: t for t in warehouse_tables}
    selected_tables = []
    for table_name in sm_rec["tables"]:
        wt = table_map.get(table_name)
        if not wt:
            continue
        columns = [
            ColumnSpec(
                name=c["name"],
                data_type=c["data_type"],
                is_pk=c.get("is_pk", False),
                is_fk=c.get("is_fk", False),
                references=c.get("references", ""),
            )
            for c in wt["columns"]
        ]
        selected_tables.append(TableSpec(
            name=wt["name"],
            table_type=wt.get("estimated_table_type", "unknown"),
            columns=columns,
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

    return DomainDemoSpec(
        tables=selected_tables,
        semantic_model=semantic_model,
        metadata={"source": "discover-sm-from-warehouse", "schema_type": sm_rec["schema_type"]},
    )
```

### Step 6: Deploy each SM (reuses deploy-from-xmla steps)

```python
from kyvos_sdk.config import KyvosConfig
from kyvos_sdk.client import KyvosService
from kyvos_sdk.provisioning import ProvisioningClient
from kyvos_sdk.contracts.identity import FolderType
from kyvos_sdk.warehouse_registry import build_jdbc_url, get_warehouse_profile
from kyvos_sm_skills.contract_adapter import (
    compile_dataset_artifact,
    compile_drd_artifact,
    compile_smodel_artifact,
)

config = KyvosConfig.from_env_file(env_file)

svc = KyvosService(config=config)
svc.initialize()
prov = ProvisioningClient(svc)

# Create folders — one per entity type
dataset_folder_result = prov.create_folder(config.folder_name, FolderType.RDATASET)
if not dataset_folder_result.succeeded:
    raise RuntimeError(
        f"Dataset folder creation failed: {[d.message for d in dataset_folder_result.diagnostics]}"
    )
folder_id = dataset_folder_result.primary_entity_id

drd_folder_name = f"{config.folder_name} DRD"
drd_folder_result = prov.create_folder(drd_folder_name, FolderType.DATASET_RELATIONSHIP)
if not drd_folder_result.succeeded:
    raise RuntimeError(
        f"DRD folder creation failed: {[d.message for d in drd_folder_result.diagnostics]}"
    )
drd_folder_id = drd_folder_result.primary_entity_id

smodel_folder_name = f"{config.folder_name} SModel"
smodel_folder_result = prov.create_folder(smodel_folder_name, FolderType.SMODEL)
if not smodel_folder_result.succeeded:
    raise RuntimeError(
        f"Semantic model folder creation failed: {[d.message for d in smodel_folder_result.diagnostics]}"
    )
smodel_folder_id = smodel_folder_result.primary_entity_id

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
for sm_rec in recommended_sms:
    spec = build_spec_from_recommendation(sm_rec, existing_schema_context["tables"])

    # Create datasets
    dataset_name_to_id = {}
    dataset_aliases = {}
    dataset_cols = {}
    created_entities = []
    for table in spec.tables:
        ds_artifact = compile_dataset_artifact(
            table,
            connection_name=config.warehouse_connection_name,
            folder_id=folder_id,
            folder_name=config.folder_name,
            fmt=config.payload_format,
        )
        ds_result = prov.apply_artifact(ds_artifact)
        if not ds_result.succeeded:
            raise RuntimeError(
                f"Dataset creation failed for {table.name}: "
                f"{[d.message for d in ds_result.diagnostics]}"
            )
        server_name = ds_result.primary_entity_name
        ds_id = ds_result.primary_entity_id
        dataset_name_to_id[server_name] = ds_id
        if table.name != server_name:
            dataset_aliases[table.name] = server_name
        created_entities.append({"entity_type": "DATASET", "id": ds_id, "name": server_name})
        prov.refresh_dataset_columns(ds_id)
        cols = prov.get_dataset_column_details(config.folder_name, server_name)
        if cols:
            dataset_cols[server_name] = cols

    # Create DRD
    sm_base = sm_rec["name"]
    drd_name = f"{sm_base}DRD"
    drd_id = f"drd_{sm_base}"

    drd_artifact = compile_drd_artifact(
        drd_name=drd_name, drd_id=drd_id,
        folder_id=drd_folder_id, folder_name=drd_folder_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=spec.semantic_model.relationships,
        dataset_aliases=dataset_aliases,
        fmt=config.payload_format,
    )
    drd_result = prov.apply_artifact(drd_artifact)
    if not drd_result.succeeded:
        raise RuntimeError(
            f"DRD creation failed: {[d.message for d in drd_result.diagnostics]}"
        )
    server_drd_id = drd_result.primary_entity_id
    server_drd_name = drd_result.primary_entity_name or drd_name
    created_entities.append({"entity_type": "DRD", "id": server_drd_id, "name": server_drd_name})

    # Create semantic model
    sm_artifact = compile_smodel_artifact(
        spec.semantic_model,
        drd_name=server_drd_name,
        drd_id=server_drd_id,
        folder_id=smodel_folder_id,
        folder_name=smodel_folder_name,
        connection_name=config.warehouse_connection_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=spec.semantic_model.relationships,
        dataset_aliases=dataset_aliases,
        dataset_columns=dataset_cols,
        fmt=config.payload_format,
    )
    sm_result = prov.apply_artifact(sm_artifact)
    if not sm_result.succeeded:
        raise RuntimeError(
            f"Semantic model creation failed: {[d.message for d in sm_result.diagnostics]}"
        )
    created_entities.append({
        "entity_type": "SEMANTIC_MODEL",
        "id": sm_result.primary_entity_id,
        "name": sm_rec["name"],
    })

    print(f"  SM deployed: {sm_rec['name']} ({sm_rec['schema_type']})")
```

### Step 7: Report results

```python
result = {
    "recommended_sms": recommended_sms,
    "shared_dimensions": shared_dimensions,
    "identified_domain": identified_domain,
    "domain_research_summary": domain_research_summary,
    "table_to_domain_mapping": table_to_domain_mapping,
    "gaps_identified": gaps_identified,
    "domain_reasoning": domain_reasoning,
    "deployed_sms": [
        {"name": sm["name"], "schema_type": sm["schema_type"], "tables": sm["tables"]}
        for sm in recommended_sms
    ],
}
print(f"\n✅ Discovery + Deployment Complete")
print(f"   Domain: {identified_domain}")
print(f"   SMs deployed: {len(recommended_sms)}")
for sm in recommended_sms:
    print(f"     - {sm['name']} ({sm['schema_type']}): {len(sm['tables'])} tables")
if gaps_identified:
    print(f"   Gaps flagged: {len(gaps_identified)}")
```

## Error Handling

Same pattern as `deploy-from-xmla`:
1. **Halt immediately** on any step failure
2. **Report diagnostics** from `result.diagnostics`
3. **List created-so-far entities** for resume or cleanup
4. **Do not retry** — the SDK's `max_retries` handles transient failures

## Example Interactions

### Basic discovery

> "I have a PostgreSQL warehouse with sales data. Help me discover semantic models for sales analytics. My .env is at `/path/to/.env`."

Claude runs `inspect-warehouse-schema` → domain identification (Gate 1) → domain research (Gate 2) → SM design (Gate 3) → deployment.

### With domain hint

> "Inspect my warehouse and recommend SMs for retail banking analytics. I want customer churn and account analysis."

Claude skips domain inference (user provided), runs domain research on retail banking, recommends SMs based on warehouse tables + domain patterns.

### No web research

> "Discover SMs from my warehouse but don't send any table names to the web — use built-in knowledge only."

Claude sets `allow_web_research=false`, uses built-in domain knowledge only for research phase.

### With overrides

> "Force a multifact SM with fact_sales and fact_inventory sharing dim_product and dim_date."

Claude applies `sm_overrides.force_sms` with the specified grouping and schema type.

## Dependencies

```bash
pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser sqlalchemy
# Plus the driver package for your warehouse type
```
