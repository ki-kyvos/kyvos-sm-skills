# CLAUDE.md — Kyvos Semantic Model Skills

## Project Purpose

This repo provides Claude Cowork skills and Python generators for creating Kyvos semantic models. Skills are markdown files that teach Claude how to orchestrate SDK building blocks to deploy semantic models from XMLA, PBIT, or user intent.

## Available Skills

### Existing Skills (7)
| File | Purpose |
|------|---------|
| `skills/generate-connection.md` | Generate Kyvos connection XML/JSON |
| `skills/generate-dataset.md` | Generate Kyvos dataset XML/JSON |
| `skills/generate-drd.md` | Generate DRD XML/JSON |
| `skills/generate-semantic-model.md` | Generate semantic model XML/JSON |
| `skills/design-star-schema.md` | Design star/snowflake/multifact schemas |
| `skills/design-measures.md` | Design enterprise-quality measures |
| `skills/convert-dax-to-mdx.md` | Convert DAX measures to MDX |

### New Skills (6 — planned for Phases 2–5)
| File | Purpose | Phase |
|------|---------|-------|
| `skills/deploy-from-xmla.md` | Orchestrate XMLA → Kyvos deployment | 2 |
| `skills/deploy-from-pbit.md` | Orchestrate PBIT → Kyvos deployment | 2 |
| `skills/deploy-from-multi-pbit.md` | Multi-PBIT analysis + SM grouping | 5 |
| `skills/generate-sm-from-intent.md` | Design new SM(s) from user intent + generate data + deploy | 4 |
| `skills/discover-sm-from-warehouse.md` | Recommend SM(s) from existing warehouse schema | 3 |
| `skills/inspect-warehouse-schema.md` | Inspect existing warehouse schema (multi-DB) | 3 |

## SDK Building Blocks Reference

These are the key SDK functions Claude should call from skill code snippets:

### Configuration
```python
from kyvos_sdk.config import KyvosConfig
config = KyvosConfig.from_env_file("/path/to/.env")
# Fields: base_url, username, password, warehouse_type, warehouse_host,
#         warehouse_port, warehouse_database, warehouse_username, warehouse_password,
#         warehouse_jdbc_url, warehouse_driver, warehouse_schema, warehouse_extra_params,
#         folder_name, payload_format, use_plan_apply, skip_hidden_tables, default_scale
```

### Warehouse Registry
```python
from kyvos_sdk.warehouse_registry import build_jdbc_url, build_sqlalchemy_url, get_warehouse_profile
# Resolves warehouse_type → JDBC URL template, driver class, default port, SQLAlchemy dialect
# Supports: POSTGRES, SNOWFLAKE, BIGQUERY, ORACLE, MSSQL, REDSHIFT
# User can bypass with WAREHOUSE_JDBC_URL / WAREHOUSE_DRIVER env vars
jdbc_url = config.warehouse_jdbc_url or build_jdbc_url(
    config.warehouse_type, config.warehouse_host, config.warehouse_port,
    config.warehouse_database, **config.warehouse_extra_params,
)
driver = config.warehouse_driver or get_warehouse_profile(config.warehouse_type).driver_class
```

### Parsing (kyvos-xmla-parser)
```python
from kyvos_xmla_parser.xmla_parser import parse_xmla
spec = parse_xmla(xmla_text)  # Returns DomainDemoSpec

from kyvos_xmla_parser.pbit_adapter import enrich_spec_from_pbit
with open(pbit_path, "rb") as f:
    spec = enrich_spec_from_pbit(f.read(), filename=pbit_path, skip_conversion=True)
```

### Compilation (kyvos-sm-skills contract adapters)
```python
from kyvos_sm_skills.contract_adapter import (
    compile_connection_artifact, compile_dataset_artifact,
    build_drd_graph, compile_drd_artifact, compile_smodel_artifact,
)
# All return CompiledArtifact with deterministic payload + content_hash
# Pass jdbc_url_override and driver_override to compile_connection_artifact
```

### Provisioning (kyvos-sdk-python)
```python
from kyvos_sdk.client import KyvosService
from kyvos_sdk.provisioning import ProvisioningClient, FolderType
svc = KyvosService(config=config)
svc.initialize()
prov = ProvisioningClient(svc)

# All methods return OperationResult
folder_result = prov.create_folder(config.folder_name, FolderType.RDATASET)
conn_result = prov.create_connection(...)  # accepts jdbc_url_override, driver_override

# Convenience properties on OperationResult:
#   .primary_entity_id  → str | None (entity_refs[0].id)
#   .primary_entity_name → str | None
#   .succeeded          → bool
#   .diagnostics        → list[Diagnostic]
```

### Inspection (kyvos-sdk-python)
```python
from kyvos_sdk.inspection import InspectionClient
insp = InspectionClient(svc)
# Methods: health_check, list_folders, list_datasets, list_drds, list_smodels,
#          get_dataset_columns, get_drd_graph, validate_dataset/drd/smodel, get_connection
```

### Data Generation (kyvos-data-gen)
```python
from kyvos_data_gen import generate_data
result = generate_data(spec)  # Returns DataGenerationManifest
```

## Natural Language Workflow Patterns

1. **XMLA deployment:** `deploy-from-xmla` → parse XMLA → compile artifacts → provision
2. **PBIT deployment:** `deploy-from-pbit` → parse PBIT → compile artifacts → provision
3. **Multi-PBIT:** `deploy-from-multi-pbit` → parse all PBITs locally → LLM groups into SMs → deploy each
4. **User intent (new data):** `generate-sm-from-intent` → domain research → design schema → generate data → load to warehouse → deploy
5. **User intent (existing schema):** `inspect-warehouse-schema` → `discover-sm-from-warehouse` → deploy

## .env File Usage

- Load with `KyvosConfig.from_env_file("path/to/.env")`
- Process env vars take precedence over file values
- `chmod 600 .env` and add to `.gitignore` — never commit
- Use `*_PASSWORD_CMD` for enterprise secret indirection (Vault, OS keychain)
- See `.env.example` in kyvos-sdk-python for all available variables

## Warehouse Type Configuration

`WAREHOUSE_TYPE` drives:
- JDBC URL template (from `warehouse_registry.py`)
- Driver class name
- Default port (when `WAREHOUSE_PORT=0`)
- SQLAlchemy dialect for schema inspection
- DB version default

User can bypass the registry with `WAREHOUSE_JDBC_URL` and `WAREHOUSE_DRIVER`.

## Error Handling

- All provisioning/inspection methods return `OperationResult`
- Check `result.succeeded` before proceeding
- On failure: inspect `result.diagnostics` for error details
- `result.retryable` indicates if the operation can be retried
- Halt on first failure, report created-so-far entities for resume/cleanup

## Dependency Direction

- `kyvos-sm-skills` core (generators, contract_adapter) does NOT import `kyvos_sdk` in its required path
- `kyvos-sdk-python` is an optional `[sdk]` extra
- Registry resolution happens in the SDK layer, not in generators
- Generators accept explicit `jdbc_url_override`/`driver_override` parameters
