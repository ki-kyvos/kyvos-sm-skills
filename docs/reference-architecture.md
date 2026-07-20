# Reference Architecture: Kyvos Semantic Model Workflows via Claude Code

> **Scope:** This document defines the canonical reference architecture for running all possible Kyvos semantic model workflows through Claude Code using the skills in `kyvos-sm-skills`.

---

## 1. Architecture Overview

The platform is a layered stack where each layer has a single responsibility and well-defined boundaries. Claude Code sits at the top as the orchestration and reasoning layer; the skills provide prompt-based task definitions; the SDK provides deterministic compilers, contracts, and transport; and supporting packages handle parsing, data generation, and DAX conversion.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Claude Code (Orchestration)                      │
│  Reasoning · Domain Research · User Approval Gates · Code Execution  │
├─────────────────────────────────────────────────────────────────────┤
│                     kyvos-sm-skills (Skills Layer)                   │
│  12 markdown skill files · _shared/sm-design-principles.md           │
│  contract_adapter.py (SDK compiler wrappers)                         │
├─────────────────────────────────────────────────────────────────────┤
│                    kyvos-sdk-python (SDK Layer)                       │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────┐ ┌───────────────┐  │
│  │  Contracts   │ │  Compilers   │ │ Provisioning│ │  Inspection   │  │
│  │  (Pydantic)  │ │  (Pure fns)  │ │  Client     │ │  Client       │  │
│  ├─────────────┤ ├──────────────┤ ├────────────┤ ├───────────────┤  │
│  │  Workflow    │ │ Observability│ │  Factory    │ │  Config       │  │
│  │  Helpers     │ │  Recorder    │ │  Functions  │ │  (.env)       │  │
│  └─────────────┘ └──────────────┘ └────────────┘ └───────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                   Supporting Packages (Optional)                     │
│  ┌──────────────────┐ ┌───────────────┐ ┌────────────────────────┐  │
│  │ kyvos-xmla-parser│ │ kyvos-data-gen│ │ kyvos-dax-mdx-converter│  │
│  │ (XMLA/PBIT parse)│ │ (Synthetic CSV│ │ (DAX→MDX conversion)   │  │
│  └──────────────────┘ └───────────────┘ └────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                     Kyvos Platform (REST API v2)                     │
│  Folders · Connections · Datasets · DRDs · Semantic Models           │
└─────────────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Responsibility | Does Not Do |
|-------|---------------|-------------|
| **Claude Code** | Reasoning, domain research, user interaction, code execution, error diagnosis | Direct HTTP calls (uses SDK), credential storage |
| **Skills Layer** | Prompt definitions, input/output schemas, backend code snippets, contract adapters | Business logic (delegates to SDK) |
| **SDK Layer** | Contracts, pure compilers, provisioning/inspection clients, workflow helpers, observability | LLM calls, domain research, file parsing |
| **Supporting Packages** | XMLA/PBIT parsing, synthetic data generation, DAX→MDX conversion | Kyvos API calls, LLM calls |
| **Kyvos Platform** | Entity storage, query engine, OLAP cube serving | Client-side logic |

---

## 2. Claude Code as Orchestration Layer

Claude Code is the runtime that executes skills. Each skill file is a markdown document containing:

1. **System Prompt** — Role and instructions for the LLM
2. **Input Schema** — JSON schema for skill inputs
3. **Output Schema** — JSON schema for expected outputs
4. **Backend** — Python code snippets Claude executes step-by-step
5. **Example Interactions** — Sample user prompts and expected behavior

### How Claude Code Executes a Skill

```
User Prompt
    │
    ▼
Claude Code loads skill file → extracts System Prompt as context
    │
    ▼
Claude interprets user intent → maps to skill input schema
    │
    ▼
Claude executes Backend steps sequentially:
    ├── Python code blocks (imports, function calls, API interactions)
    ├── LLM reasoning steps (domain research, schema design, measure design)
    └── User approval gates (⏸ pauses for user confirmation)
    │
    ▼
Output produced per skill's Output Schema
```

### Key Claude Code Capabilities Used

- **Code execution** — Runs Python snippets from `## Backend` sections
- **Web search** — Domain research for `discover-sm-from-warehouse` and `generate-sm-from-intent`
- **File I/O** — Reads XMLA/PBIT files, writes CSV files, reads `.env` config
- **User interaction** — Presents findings at approval gates, accepts corrections
- **Error diagnosis** — Inspects `result.diagnostics`, helps user debug failures

---

## 3. Skills Inventory and Classification

### 3.1 End-to-End Orchestration Skills

These skills run the complete pipeline from input to deployed semantic model.

| Skill | Input | Pipeline | Approval Gates |
|-------|-------|----------|----------------|
| `deploy-from-xmla.md` | XMLA file + `.env` | Parse → Folders → Connection → Datasets → DRD → SM | None (deterministic) |
| `deploy-from-pbit.md` | PBIT file + `.env` | Parse PBIT → Folders → Connection → Datasets → DRD → SM | None (deterministic) |
| `discover-sm-from-warehouse.md` | Warehouse schema + `.env` | Inspect → Domain ID → Research → SM Design → Deploy | 3 gates |
| `generate-sm-from-intent.md` | NL intent + domain + `.env` | Research → SM Design → Data Gen → Load → Deploy | 3 gates |

### 3.2 Component Generation Skills

These skills generate individual Kyvos entity payloads. They are used standalone or as building blocks within orchestration skills.

| Skill | Input | Output | LLM vs Deterministic |
|-------|-------|--------|---------------------|
| `generate-connection.md` | DB params | Connection JSON/XML | Deterministic (compiler) |
| `generate-dataset.md` | TableSpec | Dataset JSON/XML | Deterministic (compiler) |
| `generate-drd.md` | Relationships + dataset IDs | DRD JSON/XML | Deterministic (compiler) |
| `generate-semantic-model.md` | Schema + measures + hierarchies | SM JSON/XML | Deterministic (compiler) |

### 3.3 Design Skills (LLM-Only)

These skills use LLM reasoning to produce structured specifications. No Python backend — output is JSON consumed by downstream skills.

| Skill | Input | Output |
|-------|-------|--------|
| `design-star-schema.md` | Domain + business scenario | Schema JSON (tables, columns, relationships) |
| `design-measures.md` | Schema + domain | Measures JSON (base + calculated) |

### 3.4 Utility Skills

| Skill | Input | Output | LLM vs Deterministic |
|-------|-------|--------|---------------------|
| `inspect-warehouse-schema.md` | `.env` + optional filters | Schema summary + pattern detection | Deterministic (SQLAlchemy) |
| `convert-dax-to-mdx.md` | DAX measures | MDX measures | LLM (or `kyvos-dax-mdx-converter` JAR) |

### 3.5 Shared Principles

`skills/_shared/sm-design-principles.md` is referenced by the three agentic skills (`discover-sm-from-warehouse`, `generate-sm-from-intent`) and defines:

- Enterprise-quality mandate (conformed dimensions, industry-standard KPIs, correct granularity)
- Supported schema types (single table, star, snowflake, multifact)
- Domain research requirements
- User approval gate protocol (3 gates)

---

## 4. SDK Layer Architecture

### 4.1 Contracts (`kyvos_sdk.contracts`)

Pydantic models that define the canonical data shapes exchanged between all layers. Contracts depend only on Pydantic + stdlib — no FastAPI, no LangGraph, no MCP, no skills imports.

| Module | Key Types |
|--------|-----------|
| `domain.py` | `ColumnSpec`, `TableSpec`, `DatasetSpec`, `RelationshipSpec`, `MeasureSpec`, `HierarchySpec`, `SemanticModelSpec`, `DashboardSpec`, `CanonicalDataType` |
| `identity.py` | `EntityRef`, `FolderRef`, `DatasetRef`, `DrdNode`, `DrdRelation`, `DrdGraph`, `FolderType` |
| `artifacts.py` | `CompiledArtifact`, `ArtifactKind`, `ArtifactFormat`, `CapabilityRequirement` |
| `results.py` | `ValidationResult`, `OperationResult`, `ConversionResult`, `DataGenerationResult` |
| `plan.py` | `PlanItem`, `PlanRequest`, `ExecutionPlan`, `Approval`, `ApplyRequest`, `ApplyResult`, `StepResult` |
| `manifest.py` | `DataGenerationManifest`, `TableGenerationStats`, `compute_schema_fingerprint` |
| `adapters.py` | Legacy ↔ contract adapters, `adapt_column/table/dataset/relationship/measure/hierarchy/smodel/dashboard`, `adapt_to_imported_model`, `adapt_conversion_result` |
| `versioning.py` | Contract version constants, `parse/validate/backward_compat` checks |
| `common.py` | `ContractMetadata`, `Diagnostic`, `Severity`, `SourceLocation`, `ArtifactRef`, `CorrelationContext` |
| `imported_model.py` | `ImportedModel`, `ImportSource`, `ImportedTable`, `ImportedColumn`, `ParserDiagnostic` |

### 4.2 Compilers (`kyvos_sdk.compiler`)

Pure functions that convert contract types into `CompiledArtifact` objects with deterministic payloads. No side effects, no HTTP, no state.

| Function | Input | Output |
|----------|-------|--------|
| `compile_connection()` | Connection params | `CompiledArtifact` (JSON/XML) |
| `compile_dataset()` | `TableSpec` + connection/folder info | `CompiledArtifact` (JSON/XML) |
| `compile_drd()` | `DrdGraph` | `CompiledArtifact` (JSON/XML) |
| `compile_semantic_model()` | `SemanticModelSpec` + `DrdGraph` | `CompiledArtifact` (JSON/XML) |

Each `CompiledArtifact` contains:
- `payload` — The JSON or XML string
- `content_hash` — SHA256 of payload (deterministic)
- `diagnostics` — List of `Diagnostic` objects
- `capability_requirements` — What the Kyvos instance must support

### 4.3 Provisioning Client (`kyvos_sdk.provisioning`)

Mutating operations against the Kyvos REST API. Wraps `KyvosService` transport.

| Method | Action |
|--------|--------|
| `create_folder()` | Create/reuse folder by type |
| `create_connection()` | Create/update connection |
| `apply_artifact()` | Dispatch `CompiledArtifact` → create entity |
| `refresh_dataset_columns()` | Trigger column refresh |
| `validate_dataset/drd/smodel()` | Validate entity post-creation |
| `plan_creation()` | Generate execution plan (non-mutating) |
| `approve_plan()` | Issue approval token |
| `apply_plan()` | Execute approved plan (mutating) |
| `get_plan/cancel_plan()` | Plan lifecycle management |

### 4.4 Inspection Client (`kyvos_sdk.inspection`)

Non-mutating operations for querying Kyvos state.

| Method | Action |
|--------|--------|
| `health_check()` | Kyvos instance health |
| `list_folders/datasets/drds/smodels()` | Entity listing |
| `get_dataset_columns()` | Column metadata |
| `get_drd_graph()` | Normalized `DrdGraph` with node IDs |
| `validate_dataset/drd/smodel()` | Validation checks |

### 4.5 Workflow Helpers (`kyvos_sdk.workflow`)

High-level orchestration functions that chain compile → plan → review → apply.

| Function | Description |
|----------|-------------|
| `compile_artifact()` | Dispatch to correct compiler by kind |
| `compile_and_plan()` | Compile + generate execution plan |
| `review_plan()` | `PlanReview` with blocking conflict analysis |
| `apply_workflow()` | Execute approved plan with safety checks |
| `plan_review_apply()` | Full lifecycle in one call |

### 4.6 Observability (`kyvos_sdk.observability`)

Records events for tracking plan/apply outcomes, fallback rates, validation classes, and compiler invocations.

| Event Type | Tracked Data |
|------------|-------------|
| `PLAN_CREATED` | Plan ID, items, conflicts |
| `APPLY_STARTED/COMPLETED/FAILED` | Plan ID, step results |
| `COMPILER_INVOKED` | Artifact kind, format, hash |
| `VALIDATION_RESULT` | Entity, severity, diagnostics |
| `FALLBACK_USED` | What failed, what was used instead |
| `ROLLBACK` | Entity, reason |

### 4.7 Configuration (`kyvos_sdk.config`)

`KyvosConfig` loads all settings from `.env` files. No credentials ever appear in skill inputs.

| Config Source | Examples |
|--------------|---------|
| `.env` file | `KYVOS_URL`, `KYVOS_USERNAME`, `KYVOS_PASSWORD` |
| Warehouse config | `WAREHOUSE_TYPE`, `WAREHOUSE_HOST`, `WAREHOUSE_PORT` |
| Payload format | `KYVOS_PAYLOAD_FORMAT` (json/xml) |
| Scale guardrail | `KYVOS_DEFAULT_SCALE` |
| Password indirection | `*_PASSWORD_CMD` for secret managers |

### 4.8 Warehouse Registry (`kyvos_sdk.warehouse_registry`)

Resolves JDBC URLs, driver classes, and SQLAlchemy URLs by warehouse type.

| Supported Types | JDBC URL Pattern |
|----------------|-----------------|
| POSTGRES | `jdbc:postgresql://host:port/db` |
| SNOWFLAKE | `jdbc:snowflake://account/db/schema` |
| BIGQUERY | `jdbc:bigquery://project/dataset` |
| ORACLE | `jdbc:oracle:thin:@host:port:sid` |
| MSSQL | `jdbc:sqlserver://host:port;databaseName=db` |
| REDSHIFT | `jdbc:redshift://host:port/db` |

---

## 5. Supporting Packages

### 5.1 kyvos-xmla-parser

Parses Power BI / SSAS semantic model definitions into `DomainDemoSpec` objects.

| Function | Input | Output |
|----------|-------|--------|
| `parse_xmla()` | XMLA string | `DomainDemoSpec` (tables, relationships, measures, hierarchies) |
| `enrich_spec_from_pbit()` | PBIT binary + filename | `DomainDemoSpec` with PBIT metadata (calculated columns, schema name) |

### 5.2 kyvos-data-gen

Generates synthetic CSV data for semantic model testing and demos.

| Function | Input | Output |
|----------|-------|--------|
| `generate_data()` | `DomainDemoSpec` | `DataGenerationResult` (CSV files, row counts, paths) |

Supports domain-specific generators for retail banking, healthcare, retail, telecom, and custom domains. Row counts capped by `KYVOS_DEFAULT_SCALE`.

### 5.3 kyvos-dax-mdx-converter

Converts DAX measure expressions to Kyvos-compatible MDX. Available as:
- **LLM skill** — `convert-dax-to-mdx.md` for interactive conversion
- **Java JAR** — Deterministic batch conversion via `kyvos_dax_mdx_converter` package
- **Contract adapter** — `to_contract_result()` for SDK contract integration

---

## 6. Contract Adapter Bridge

`kyvos_sm_skills.contract_adapter` bridges the skills layer to the SDK compiler layer. It wraps SDK compilers with backward-compatible signatures that accept legacy model types from `kyvos-xmla-parser` and `kyvos-data-gen`.

```
Skill Backend Code
    │
    ▼
contract_adapter.compile_dataset_artifact(table, ...)     ──┐
contract_adapter.compile_drd_artifact(drd_name, ...)       ──┤── kyvos_sdk.compiler.*
contract_adapter.compile_smodel_artifact(sm_spec, ...)     ──┤
contract_adapter.compile_connection_artifact(...)          ──┘
    │
    ▼
CompiledArtifact (payload + hash + diagnostics)
    │
    ▼
ProvisioningClient.apply_artifact(artifact) → Kyvos REST API
```

The adapter also provides `build_drd_graph()` which constructs a `DrdGraph` contract from relationship specs and dataset ID mappings — used by the DRD and semantic model compilers.

---

## 7. Workflow Catalog

All possible workflows achievable through Claude Code with the skills. Each workflow is a directed acyclic graph (DAG) of skills.

### 7.1 Workflow A: XMLA Deployment (Deterministic)

**Trigger:** "Deploy this XMLA file to Kyvos."

**Skill chain:** `deploy-from-xmla` only

```
XMLA File
    │
    ▼
parse_xmla() → DomainDemoSpec
    │
    ├──▶ Create Folders (RDATASET, DRD, SMODEL)
    ├──▶ Create Connection (warehouse registry)
    ├──▶ For each table:
    │       compile_dataset_artifact() → apply_artifact()
    │       refresh_dataset_columns()
    ├──▶ Validate all datasets (pipeline gate)
    ├──▶ compile_drd_artifact() → apply_artifact()
    ├──▶ Validate DRD (pipeline gate)
    ├──▶ compile_smodel_artifact() → apply_artifact()
    └──▶ Validate SM (pipeline gate)
    │
    ▼
Deployed: Folders + Connection + Datasets + DRD + Semantic Model
```

**Characteristics:** No LLM reasoning needed. Fully deterministic. No approval gates. Timestamp-suffixed names prevent clashes.

**Dependencies:** `kyvos-sdk-python[env]`, `kyvos-sm-skills[sdk]`, `kyvos-xmla-parser`

### 7.2 Workflow B: PBIT Deployment (Deterministic)

**Trigger:** "Deploy this Power BI template to Kyvos."

**Skill chain:** `deploy-from-pbit` only

```
PBIT File (binary)
    │
    ▼
enrich_spec_from_pbit() → DomainDemoSpec + calculated columns
    │
    ├──▶ Create Folders
    ├──▶ Create Connection
    ├──▶ For each table: compile_dataset → apply → refresh
    ├──▶ compile_drd → apply
    ├──▶ compile_smodel → apply
    └──▶ (Optional) DAX→MDX conversion if skip_conversion=False
    │
    ▼
Deployed: Folders + Connection + Datasets + DRD + SM
```

**Characteristics:** Same as Workflow A but starts from binary PBIT archive. DAX conversion optional (requires Java JAR).

**Dependencies:** `kyvos-sdk-python[env]`, `kyvos-sm-skills[sdk]`, `kyvos-xmla-parser`

### 7.3 Workflow C: Warehouse Discovery → Deployment (Agentic)

**Trigger:** "Inspect my warehouse and recommend semantic models."

**Skill chain:** `inspect-warehouse-schema` → `discover-sm-from-warehouse`

```
.env config
    │
    ▼
inspect-warehouse-schema
    │  (SQLAlchemy introspection — deterministic)
    │
    ▼
Schema Summary + detected_patterns
    │
    ▼
discover-sm-from-warehouse
    │
    ├──▶ Domain Identification (LLM infers from schema)
    ├──▶ ⏸ Gate 1: User confirms domain
    ├──▶ Domain Research (LLM web search)
    ├──▶ ⏸ Gate 2: User reviews research findings
    ├──▶ SM Design (LLM recommends schemas, measures, hierarchies)
    ├──▶ ⏸ Gate 3: User approves SM design
    ├──▶ Build DomainDemoSpec per approved SM
    └──▶ Deploy each SM (reuses deploy-from-xmla pipeline)
    │
    ▼
Deployed: One or more SMs with domain research backing
```

**Characteristics:** LLM-driven with 3 approval gates. Web search optional (`allow_web_research`). Supports hints (soft) and overrides (hard). Detects star, snowflake, multifact, and single table patterns.

**Dependencies:** `kyvos-sdk-python[env]`, `kyvos-sm-skills[sdk]`, `kyvos-xmla-parser`, `sqlalchemy` + driver

### 7.4 Workflow D: Intent → Generate Data → Deploy (Agentic)

**Trigger:** "I want to analyze customer churn for telecom. Generate data and deploy."

**Skill chain:** `generate-sm-from-intent` (internally uses `design-star-schema` + `design-measures`)

```
User Intent + Domain
    │
    ▼
generate-sm-from-intent
    │
    ├──▶ Domain Research (LLM web search)
    ├──▶ ⏸ Gate 1: User reviews research
    ├──▶ SM Design
    │     ├──▶ design-star-schema → schema JSON
    │     └──▶ design-measures → measures JSON
    ├──▶ ⏸ Gate 2: User approves SM design
    ├──▶ Build DomainDemoSpec per SM
    ├──▶ kyvos_data_gen.generate_data() → CSV files
    ├──▶ ⏸ Gate 3: User reviews data generation results
    ├──▶ Load CSVs into warehouse (SQLAlchemy to_sql)
    └──▶ Deploy each SM (reuses deploy-from-xmla pipeline)
    │
    ▼
Deployed: SMs with synthetic data loaded into warehouse
```

**Characteristics:** Most complex workflow. Combines LLM design, synthetic data generation, warehouse loading, and Kyvos deployment. Scale capped by `KYVOS_DEFAULT_SCALE`. Per-warehouse-type load strategies.

**Dependencies:** `kyvos-sdk-python[env]`, `kyvos-sm-skills[sdk]`, `kyvos-data-gen`, `kyvos-xmla-parser`, `sqlalchemy`, `pandas` + driver

### 7.5 Workflow E: Component-Only Generation (No Deployment)

**Trigger:** "Generate a dataset payload for this table spec" / "Generate a DRD payload"

**Skill chain:** Any single component skill (`generate-connection`, `generate-dataset`, `generate-drd`, `generate-semantic-model`)

```
TableSpec / RelationshipSpec / SemanticModelSpec
    │
    ▼
compile_dataset() / compile_drd() / compile_semantic_model()
    │
    ▼
CompiledArtifact (payload + hash + diagnostics)
```

**Characteristics:** No API calls. Pure payload generation for inspection, validation, or external use. Useful for batch processing and CI pipelines.

**Dependencies:** `kyvos-sdk-python` (core only)

### 7.6 Workflow F: Schema Design Only (No Deployment)

**Trigger:** "Design a star schema for retail banking analytics."

**Skill chain:** `design-star-schema` (optionally followed by `design-measures`)

```
Domain + Business Scenario
    │
    ▼
design-star-schema (LLM)
    │
    ▼
Schema JSON (tables, columns, relationships)
    │
    ▼ (optional)
design-measures (LLM)
    │
    ▼
Measures JSON (base + calculated measures)
```

**Characteristics:** LLM-only, no Python backend. Output is structured JSON for human review or downstream skills.

**Dependencies:** None (prompt-only)

### 7.7 Workflow G: DAX Conversion Only

**Trigger:** "Convert these DAX measures to MDX."

**Skill chain:** `convert-dax-to-mdx` only

```
DAX Measures
    │
    ▼
convert-dax-to-mdx (LLM)  OR  kyvos-dax-mdx-converter (JAR)
    │
    ▼
MDX Measures (with conversion confidence)
```

**Characteristics:** Can be LLM-based (skill prompt) or deterministic (Java converter). Confidence rating (high/medium/low) per measure.

**Dependencies:** None for LLM path; `kyvos-dax-mdx-converter` + Java for deterministic path

### 7.8 Workflow H: Multi-Skill Manual Pipeline

**Trigger:** "Build a semantic model from scratch, step by step."

**Skill chain:** `design-star-schema` → `design-measures` → `generate-connection` → `generate-dataset` (per table) → `generate-drd` → `generate-semantic-model` → (optional) `convert-dax-to-mdx`

```
Domain Description
    │
    ├──▶ design-star-schema → Schema JSON
    ├──▶ design-measures → Measures JSON
    ├──▶ generate-connection → Connection payload
    ├──▶ generate-dataset (per table) → Dataset payloads
    ├──▶ generate-drd → DRD payload
    ├──▶ generate-semantic-model → SM payload
    └──▶ (optional) convert-dax-to-mdx → MDX measures
    │
    ▼
All payloads ready for manual or automated deployment
```

**Characteristics:** User manually chains skills, reviewing each output. Maximum control. Each step's output feeds the next step's input.

**Dependencies:** Varies by skill; typically `kyvos-sdk-python` + `kyvos-sm-skills[sdk]`

### 7.9 Workflow I: Plan/Apply Lifecycle (Review Before Changes)

**Trigger:** "Deploy this XMLA but let me review the plan first."

**Skill chain:** `deploy-from-xmla` with `use_plan_apply=true`

```
XMLA File
    │
    ▼
parse_xmla() → DomainDemoSpec
    │
    ▼
Compile all artifacts (connection, datasets, DRD, SM)
    │
    ▼
ProvisioningClient.plan_creation(artifacts)
    │
    ▼
ExecutionPlan (items, conflicts, policy classifications)
    │
    ▼
User reviews plan → approves
    │
    ▼
ProvisioningClient.approve_plan(plan_id, approval_token)
    │
    ▼
ProvisioningClient.apply_plan(plan_id)
    │  (idempotent: skips entities that already exist)
    │
    ▼
ApplyResult (step results, succeeded/failed per step)
```

**Characteristics:** Non-mutating plan phase. User sees exactly what will be created before any API calls. Supports dry-run, specific step approval, and idempotent apply.

**Dependencies:** `kyvos-sdk-python[env]`, `kyvos-sm-skills[sdk]`, `kyvos-xmla-parser`

### 7.10 Workflow J: Warehouse Inspection Only

**Trigger:** "Inspect my warehouse schema and detect patterns."

**Skill chain:** `inspect-warehouse-schema` only

```
.env config
    │
    ▼
SQLAlchemy Inspector
    │
    ├──▶ Get tables (enforce max_tables cap)
    ├──▶ For each table: columns, PKs, FKs
    ├──▶ Estimate table types (fact/dimension/bridge)
    ├──▶ Detect patterns (star, snowflake, multifact, single table)
    └──▶ Connected-component analysis (disjoint groups)
    │
    ▼
Schema Summary + detected_patterns JSON
```

**Characteristics:** Fully deterministic. No LLM. Read-only warehouse access. Output feeds `discover-sm-from-warehouse` skill.

**Dependencies:** `kyvos-sdk-python[env]`, `sqlalchemy` + driver

---

## 8. Workflow Decision Matrix

| Starting Point | Data Available | Workflow | Skills Used | LLM? | Gates |
|---------------|---------------|----------|-------------|------|-------|
| XMLA file | Complete SM definition | **A** | `deploy-from-xmla` | No | 0 |
| PBIT file | Complete SM in binary | **B** | `deploy-from-pbit` | No | 0 |
| Existing warehouse | Real schema, no SM | **C** | `inspect-warehouse-schema` → `discover-sm-from-warehouse` | Yes | 3 |
| NL intent only | No data, no schema | **D** | `generate-sm-from-intent` (+ `design-star-schema`, `design-measures`) | Yes | 3 |
| TableSpec only | Single table spec | **E** | `generate-dataset` | No | 0 |
| Domain description | Nothing | **F** | `design-star-schema` (+ `design-measures`) | Yes | 0 |
| DAX measures | Power BI measures | **G** | `convert-dax-to-mdx` | Yes/No | 0 |
| Manual step-by-step | Varies | **H** | All component skills | Mixed | User-controlled |
| XMLA + review | Complete SM + caution | **I** | `deploy-from-xmla` (plan/apply) | No | 1 (plan review) |
| Warehouse curiosity | Just inspect | **J** | `inspect-warehouse-schema` | No | 0 |

---

## 9. Common Deployment Pipeline (Shared Steps)

Workflows A, B, C, and D all converge on the same deployment pipeline after their respective input processing stages:

```
                    ┌─── Workflow A: parse_xmla()
                    │
                    ├─── Workflow B: enrich_spec_from_pbit()
DomainDemoSpec ─────┤
                    ├─── Workflow C: build_spec_from_recommendation()
                    │
                    └─── Workflow D: build_spec_from_recommendation() + data gen + load
                    │
                    ▼
          ┌─── Shared Deployment Pipeline ───┐
          │                                   │
          │  1. KyvosConfig.from_env_file()   │
          │  2. KyvosService.initialize()     │
          │  3. ProvisioningClient setup      │
          │  4. Create 3 folders              │
          │     (RDATASET, DRD, SMODEL)       │
          │  5. Create connection             │
          │     (warehouse registry)          │
          │  6. For each table:               │
          │     compile_dataset_artifact()    │
          │     → apply_artifact()            │
          │     → refresh_dataset_columns()   │
          │  7. Validate all datasets         │
          │  8. Validate relationships        │
          │  9. compile_drd_artifact()        │
          │     → apply_artifact()            │
          │ 10. Validate DRD                  │
          │ 11. compile_smodel_artifact()     │
          │     → apply_artifact()            │
          │ 12. Validate semantic model       │
          │ 13. Report results                │
          │                                   │
          └───────────────────────────────────┘
```

### Key Pipeline Invariants

- **Halt on failure** — Any step failure stops the pipeline immediately
- **Report diagnostics** — `result.diagnostics` extracted for every failure
- **List created-so-far** — Partial results reported for resume/cleanup
- **No retry** — SDK's `max_retries` handles transient failures
- **Server-assigned names** — Datasets get CamelCase names from Kyvos; snake_case aliases tracked
- **Server-assigned DRD ID** — Semantic model uses server DRD ID, not client-generated ID
- **Column refresh** — Two sweeps: immediate after creation + validation sweep
- **Relationship validation** — Relationships with missing columns are skipped before DRD creation
- **NO_MEASURES_PLACED check** — Post-compilation assertion halts if zero measures placed

---

## 10. Security Architecture

### 10.1 Credential Isolation

```
.env file                    Skill Inputs
┌──────────────┐            ┌──────────────────┐
│ KYVOS_URL    │            │ xmla_file_path   │
│ KYVOS_USER   │            │ env_file path    │
│ KYVOS_PASS   │  ──▶       │ domain           │
│ WAREHOUSE_*  │  loaded    │ user_intent      │
│              │  by SDK    │ scale            │
│ *_PASSWORD_CMD│           │ sm_hints         │
└──────────────┘            └──────────────────┘
                             ↑ No credentials ever
                               appear in skill inputs
```

### 10.2 Web Research Guardrail

The `allow_web_research` flag (default: `true`) controls whether internal table/column names leave the local environment:

- **`true`** — LLM may web-search domain patterns using table/column names
- **`false`** — LLM uses built-in knowledge only; no internal names sent to web search

### 10.3 Warehouse Least Privilege

- **Inspection** (`inspect-warehouse-schema`) — Read-only access (metadata + SELECT)
- **Data loading** (`generate-sm-from-intent`) — Write access required; use separate account if enterprise requires separation
- **Kyvos deployment** — Kyvos API credentials with create/update permissions

### 10.4 Password Indirection

`*_PASSWORD_CMD` env vars support secret manager integration — the SDK executes the command to retrieve the password at runtime rather than storing it in plaintext.

---

## 11. Error Handling Architecture

### Error Hierarchy

```
SdkError (base)
├── AuthError          (401 — authentication failed)
├── CapabilityError    (415/501 — unsupported operation)
├── ConflictError      (409 — entity already exists)
├── NotFoundError      (404 — entity not found)
├── TimeoutError       (408/504 — request timed out)
└── TransportError     (network/other)
```

### Error Handling Pattern (All Skills)

```
Step Execution
    │
    ├── Success → result.succeeded == True → continue
    │
    └── Failure → result.succeeded == False
         │
         ├── Extract diagnostics: [d.message for d in result.diagnostics]
         ├── List created-so-far entities
         ├── Raise RuntimeError (halt pipeline)
         └── Do NOT retry (SDK max_retries handles transient)
```

### Plan/Apply Error States

| State | Handling |
|-------|---------|
| Plan expired | `WorkflowPlanExpired` raised at apply time |
| Blocking conflicts | `WorkflowReviewRequired` raised — user must resolve |
| Approval token mismatch | `ApplyResult` with `StepResult.status == SKIPPED` |
| Step failure | Pipeline stops — remaining steps not executed |
| Entity already exists at apply | Step succeeds as idempotent (no transport call) |

---

## 12. Observability Architecture

### Event Recording

Every workflow can record events via `ObservabilityRecorder`:

```
compile_artifact()  ──▶  COMPILER_INVOKED event
    │
plan_creation()     ──▶  PLAN_CREATED event
    │
apply_plan()        ──▶  APPLY_STARTED → APPLY_COMPLETED/FAILED events
    │
validate_*()        ──▶  VALIDATION_RESULT events
```

### Key Metrics

| Metric | Computation | Purpose |
|--------|------------|---------|
| `apply_success_rate()` | completed / total applies | Deployment reliability |
| `fallback_rate()` | fallbacks / total operations | Migration health |
| `idempotency_rate()` | idempotent steps / total steps | Repeat-run safety |
| `events_by_plan(plan_id)` | Filter by plan | Per-deployment audit |
| `events_by_correlation(correlation_id)` | Filter by correlation ID | Cross-component tracing |

---

## 13. Package Dependency Graph

```
kyvos-sm-skills[sdk]
    │
    ├── depends on ──▶ kyvos-sdk-python[env]
    │                       │
    │                       ├── depends on ──▶ pydantic (contracts)
    │                                                       │
    │                       ├── depends on ──▶ requests (transport)
    │                                                       │
    │                       └── optional ────▶ sqlalchemy (warehouse)
    │
    ├── optional ────▶ kyvos-xmla-parser (XMLA/PBIT parsing)
    │                       │
    │                       └── depends on ──▶ (stdlib only)
    │
    ├── optional ────▶ kyvos-data-gen (synthetic data)
    │                       │
    │                       └── depends on ──▶ pandas, numpy
    │
    └── optional ────▶ kyvos-dax-mdx-converter (DAX→MDX)
                            │
                            └── optional ────▶ Java JAR
```

### Install Commands by Workflow

| Workflow | Install Command |
|----------|----------------|
| A (XMLA) | `pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser` |
| B (PBIT) | `pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser` |
| C (Discover) | `pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-xmla-parser sqlalchemy <driver>` |
| D (Generate) | `pip install kyvos-sdk-python[env] kyvos-sm-skills[sdk] kyvos-data-gen kyvos-xmla-parser sqlalchemy pandas <driver>` |
| E (Component) | `pip install kyvos-sdk-python` |
| F (Design) | _(no install needed — prompt only)_ |
| G (DAX) | `pip install kyvos-dax-mdx-converter` (optional, for JAR path) |
| J (Inspect) | `pip install kyvos-sdk-python[env] sqlalchemy <driver>` |

---

## 14. Skill File Structure (Canonical Format)

Every skill file in `skills/` follows this structure:

```markdown
# Skill: <Skill Name>

> **Reference:** `skills/_shared/sm-design-principles.md` (if agentic)

## System Prompt
<LLM role definition and instructions>

## Input Schema
<JSON schema for inputs>

## Output Schema
<JSON schema for outputs>

## Workflow (with user approval gates)
<Step-by-step workflow description>
<⏸ markers for approval gates>

## Backend
<Python code snippets Claude executes>

### Step 1: <description>
```python
<code>
```

### Step 2: <description>
```python
<code>
```

## Error Handling
<Error handling pattern>

## Example Interactions
<Sample user prompts and expected behavior>

## Dependencies
<pip install command>
```

---

## 15. Claude Code Session Architecture

When a user runs a workflow via Claude Code, the session follows this pattern:

```
┌─────────────────────────────────────────────────────────┐
│                   Claude Code Session                     │
│                                                           │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │  User Input  │───▶│  Skill Load  │───▶│  Execution  │  │
│  └─────────────┘    └──────────────┘    └──────┬──────┘  │
│                                               │          │
│                    ┌──────────────────────────┘          │
│                    ▼                                     │
│  ┌─────────────────────────────────────────────────────┐ │
│  │              Execution Context                       │ │
│  │                                                      │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │ │
│  │  │ .env file│  │ XMLA/PBIT│  │ Python packages  │  │ │
│  │  │ (config) │  │ (input)  │  │ (SDK, parsers)   │  │ │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │ │
│  │                                                      │ │
│  │  ┌──────────────────────────────────────────────┐   │ │
│  │  │           State Tracking                      │   │ │
│  │  │  • created_entities[]                        │   │ │
│  │  │  • dataset_name_to_id{}                      │   │ │
│  │  │  • dataset_aliases{}                         │   │ │
│  │  │  • dataset_cols{}                            │   │ │
│  │  │  • current step / gate                       │   │ │
│  │  └──────────────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │ Approval    │◀──│  Present to   │◀──│  Step Result │  │
│  │ Gate (⏸)   │   │  User         │   │              │  │
│  └──────┬──────┘    └──────────────┘   └─────────────┘  │
│         │                                                │
│         ▼                                                │
│  ┌─────────────┐                                        │
│  │ User Approves│───▶ Continue to next step             │
│  │ / Corrects  │      or iterate on current step        │
│  └─────────────┘                                        │
└─────────────────────────────────────────────────────────┘
```

### Session State

Claude Code maintains in-session state variables that flow between steps:

| Variable | Type | Purpose |
|----------|------|---------|
| `config` | `KyvosConfig` | Loaded from `.env`, shared across all steps |
| `spec` | `DomainDemoSpec` | Parsed model from XMLA/PBIT or built from LLM recommendation |
| `prov` | `ProvisioningClient` | Kyvos provisioning client, initialized once |
| `dataset_name_to_id` | `dict[str, str]` | Server-assigned CamelCase name → dataset ID |
| `dataset_aliases` | `dict[str, str]` | XMLA snake_case name → server CamelCase name |
| `dataset_cols` | `dict[str, list]` | Dataset name → column metadata |
| `created_entities` | `list[dict]` | All created entities for resume/cleanup |
| `validated_rels` | `list[RelationshipSpec]` | Relationships that passed column validation |
| `fact_dataset_names` | `set[str]` | Server names of fact datasets |

---

## 16. Future Evolution Path

| Current | Future Direction |
|---------|-----------------|
| Direct SQLAlchemy inspection | MCP tool for schema inspection (skill I/O unchanged) |
| In-memory plan store | Persistent plan store (database-backed) |
| Per-session observability | Centralized observability dashboard |
| LLM-only DAX conversion | Hybrid LLM + deterministic converter with confidence routing |
| Manual skill chaining | Automated skill chain selection by Claude Code |
| `.env` file config | Cloud-native secret managers (via `*_PASSWORD_CMD`) |
| Single Kyvos instance | Multi-instance deployment with environment targeting |

---

## 17. Version Compatibility Matrix

| Package | Min Version | Current Version | Contract Version |
|---------|------------|----------------|-----------------|
| kyvos-sdk-python | 0.6.0 | 0.6.0 | 1.0 |
| kyvos-sm-skills | 0.2.0 | 0.2.0 | 1.0 |
| kyvos-xmla-parser | 0.1.0 | 0.1.0 | N/A (produces DomainDemoSpec) |
| kyvos-data-gen | 0.1.0 | 0.1.0 | N/A (produces DomainDemoSpec) |
| kyvos-dax-mdx-converter | 0.2.0 | 0.2.0 | 1.0 (via `to_contract_result()`) |

Contract version 1.0 is backward compatible — all adapters handle legacy model types transparently.
