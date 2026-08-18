# API Reference

> **Deprecation notice:** The model and generator APIs documented below are retained for backward compatibility. New code should use the typed contracts in `kyvos_sdk.contracts.domain` and the pure compilers in `kyvos_sdk.compiler`. See `quickstart.md` for the recommended pattern.

## Models (`kyvos_sm_skills.models`)

### `ColumnSpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Column name |
| `data_type` | `str` | required | SQL type (e.g. `"INTEGER"`, `"VARCHAR(100)"`) |
| `nullable` | `bool` | `True` | Whether the column allows NULL |
| `is_primary_key` | `bool` | `False` | Whether this is a PK |
| `is_foreign_key` | `bool` | `False` | Whether this is an FK |
| `references` | `str \| None` | `None` | FK reference (`"schema.table.column"`) |
| `description` | `str` | `""` | Column description |
| `column_samples` | `list[str]` | `[]` | Sample values |
| `source_column` | `str \| None` | `None` | Original DB column name |
| `display_folder` | `str` | `""` | XMLA display folder |

### `TableSpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Table name (snake_case) |
| `schema_name` | `str` | `"public"` | Database schema |
| `table_type` | `str` | `"dimension"` | `"fact"`, `"dimension"`, `"bridge"`, or `"snowflake_dimension"` |
| `columns` | `list[ColumnSpec]` | `[]` | Column definitions |
| `description` | `str` | `""` | Table description |
| `row_count_target` | `int \| None` | `None` | Target row count |
| `is_hidden` | `bool` | `False` | Whether table is hidden |

### `DatasetSpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Dataset name |
| `source_table` | `str` | required | Source table name |
| `connection_name` | `str` | required | Connection name |
| `columns` | `list[str]` | `[]` | Column names |

### `RelationshipSpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `left_dataset` | `str` | required | Left dataset name |
| `left_column` | `str` | required | Left join column |
| `right_dataset` | `str` | required | Right dataset name |
| `right_column` | `str` | required | Right join column |
| `relationship_type` | `str` | `"many_to_one"` | Relationship type |
| `semantic_role` | `str` | `"auto"` | Role classification |
| `active` | `bool` | `True` | Whether relationship is active |
| `description` | `str` | `""` | Description |

### `MeasureSpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Measure name |
| `expression` | `str` | required | MDX expression (empty for base) |
| `format_string` | `str` | `"#,##0"` | Format string |
| `description` | `str` | `""` | Description |
| `is_calculated` | `bool` | `False` | Whether this is a calculated measure |
| `source_dataset` | `str \| None` | `None` | Source dataset |
| `aggregation_type` | `str` | `"sum"` | Aggregation type |
| `source_column` | `str \| None` | `None` | Source column (base measures) |
| `is_hidden` | `bool` | `False` | Whether measure is hidden |

### `HierarchySpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Hierarchy name |
| `levels` | `list[str]` | required | Level column names |
| `source_dataset` | `str \| None` | `None` | Source dimension dataset |
| `is_parent_child` | `bool` | `False` | Whether this is a parent-child hierarchy |
| `has_alternate_path` | `bool` | `False` | Whether this has alternate paths |
| `parent_column` | `str \| None` | `None` | Parent column (PCH only) |
| `child_column` | `str \| None` | `None` | Child column (PCH only) |
| `custom_rollup_weight_column` | `str \| None` | `None` | Custom rollup weight column |
| `pc_level_naming_pattern` | `str` | `"Level_*"` | PCH level naming pattern |

### `SemanticModelSpec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Model name |
| `datasets` | `list[DatasetSpec]` | `[]` | Datasets |
| `relationships` | `list[RelationshipSpec]` | `[]` | Relationships |
| `measures` | `list[MeasureSpec]` | `[]` | Measures |
| `hierarchies` | `list[HierarchySpec]` | `[]` | Hierarchies |

## Generators

### `generate_connection_json(name, host, port, database, username, password, db_type, db_version)`

Returns `dict[str, Any]` — Kyvos connection JSON payload.

### `generate_connection_xml(name, host, port, database, username, password, db_type, db_version)`

Returns `str` — Kyvos connection XML string.

### `DatasetJsonGenerator(connection_name, connection_type, category_name, folder_id)`

- `.generate_json_payload(table: TableSpec) -> dict[str, Any]`

### `DatasetXmlGenerator(connection_name, ...)`

- `.generate_xml(table: TableSpec) -> str`

### `DrdJsonGenerator(drd_folder_id, drd_folder_name, ...)`

- `.generate(drd_name, dataset_name_to_id, relationships, dataset_aliases, fact_dataset_names) -> dict[str, Any]`

### `DrdXmlGenerator()`

- `.generate(drd_name, dataset_name_to_id, relationships, dataset_aliases, fact_dataset_names) -> str`

### `SimpleRel(left_dataset, left_column, right_dataset, right_column)`

Dataclass representing a dataset relationship.

### `SModelJsonGenerator(folder_id, folder_name, smodel_name, connection_name, drd_id, drd_name, drd_xml, dataset_name_to_id, dataset_columns, hierarchy_specs, semantic_measures, fact_dataset_names, connected_dim_names, ...)`

- `.generate() -> dict[str, Any]`

### `SModelXmlGenerator(smodel_name, connection_name, drd_id, drd_name, drd_xml, dataset_name_to_id, dataset_columns, hierarchy_specs, semantic_measures, fact_dataset_names, connected_dim_names, ...)`

- `.generate() -> str`

## Type Mapping (`kyvos_sm_skills.type_mapping`)

### `SQL_TO_KYVOS_XML_MAP: dict[str, tuple[str, str, str, str]]`

Maps SQL types to Kyvos type tuples `(dataTypeName, pigDataType, dataSubTypeName, fieldDataFormatType)`.

### `resolve_sql_type(sql_type: str) -> str`

Resolves a raw SQL type to its canonical Kyvos map key.

### `map_sql_to_kyvos_type(sql_type: str) -> dict[str, str]`

Maps a SQL type to the full set of Kyvos type fields.

### `field_format_value(type_info: dict) -> str`

Returns the FIELDFORMAT value for a given type info dict.
