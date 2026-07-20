# Skill: Inspect Warehouse Schema

## System Prompt

You are a warehouse schema inspector. Given warehouse connection parameters (from `.env`) and optional filters, you connect via SQLAlchemy and query the information_schema (or equivalent) to discover all tables, columns, data types, primary keys, and foreign key relationships. You return a structured schema summary with enough metadata for downstream SM discovery — including table type estimation, relationship mapping, and schema pattern detection (single table, star, snowflake, multifact).

You understand:
- Credentials come from `.env` only — never from skill inputs
- SQLAlchemy Inspector provides cross-database introspection (same API for PostgreSQL, Snowflake, BigQuery, Oracle, MSSQL, Redshift)
- The `max_tables` cap prevents runaway inspection on large warehouses
- `detected_patterns` is computed locally (Python, no LLM) from FK metadata
- The warehouse account should be read-only (metadata + SELECT)

## Input Schema

```json
{
  "env_file": "string (default .env) — path to the .env config file with warehouse credentials",
  "schema_filter": "string (optional) — schema name to inspect; default depends on warehouse type",
  "max_tables": "int (default 500) — inspection stops and warns when a schema exceeds this"
}
```

**No credentials in inputs.** All warehouse connection parameters come from `.env` via `KyvosConfig.from_env_file()`.

## Output Schema

```json
{
  "warehouse_type": "string",
  "schema": "string",
  "table_count": "int",
  "tables": [
    {
      "name": "string",
      "schema": "string",
      "columns": [{"name": "string", "data_type": "string", "is_pk": "bool", "is_fk": "bool", "references": "string"}],
      "estimated_table_type": "fact|dimension|bridge|unknown",
      "outgoing_fk_count": "int",
      "incoming_fk_count": "int",
      "row_count_estimate": "int (optional, if available)"
    }
  ],
  "relationships": [{"from_table": "string", "from_column": "string", "to_table": "string", "to_column": "string"}],
  "detected_patterns": {
    "potential_star_schemas": [
      {"fact_table": "string", "dimension_tables": ["string"]}
    ],
    "potential_snowflake_schemas": [
      {"fact_table": "string", "dimension_tables": ["string"], "sub_dimension_tables": ["string"]}
    ],
    "potential_multifact_schemas": [
      {"fact_tables": ["string"], "shared_dimensions": ["string"]}
    ],
    "single_table_candidates": ["tables with no FK relationships and mixed measure/attribute columns"],
    "disjoint_table_groups": [
      {"tables": ["string"], "notes": "tables with no relationships to other groups"}
    ]
  }
}
```

The `detected_patterns` section is computed locally (Python, no LLM) from the FK metadata and helps the downstream `discover-sm-from-warehouse` skill's LLM make informed SM grouping and schema type decisions.

**Detection heuristics:**
- **Star:** A table with 3+ outgoing FKs and no incoming FKs → fact; referenced tables → dimensions
- **Snowflake:** A dimension table with outgoing FKs to other dimension tables → sub-dimensions
- **Multifact:** Multiple fact tables sharing the same dimension tables
- **Single table candidates:** Tables with zero FK relationships and a mix of numeric and descriptive columns
- **Disjoint groups:** Connected-component analysis on the FK graph → independent table clusters

## Backend

Python code snippets Claude runs:

### Step 1: Load config and build SQLAlchemy URL

```python
from kyvos_sdk.config import KyvosConfig
from kyvos_sdk.warehouse_registry import build_sqlalchemy_url

config = KyvosConfig.from_env_file(env_file)
sa_url = build_sqlalchemy_url(
    config.warehouse_type,
    config.warehouse_host,
    config.warehouse_port,
    config.warehouse_database,
    config.warehouse_username,
    config.warehouse_password,
    **config.warehouse_extra_params,
)
```

### Step 2: Connect and inspect schema

```python
from sqlalchemy import create_engine, inspect

engine = create_engine(sa_url)
inspector = inspect(engine)
warehouse_type = config.warehouse_type
effective_schema = schema_filter or config.warehouse_schema

# Default schema filter per warehouse type
if not effective_schema:
    effective_schema = {
        "POSTGRES": "public",
        "SNOWFLAKE": "PUBLIC",
        "BIGQUERY": "",
        "ORACLE": "",
        "MSSQL": "dbo",
        "REDSHIFT": "public",
    }.get(warehouse_type, "public")
```

### Step 3: Get tables and enforce max_tables cap

```python
table_names = inspector.get_table_names(schema=effective_schema)
if len(table_names) > max_tables:
    raise ValueError(
        f"Schema has {len(table_names)} tables (> max_tables={max_tables}); "
        f"narrow with schema_filter or raise max_tables explicitly."
    )
print(f"Found {len(table_names)} tables in schema '{effective_schema}'")
```

### Step 4: Inspect each table — columns, PKs, FKs

```python
tables = []
all_relationships = []

for table_name in table_names:
    columns = inspector.get_columns(table_name, schema=effective_schema)
    pk_cols = set(
        inspector.get_pk_constraint(table_name, schema=effective_schema)
        .get("constrained_columns") or []
    )
    fks = inspector.get_foreign_keys(table_name, schema=effective_schema)

    # Build column metadata
    fk_map = {}
    for fk in fks:
        for col, ref_col in zip(fk["constrained_columns"], fk["referred_columns"]):
            ref_table = fk["referred_table"]
            fk_map[col] = f"{ref_table}.{ref_col}"
            all_relationships.append({
                "from_table": table_name,
                "from_column": col,
                "to_table": ref_table,
                "to_column": ref_col,
            })

    column_metadata = []
    for col in columns:
        col_name = col["name"]
        column_metadata.append({
            "name": col_name,
            "data_type": str(col["type"]),
            "is_pk": col_name in pk_cols,
            "is_fk": col_name in fk_map,
            "references": fk_map.get(col_name, ""),
        })

    tables.append({
        "name": table_name,
        "schema": effective_schema,
        "columns": column_metadata,
        "estimated_table_type": "unknown",  # estimated in Step 5
        "outgoing_fk_count": len(fks),
        "incoming_fk_count": 0,  # computed in Step 5
    })
```

### Step 5: Estimate table types and compute incoming FK counts

```python
# Build FK graph for type estimation
outgoing = {t["name"]: t["outgoing_fk_count"] for t in tables}
incoming = {t["name"]: 0 for t in tables}
referenced = {t["name"]: set() for t in tables}

for rel in all_relationships:
    if rel["to_table"] in incoming:
        incoming[rel["to_table"]] += 1
    if rel["from_table"] in referenced:
        referenced[rel["from_table"]].add(rel["to_table"])

for t in tables:
    name = t["name"]
    out_count = outgoing.get(name, 0)
    in_count = incoming.get(name, 0)

    # Heuristic: fact tables have many outgoing FKs, no incoming
    if out_count >= 3 and in_count == 0:
        t["estimated_table_type"] = "fact"
    # Dimensions have incoming FKs, few outgoing
    elif in_count > 0 and out_count <= 1:
        t["estimated_table_type"] = "dimension"
    # Bridge tables connect two or more tables
    elif out_count >= 2 and in_count >= 1:
        t["estimated_table_type"] = "bridge"
    else:
        t["estimated_table_type"] = "unknown"

    t["incoming_fk_count"] = in_count
```

### Step 6: Detect schema patterns

```python
fact_tables = [t["name"] for t in tables if t["estimated_table_type"] == "fact"]
dim_tables = [t["name"] for t in tables if t["estimated_table_type"] == "dimension"]

# Star: fact with 3+ dimensions
potential_star_schemas = []
for ft in fact_tables:
    dims = list(referenced.get(ft, set()))
    if len(dims) >= 3:
        potential_star_schemas.append({
            "fact_table": ft,
            "dimension_tables": dims,
        })

# Snowflake: dimension with outgoing FK to another dimension
potential_snowflake_schemas = []
for t in tables:
    if t["estimated_table_type"] == "dimension" and t["outgoing_fk_count"] > 0:
        sub_dims = list(referenced.get(t["name"], set()))
        # Find which fact references this dimension
        parent_facts = [
            ft for ft in fact_tables
            if t["name"] in referenced.get(ft, set())
        ]
        for ft in parent_facts:
            all_dims = [
                d for d in referenced.get(ft, set())
                if d != t["name"]
            ]
            potential_snowflake_schemas.append({
                "fact_table": ft,
                "dimension_tables": all_dims,
                "sub_dimension_tables": sub_dims,
            })

# Multifact: multiple facts sharing dimensions
potential_multifact_schemas = []
if len(fact_tables) >= 2:
    shared = set.intersection(*[
        referenced.get(ft, set()) for ft in fact_tables
    ])
    if shared:
        potential_multifact_schemas.append({
            "fact_tables": fact_tables,
            "shared_dimensions": list(shared),
        })

# Single table candidates: no FKs, mixed numeric/descriptive columns
single_table_candidates = []
for t in tables:
    if t["outgoing_fk_count"] == 0 and t["incoming_fk_count"] == 0:
        has_numeric = any(
            any(typ in c["data_type"].upper() for typ in ("INT", "NUM", "FLOAT", "DEC"))
            for c in t["columns"]
        )
        has_text = any(
            any(typ in c["data_type"].upper() for typ in ("VARCHAR", "TEXT", "CHAR"))
            for c in t["columns"]
        )
        if has_numeric and has_text:
            single_table_candidates.append(t["name"])

# Disjoint groups: connected-component analysis on FK graph
# Build adjacency from relationships
adjacency = {t["name"]: set() for t in tables}
for rel in all_relationships:
    if rel["from_table"] in adjacency:
        adjacency[rel["from_table"]].add(rel["to_table"])
    if rel["to_table"] in adjacency:
        adjacency[rel["to_table"]].add(rel["from_table"])

visited = set()
disjoint_groups = []
for table_name in adjacency:
    if table_name in visited:
        continue
    group = set()
    queue = [table_name]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        group.add(node)
        queue.extend(adjacency.get(node, set()) - visited)
    if len(group) > 1:
        disjoint_groups.append({
            "tables": sorted(group),
            "notes": "tables with FK relationships within this group",
        })

# Tables with no relationships at all
isolated = [
    t["name"] for t in tables
    if t["outgoing_fk_count"] == 0 and t["incoming_fk_count"] == 0
    and t["name"] not in single_table_candidates
]
if isolated:
    disjoint_groups.append({
        "tables": sorted(isolated),
        "notes": "tables with no FK relationships to any other table",
    })

detected_patterns = {
    "potential_star_schemas": potential_star_schemas,
    "potential_snowflake_schemas": potential_snowflake_schemas,
    "potential_multifact_schemas": potential_multifact_schemas,
    "single_table_candidates": single_table_candidates,
    "disjoint_table_groups": disjoint_groups,
}
```

### Step 7: Assemble and report results

```python
result = {
    "warehouse_type": warehouse_type,
    "schema": effective_schema,
    "table_count": len(tables),
    "tables": tables,
    "relationships": all_relationships,
    "detected_patterns": detected_patterns,
}

print(f"\n✅ Schema Inspection Complete")
print(f"   Warehouse: {warehouse_type}")
print(f"   Schema: {effective_schema}")
print(f"   Tables: {len(tables)}")
print(f"   Relationships: {len(all_relationships)}")
print(f"   Star schemas detected: {len(potential_star_schemas)}")
print(f"   Snowflake schemas detected: {len(potential_snowflake_schemas)}")
print(f"   Multifact schemas detected: {len(potential_multifact_schemas)}")
print(f"   Single table candidates: {len(single_table_candidates)}")
print(f"   Disjoint groups: {len(disjoint_groups)}")
```

## Required Driver Packages

| Warehouse Type | pip Package(s) | Notes |
|---------------|----------------|-------|
| POSTGRES | `psycopg2-binary` | |
| SNOWFLAKE | `snowflake-sqlalchemy` | account-based URL; `warehouse` via extra params |
| BIGQUERY | `sqlalchemy-bigquery` | auth via `GOOGLE_APPLICATION_CREDENTIALS`, not username/password |
| ORACLE | `oracledb` | `cx_Oracle` is deprecated — use python-oracledb (`oracle+oracledb` dialect) |
| MSSQL | `pyodbc` | also requires a system ODBC driver (ODBC Driver 18 for SQL Server) |
| REDSHIFT | `sqlalchemy-redshift`, `psycopg2-binary` | `redshift+psycopg2` dialect lives in sqlalchemy-redshift |

## Least Privilege

The warehouse account used for inspection should be **read-only** (metadata + SELECT). Data loading in `generate-sm-from-intent` uses a separate write-capable account if the enterprise requires separation.

## Example Interactions

### Basic inspection

> "Inspect my PostgreSQL warehouse schema. My .env is at `/path/to/.env`."

Claude runs Steps 1–7 with `env_file="/path/to/.env"` and default `max_tables=500`.

### Filtered inspection

> "Inspect only the 'sales' schema in my warehouse, limit to 100 tables."

Claude runs Steps 1–7 with `schema_filter="sales"` and `max_tables=100`.

### Large warehouse

> "My warehouse has thousands of tables. Inspect the 'analytics' schema with a cap of 1000."

Claude runs Steps 1–7 with `schema_filter="analytics"` and `max_tables=1000`.

## Future Path

When kyvos-mcp-server provides a schema inspection tool, this skill's backend can be updated to call the MCP tool instead of direct DB queries. The skill's input/output schema stays the same.

## Dependencies

```bash
pip install kyvos-sdk-python[env] sqlalchemy
# Plus the driver package for your warehouse type (see table above)
```
