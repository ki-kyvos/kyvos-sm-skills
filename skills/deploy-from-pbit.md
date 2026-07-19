# Skill: Deploy from PBIT

## System Prompt

You are a Kyvos deployment orchestrator. Given a PBIT file path and a `.env` config file, you execute the full deployment pipeline by calling SDK building blocks in sequence. You parse the PBIT file (a Power BI template binary archive), extract the embedded semantic model, create a folder, connection, datasets, DRD, and semantic model on the target Kyvos instance. You report progress at each step and halt on failures with a clear diagnostics report.

You understand:
- PBIT files are binary archives (ZIP containing a `DataMashup` package and `Connections.xml`)
- `enrich_spec_from_pbit()` handles BIM extraction, XMLA parsing, and PBIT metadata enrichment
- `skip_conversion=True` is passed by default — DAX→MDX conversion requires a Java JAR
- Schema name is derived from the PBIT filename (not XMLA database name)
- Calculated columns are available in `spec.metadata["pbit_calculated_columns"]`
- The `.env` file contains all connection config — no credentials in skill inputs
- Both JSON (Kyvos 2026.5+) and XML payload formats are supported
- The warehouse registry resolves JDBC URLs and drivers by `WAREHOUSE_TYPE`

## Input Schema

```json
{
  "pbit_file_path": "string (required) — path to the .pbit file",
  "env_file": "string (default .env) — path to the .env config file",
  "folder_name": "string (optional) — override KYVOS_FOLDER_NAME",
  "payload_format": "json|xml (optional) — override KYVOS_PAYLOAD_FORMAT",
  "use_plan_apply": "bool (optional) — use plan/apply lifecycle",
  "dry_run": "bool (optional) — parse + compile only, no API calls",
  "skip_conversion": "bool (default true) — skip DAX→MDX Java conversion"
}
```

**No credentials in inputs.** All secrets come from `.env` (or `*_PASSWORD_CMD` indirection).

## Output Schema

```json
{
  "success": "bool",
  "spec_summary": {
    "tables": "int",
    "relationships": "int",
    "measures": "int",
    "hierarchies": "int",
    "calculated_columns": "int"
  },
  "connection_name": "string",
  "dataset_name_to_id": {"dataset_name": "dataset_id"},
  "drd_name": "string",
  "smodel_name": "string",
  "created_entities": [{"entity_type": "string", "id": "string", "name": "string"}],
  "errors": ["string"],
  "warnings": ["string"]
}
```

## Backend

Python code snippets Claude runs step by step:

### Step 1: Load config

```python
from kyvos_sdk.config import KyvosConfig

config = KyvosConfig.from_env_file(env_file)
```

### Step 2: Parse PBIT

PBIT files are binary — read as `rb`. The `enrich_spec_from_pbit()` function handles BIM extraction, XMLA parsing, and PBIT-specific metadata enrichment. Pass `skip_conversion=True` unless the user explicitly requests DAX→MDX conversion (which requires a Java JAR).

```python
from kyvos_xmla_parser.pbit_adapter import enrich_spec_from_pbit

with open(pbit_file_path, "rb") as f:
    spec = enrich_spec_from_pbit(
        f.read(),
        filename=pbit_file_path,
        skip_conversion=skip_conversion,
    )

calc_columns = spec.metadata.get("pbit_calculated_columns", [])
print(f"Parsed PBIT: {len(spec.tables)} tables, "
      f"{len(spec.semantic_model.relationships)} relationships, "
      f"{len(spec.semantic_model.measures)} measures, "
      f"{len(calc_columns)} calculated columns")
```

### Step 3: Initialize Kyvos client

```python
from kyvos_sdk.client import KyvosService
from kyvos_sdk.provisioning import ProvisioningClient, FolderType

svc = KyvosService(config=config)
svc.initialize()
prov = ProvisioningClient(svc)
```

### Step 4: Create folder

```python
folder_result = prov.create_folder(config.folder_name, FolderType.RDATASET)
if not folder_result.succeeded:
    raise RuntimeError(
        f"Folder creation failed: {[d.message for d in folder_result.diagnostics]}"
    )
folder_id = folder_result.primary_entity_id
print(f"Folder: {config.folder_name} (id={folder_id})")
```

### Step 5: Create connection

```python
from kyvos_sdk.warehouse_registry import build_jdbc_url, get_warehouse_profile

jdbc_url = config.warehouse_jdbc_url or build_jdbc_url(
    config.warehouse_type,
    config.warehouse_host,
    config.warehouse_port,
    config.warehouse_database,
    **config.warehouse_extra_params,
)
driver = config.warehouse_driver or get_warehouse_profile(config.warehouse_type).driver_class
db_version = config.warehouse_db_version or get_warehouse_profile(config.warehouse_type).db_version_default

conn_result = prov.create_connection(
    name=config.warehouse_connection_name,
    host=config.warehouse_host,
    port=config.warehouse_port,
    database=config.warehouse_database,
    username=config.warehouse_username,
    password=config.warehouse_password,
    db_type=config.warehouse_type,
    db_version=db_version,
    use_json=(config.payload_format == "json"),
    jdbc_url_override=jdbc_url,
    driver_override=driver,
)
if not conn_result.succeeded:
    raise RuntimeError(
        f"Connection creation failed: {[d.message for d in conn_result.diagnostics]}"
    )
connection_id = conn_result.primary_entity_id
print(f"Connection: {config.warehouse_connection_name} (id={connection_id})")
```

### Step 6: Create datasets

```python
from kyvos_sm_skills.contract_adapter import compile_dataset_artifact

dataset_name_to_id = {}
created_entities = []

for table in spec.tables:
    if config.skip_hidden_tables and table.is_hidden:
        continue

    ds_result = prov.create_dataset(
        table,
        config.warehouse_connection_name,
        folder_id,
        config.folder_name,
        use_json=(config.payload_format == "json"),
    )

    if not ds_result.succeeded:
        raise RuntimeError(
            f"Dataset creation failed for {table.name}: "
            f"{[d.message for d in ds_result.diagnostics]}\n"
            f"Created so far: {dataset_name_to_id}"
        )

    dataset_name_to_id[table.name] = ds_result.primary_entity_id
    created_entities.append({
        "entity_type": "DATASET",
        "id": ds_result.primary_entity_id,
        "name": ds_result.primary_entity_name,
    })

    prov.refresh_dataset_columns(ds_result.primary_entity_id)
    print(f"  Dataset: {ds_result.primary_entity_name} (id={ds_result.primary_entity_id})")
```

### Step 7: Build DRD graph + create DRD

```python
from kyvos_sm_skills.contract_adapter import compile_drd_artifact

drd_name = f"{config.folder_name}DRD"
drd_id = f"drd_{config.folder_name}"

drd_artifact = compile_drd_artifact(
    drd_name=drd_name,
    drd_id=drd_id,
    folder_id=folder_id,
    folder_name=config.folder_name,
    dataset_name_to_id=dataset_name_to_id,
    relationships=spec.semantic_model.relationships,
    fmt=config.payload_format,
)

drd_result = prov.apply_artifact(drd_artifact)
if not drd_result.succeeded:
    raise RuntimeError(
        f"DRD creation failed: {[d.message for d in drd_result.diagnostics]}"
    )
print(f"DRD: {drd_name}")
```

### Step 8: Compile + create semantic model

```python
from kyvos_sm_skills.contract_adapter import compile_smodel_artifact

sm_artifact = compile_smodel_artifact(
    spec.semantic_model,
    drd_name=drd_name,
    drd_id=drd_id,
    folder_id=folder_id,
    folder_name=config.folder_name,
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
smodel_name = spec.semantic_model.name
print(f"Semantic Model: {smodel_name}")
```

### Step 9: Report results

```python
calc_columns = spec.metadata.get("pbit_calculated_columns", [])

result = {
    "success": True,
    "spec_summary": {
        "tables": len(spec.tables),
        "relationships": len(spec.semantic_model.relationships),
        "measures": len(spec.semantic_model.measures),
        "hierarchies": len(spec.semantic_model.hierarchies),
        "calculated_columns": len(calc_columns),
    },
    "connection_name": config.warehouse_connection_name,
    "dataset_name_to_id": dataset_name_to_id,
    "drd_name": drd_name,
    "smodel_name": smodel_name,
    "created_entities": created_entities + [
        {"entity_type": "FOLDER", "id": folder_id, "name": config.folder_name},
        {"entity_type": "CONNECTION", "id": connection_id, "name": config.warehouse_connection_name},
    ],
    "errors": [],
    "warnings": [],
}
print(f"\n✅ PBIT Deployment Successful")
print(f"   Tables parsed: {len(spec.tables)}")
print(f"   Datasets created: {len(dataset_name_to_id)}")
print(f"   Relationships: {len(spec.semantic_model.relationships)}")
print(f"   Measures: {len(spec.semantic_model.measures)}")
print(f"   Calculated columns: {len(calc_columns)}")
print(f"   Connection: {config.warehouse_connection_name}")
print(f"   DRD: {drd_name}")
print(f"   Semantic Model: {smodel_name}")
```

## PBIT-Specific Notes

- **Binary format:** PBIT files are ZIP archives — always read as `rb`
- **BIM extraction:** `enrich_spec_from_pbit()` extracts the `DataModelSchema` from the archive
- **DAX conversion:** Set `skip_conversion=False` only if the Java JAR is available; otherwise the spec will have `pbit_calculated_columns` in metadata but no converted MDX
- **Schema name:** Derived from the PBIT filename, not the XMLA database name inside the archive
- **Calculated columns:** Available in `spec.metadata["pbit_calculated_columns"]` — these are PBIT-specific expressions that may need manual review

## Error Handling

Same pattern as `deploy-from-xmla`:
1. **Halt immediately** on any step failure
2. **Report diagnostics** from `result.diagnostics`
3. **List created-so-far entities** for resume or cleanup
4. **Do not retry** — the SDK's `max_retries` handles transient failures

## Example Interactions

### Basic PBIT deployment

> "Deploy the Adventure Works PBIT file to Kyvos. File is at `/path/to/AdventureWorks.pbit`. My .env is at `/path/to/.env`."

Claude runs Steps 1–9 with `pbit_file_path="/path/to/AdventureWorks.pbit"` and `env_file="/path/to/.env"`.

### Dry run

> "Parse the PBIT file at `/path/to/AdventureWorks.pbit` and show me what would be deployed, but don't create anything."

Claude runs Steps 1–2 only, reports the spec summary including calculated columns.

### With DAX conversion

> "Deploy the PBIT file and convert DAX measures to MDX. The Java JAR is at `/path/to/dax-converter.jar`."

Claude runs Steps 1–9 with `skip_conversion=False` and passes the JAR path to `enrich_spec_from_pbit()`.

## Dependencies

```bash
pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser
```
