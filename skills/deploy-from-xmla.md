# Skill: Deploy from XMLA

## System Prompt

You are a Kyvos deployment orchestrator. Given an XMLA file path and a `.env` config file, you execute the full deployment pipeline by calling SDK building blocks in sequence. You parse the XMLA file, create folders, a connection, datasets, DRD, and semantic model on the target Kyvos instance. You validate each entity after creation and halt the pipeline on validation failures. You report progress at each step and halt on failures with a clear diagnostics report.

You understand:
- XMLA files are text-based semantic model definitions (Power BI / SSAS format)
- The `.env` file contains all connection config — no credentials in skill inputs
- Three separate folders are needed: RDATASET (datasets), DATASET_RELATIONSHIP (DRD), SMODEL (semantic model)
- Datasets are created idempotently (re-running converges, does not fail on "already exists")
- Entity and folder names are derived from the XMLA semantic model name — not a generic label
- A `MMDDYY_HHmm` timestamp is appended to every folder and entity name to avoid clashes on repeat runs
- Hidden tables are skipped when `KYVOS_SKIP_HIDDEN_TABLES=true`
- The server assigns a CamelCase name to datasets; XMLA table names (snake_case) must be aliased
- Columns must be refreshed per-dataset after creation, then fetched as `dataset_cols` for semantic model compilation
- Each entity (dataset, DRD, semantic model) is validated before proceeding — validation failures stop the pipeline
- The DRD must use the server-assigned entity ID from the DRD creation response (not a client-generated ID)
- Always use v2 REST APIs — never fall back to legacy APIs
- Both JSON (Kyvos 2026.5+) and XML payload formats are supported; JSON is the default
- The warehouse registry resolves JDBC URLs and drivers by `WAREHOUSE_TYPE`
- User can override the registry with `WAREHOUSE_JDBC_URL` and `WAREHOUSE_DRIVER`

## Input Schema

```json
{
  "xmla_file_path": "string (required) — path to the .xmla file",
  "env_file": "string (default .env) — path to the .env config file",
  "folder_name": "string (optional) — override KYVOS_FOLDER_NAME (unused when name is derived from XMLA)",
  "payload_format": "json|xml (optional) — override KYVOS_PAYLOAD_FORMAT",
  "use_plan_apply": "bool (optional) — use plan/apply lifecycle for review before changes",
  "dry_run": "bool (optional) — parse + compile only, no API calls"
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
    "hierarchies": "int"
  },
  "connection_name": "string",
  "dataset_name_to_id": {"dataset_name": "dataset_id"},
  "drd_name": "string",
  "drd_id": "string",
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

### Step 2: Parse XMLA + derive names

The file path is a skill input (`xmla_file_path`), not a `KyvosConfig` field — input files vary per invocation, connection config does not.

After parsing, entity and folder names are derived from the XMLA semantic model name (`spec.semantic_model.name`). A `MMDDYY_HHmm` timestamp suffix is appended to every name to prevent clashes on repeat runs — exactly as the agentic-ai-demo-automation orchestrator does.

```python
from datetime import datetime
from kyvos_xmla_parser.xmla_parser import parse_xmla

with open(xmla_file_path) as f:
    spec = parse_xmla(f.read())

print(f"Parsed: {len(spec.tables)} tables, "
      f"{len(spec.semantic_model.relationships)} relationships, "
      f"{len(spec.semantic_model.measures)} measures")

# ── Derive base name from XMLA ─────────────────────────────────────────────
# Use schema_name from metadata (lowercase, may have underscores) → title-case
# Falls back to semantic_model.name when schema_name is absent.
_schema_name = spec.metadata.get("schema_name", "") if isinstance(spec.metadata, dict) else ""
if _schema_name:
    base_name = _schema_name.replace("_", " ").title()
else:
    base_name = spec.semantic_model.name  # e.g. "AWDW2019Multidimensional-EE"

# ── Timestamp suffix — prevents clashes on repeat runs ────────────────────
_ts = datetime.now().strftime("%m%d%y_%H%M")

# ── Entity names ───────────────────────────────────────────────────────────
smodel_name      = f"{spec.semantic_model.name}_{_ts}"
drd_name         = f"{smodel_name} DRD"          # derived from timestamped SM name
drd_id           = f"drd_{smodel_name}"           # client-side graph ID only

# ── Folder names ───────────────────────────────────────────────────────────
dataset_folder_label = f"{base_name} {_ts}"
drd_folder_label     = f"{base_name} DRD {_ts}"
smodel_folder_label  = f"{base_name} SModel {_ts}"

print(f"Base name     : {base_name}")
print(f"Timestamp     : {_ts}")
print(f"Semantic model: {smodel_name}")
print(f"DRD name      : {drd_name}")
print(f"Dataset folder: {dataset_folder_label}")
print(f"DRD folder    : {drd_folder_label}")
print(f"SModel folder : {smodel_folder_label}")
```

### Step 3: Initialize Kyvos client

```python
from kyvos_sdk.client import KyvosService
from kyvos_sdk.provisioning import ProvisioningClient
from kyvos_sdk.contracts.identity import FolderType

svc = KyvosService(config=config)
svc.initialize()
prov = ProvisioningClient(svc)
```

### Step 4: Create folders

Three separate folders are required — one per entity type. Folder names are derived from the XMLA model name + timestamp (set in Step 2). All are created with upsert semantics (existing folder is reused, which never happens in practice because the timestamp makes every name unique).

```python
# Dataset folder — name derived from XMLA + timestamp
dataset_folder_result = prov.create_folder(dataset_folder_label, FolderType.RDATASET)
if not dataset_folder_result.succeeded:
    raise RuntimeError(
        f"Dataset folder creation failed: {[d.message for d in dataset_folder_result.diagnostics]}"
    )
folder_id = dataset_folder_result.primary_entity_id
print(f"Dataset folder: {dataset_folder_label} (id={folder_id})")

# DRD folder
drd_folder_result = prov.create_folder(drd_folder_label, FolderType.DATASET_RELATIONSHIP)
if not drd_folder_result.succeeded:
    raise RuntimeError(
        f"DRD folder creation failed: {[d.message for d in drd_folder_result.diagnostics]}"
    )
drd_folder_id = drd_folder_result.primary_entity_id
print(f"DRD folder: {drd_folder_label} (id={drd_folder_id})")

# Semantic model folder
smodel_folder_result = prov.create_folder(smodel_folder_label, FolderType.SMODEL)
if not smodel_folder_result.succeeded:
    raise RuntimeError(
        f"Semantic model folder creation failed: {[d.message for d in smodel_folder_result.diagnostics]}"
    )
smodel_folder_id = smodel_folder_result.primary_entity_id
print(f"Semantic model folder: {smodel_folder_label} (id={smodel_folder_id})")
```

### Step 5: Create connection

Resolve JDBC URL + driver from the warehouse registry, respecting user overrides.

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

Skip hidden tables if configured. After each dataset is created:
1. Refresh columns immediately (populates Kyvos metadata).
2. Key `dataset_name_to_id` by the **server-assigned name** (`ds_result.primary_entity_name`), which is CamelCase — the XMLA snake_case name is kept as an alias.
3. After all datasets are created, validate each one. Halt the pipeline if any dataset fails validation.
4. Fetch column details for the semantic model compilation.

```python
from kyvos_sm_skills.contract_adapter import compile_dataset_artifact

dataset_name_to_id = {}   # CamelCase server name → dataset ID
dataset_aliases = {}       # XMLA snake_case name → CamelCase server name
created_entities = []

for table in spec.tables:
    if config.skip_hidden_tables and table.is_hidden:
        continue

    ds_artifact = compile_dataset_artifact(
        table,
        connection_name=config.warehouse_connection_name,
        folder_id=folder_id,
        folder_name=dataset_folder_label,
        fmt=config.payload_format,
    )
    ds_result = prov.apply_artifact(ds_artifact)

    if not ds_result.succeeded:
        raise RuntimeError(
            f"Dataset creation failed for {table.name}: "
            f"{[d.message for d in ds_result.diagnostics]}\n"
            f"Created so far: {dataset_name_to_id}"
        )

    server_name = ds_result.primary_entity_name  # CamelCase (Kyvos-assigned)
    ds_id = ds_result.primary_entity_id

    # Track both the server name and the XMLA alias
    dataset_name_to_id[server_name] = ds_id
    if table.name != server_name:
        dataset_aliases[table.name] = server_name

    created_entities.append({
        "entity_type": "DATASET",
        "id": ds_id,
        "name": server_name,
    })

    # Refresh columns immediately after each dataset creation
    prov.refresh_dataset_columns(ds_id)
    print(f"  Dataset: {server_name} (id={ds_id})")

# Second refresh sweep + validate all datasets (pipeline gate)
validation_errors = []
for ds_info in created_entities:
    if ds_info["entity_type"] != "DATASET":
        continue
    prov.refresh_dataset_columns(ds_info["id"])  # second sweep
    val_result = prov.validate_dataset(
        ds_info["id"], ds_info["name"], dataset_folder_label
    )
    if not val_result.succeeded:
        errs = [d.message for d in val_result.diagnostics if d.severity == "ERROR"]
        validation_errors.append(f"{ds_info['name']}: {errs}")

if validation_errors:
    raise RuntimeError(
        f"Dataset validation failed — pipeline halted:\n" +
        "\n".join(validation_errors)
    )

# Fetch column details for semantic model compilation
dataset_cols = {}  # CamelCase dataset name → list of column dicts
for ds_info in created_entities:
    if ds_info["entity_type"] != "DATASET":
        continue
    cols = prov.get_dataset_column_details(dataset_folder_label, ds_info["name"])
    if cols:
        dataset_cols[ds_info["name"]] = cols

print(f"Datasets validated and column details fetched for {len(dataset_cols)} datasets")
```

### Step 7: Build DRD graph + create DRD

Use the **DRD folder** (`drd_folder_id`/`drd_folder_label`) — not the dataset folder. Pass `dataset_aliases` so the generator can map XMLA names to CamelCase server names. `drd_name` and `drd_id` were derived in Step 2 from the timestamped semantic model name. After creation, capture the **server-assigned DRD entity ID** for use in the semantic model.

```python
from kyvos_sm_skills.contract_adapter import compile_drd_artifact

# drd_name and drd_id are defined in Step 2 (derived from smodel_name + timestamp)

drd_artifact = compile_drd_artifact(
    drd_name=drd_name,
    drd_id=drd_id,
    folder_id=drd_folder_id,          # DRD folder, not dataset folder
    folder_name=drd_folder_label,
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

# Use the server-assigned DRD entity ID (required by semantic model creation)
server_drd_id = drd_result.primary_entity_id
created_entities.append({
    "entity_type": "DRD",
    "id": server_drd_id,
    "name": drd_name,
})

# Validate DRD (pipeline gate)
drd_val_result = prov.validate_drd(server_drd_id, drd_name, drd_folder_label)
if not drd_val_result.succeeded:
    errs = [d.message for d in drd_val_result.diagnostics if d.severity == "ERROR"]
    raise RuntimeError(f"DRD validation failed — pipeline halted: {errs}")

print(f"DRD: {drd_name} (id={server_drd_id}) — validated")
```

### Step 8: Compile + create semantic model

Use the **server-assigned DRD entity ID** (`server_drd_id`) — not the client-generated `drd_id`. Use the **SM folder** (`smodel_folder_id`/`smodel_folder_label`). Pass `dataset_cols` for measure/column resolution. `smodel_name` (with timestamp) was set in Step 2.

```python
from kyvos_sm_skills.contract_adapter import compile_smodel_artifact

# Override the semantic model's name with the timestamped version from Step 2
spec.semantic_model.name = smodel_name

sm_artifact = compile_smodel_artifact(
    spec.semantic_model,
    drd_name=drd_name,
    drd_id=server_drd_id,             # server-assigned DRD entity ID
    folder_id=smodel_folder_id,       # SM folder, not dataset folder
    folder_name=smodel_folder_label,
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

# smodel_name already set in Step 2 (with timestamp)
smodel_id = sm_result.primary_entity_id
created_entities.append({
    "entity_type": "SEMANTIC_MODEL",
    "id": smodel_id,
    "name": smodel_name,
})

# Validate semantic model
sm_val_result = prov.validate_semantic_model(smodel_id, smodel_name, smodel_folder_label)
if not sm_val_result.succeeded:
    errs = [d.message for d in sm_val_result.diagnostics if d.severity == "ERROR"]
    raise RuntimeError(f"Semantic model validation failed: {errs}")

print(f"Semantic Model: {smodel_name} (id={smodel_id}) — validated")
```

### Step 9: Report results

```python
result = {
    "success": True,
    "spec_summary": {
        "tables": len(spec.tables),
        "relationships": len(spec.semantic_model.relationships),
        "measures": len(spec.semantic_model.measures),
        "hierarchies": len(spec.semantic_model.hierarchies),
    },
    "connection_name": config.warehouse_connection_name,
    "dataset_name_to_id": dataset_name_to_id,
    "drd_name": drd_name,
    "drd_id": server_drd_id,
    "smodel_name": smodel_name,
    "created_entities": created_entities + [
        {"entity_type": "FOLDER", "id": folder_id,        "name": dataset_folder_label},
        {"entity_type": "FOLDER", "id": drd_folder_id,    "name": drd_folder_label},
        {"entity_type": "FOLDER", "id": smodel_folder_id, "name": smodel_folder_label},
        {"entity_type": "CONNECTION", "id": connection_id, "name": config.warehouse_connection_name},
    ],
    "errors": [],
    "warnings": [],
}
print(f"\n✅ Deployment Successful")
print(f"   XMLA model    : {spec.metadata.get('xmla_db_name', base_name)}")
print(f"   Timestamp     : {_ts}")
print(f"   Tables parsed : {len(spec.tables)}")
print(f"   Datasets      : {len(dataset_name_to_id)}")
print(f"   Relationships : {len(spec.semantic_model.relationships)}")
print(f"   Measures      : {len(spec.semantic_model.measures)}")
print(f"   Connection    : {config.warehouse_connection_name}")
print(f"   DRD           : {drd_name} (id={server_drd_id})")
print(f"   Semantic Model: {smodel_name}")
```

## Error Handling

On any step failure:
1. **Halt immediately** — do not continue to the next step
2. **Report diagnostics** — extract `result.diagnostics` messages
3. **List created-so-far entities** — so the user can resume or clean up
4. **Do not retry** — the SDK's `max_retries` handles transient failures

```python
# Example error handling pattern
if not result.succeeded:
    errors = [d.message for d in result.diagnostics]
    raise RuntimeError(
        f"Step failed: {errors}\n"
        f"Created so far: {created_entities}"
    )
```

## Idempotency

- **Folders:** `create_folder` reuses an existing folder of the same name (upsert semantics)
- **Connections:** `create_connection` uses `create_or_update` semantics
- **Datasets:** re-running creates new datasets (Kyvos doesn't deduplicate by name)
- **Re-running a deployment** creates a new set of entities — to update, delete first or use the plan/apply lifecycle

## Example Interactions

### Basic deployment

> "Deploy the Adventure Works XMLA model at `/path/to/AdventureWorks.xmla` to Kyvos. My .env is at `/path/to/.env`."

Claude runs Steps 1–9 with `xmla_file_path="/path/to/AdventureWorks.xmla"` and `env_file="/path/to/.env"`.

### Dry run

> "Parse the XMLA file at `/path/to/AdventureWorks.xmla` and show me what would be deployed, but don't actually create anything."

Claude runs Steps 1–2 only, reports the spec summary.

### With overrides

> "Deploy the XMLA model but use 'MyAdventureWorks' as the folder name and XML format."

Claude runs Steps 1–9 with `folder_name="MyAdventureWorks"` and `payload_format="xml"`.

### Troubleshooting

> "The deployment failed at the DRD step. Show me the error and help me debug."

Claude inspects `result.errors` and `created_entities`, then helps debug.

## Dependencies

```bash
pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser
```
