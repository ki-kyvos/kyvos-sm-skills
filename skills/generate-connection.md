# Skill: Generate Connection

## System Prompt

You are a Kyvos connection designer. Given database connection parameters, you generate a Kyvos-compatible connection payload that registers a database connection in Kyvos.

You understand:
- Connections use JDBC URLs (e.g. `jdbc:postgresql://host:port/database`)
- The connection payload includes provider, driver, credentials, and Spark read configuration
- Both XML and JSON formats are supported
- Passwords are marked as encrypted in the payload

## Input Schema

```json
{
  "name": "connection_name",
  "host": "database_host",
  "port": 5432,
  "database": "database_name",
  "username": "db_user",
  "password": "db_password",
  "db_type": "POSTGRES",
  "db_version": "11"
}
```

## Output Schema

### JSON Format (Kyvos 2026.5+)

```json
{
  "name": "connection_name",
  "accessRights": "1",
  "configuration": {
    "property": [
      {"name": "kyvos.connection.url", "value": "jdbc:postgresql://host:port/db", "encrypted": "false"},
      {"name": "kyvos.connection.user", "value": "user", "encrypted": "false"},
      {"name": "kyvos.connection.password", "value": "password", "encrypted": "true"},
      ...
    ]
  }
}
```

### XML Format

Kyvos CONNECTION XML with configuration > property elements.

## Example

**Input:**
```json
{
  "name": "PostgresConnection",
  "host": "localhost",
  "port": 5432,
  "database": "sales_dw",
  "username": "analytics_user",
  "password": "secure_pass",
  "db_type": "POSTGRES",
  "db_version": "14"
}
```

**Output (JSON):** A connection payload with 15 properties including JDBC URL, credentials, driver class, and Spark read method.

## Backend

Use the SDK's pure compiler to generate deterministic XML or JSON payloads. The compiler returns a `CompiledArtifact` with `payload`, `content_hash`, `diagnostics`, and `capability_requirements`.

```python
from kyvos_sdk.compiler import compile_connection
from kyvos_sdk.contracts.artifacts import ArtifactFormat

artifact = compile_connection(
    name="PostgresConnection",
    host="localhost",
    port=5432,
    database="sales_dw",
    username="analytics_user",
    password="secure_pass",
    db_type="POSTGRES",
    db_version="14",
    fmt=ArtifactFormat.JSON,  # or ArtifactFormat.XML
)
payload = artifact.payload
```
