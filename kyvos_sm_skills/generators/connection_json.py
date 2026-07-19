"""Generate Kyvos-compatible JSON for connection creation (Kyvos 2026.5+).

Emits a JSON dict suitable for ``POST /rest/v2/connections`` with
``Content-Type: application/x-www-form-urlencoded`` and the JSON payload
sent as a form-encoded ``json`` parameter.
"""

from __future__ import annotations

import time
from typing import Any


def generate_connection_json(
    name: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    db_type: str = "POSTGRES",
    db_version: str = "11",
    jdbc_url_override: str = "",
    driver_override: str = "",
) -> dict[str, Any]:
    """Generate Kyvos connection JSON payload.

    Args:
        name: Connection name.
        host: Database host.
        port: Database port.
        database: Database name.
        username: Database username.
        password: Database password.
        db_type: Database type (default: POSTGRES).
        db_version: Database version (default: 11).
        jdbc_url_override: If set, use this JDBC URL instead of the default
            PostgreSQL template. Resolved by the caller (SDK registry or
            skill snippet) — this module never imports the registry.
        driver_override: If set, use this driver class instead of
            ``org.postgresql.Driver``.

    Returns:
        Dict matching the Kyvos 2026.5 connection JSON shape.
    """
    jdbc_url = jdbc_url_override or f"jdbc:postgresql://{host}:{port}/{database}"
    driver_class = driver_override or "org.postgresql.Driver"
    now_ms = str(int(time.time() * 1000))

    properties = [
        {"name": "kyvos.connection.isEncryptedConnection", "value": "true", "encrypted": "false"},
        {"name": "kyvos.connection.provider", "value": db_type, "encrypted": "false"},
        {"name": "kyvos.connection.properties.version", "value": db_version, "encrypted": "false"},
        {"name": "kyvos.connection.rawdata.enabled", "value": "true", "encrypted": "false"},
        {"name": "kyvos.connection.sqlEngineType", "value": db_type, "encrypted": "false"},
        {"name": "kyvos.connection.rawdata.supported.engines", "value": db_type, "encrypted": "false"},
        {"name": "kyvos.connection.url", "value": jdbc_url, "encrypted": "false"},
        {"name": "kyvos.connection.lastUpdateTimestamp", "value": now_ms, "encrypted": "false"},
        {"name": "kyvos.build.spark.read.method", "value": "JAVA_JDBC", "encrypted": "false"},
        {"name": "kyvos.connection.user", "value": username, "encrypted": "false"},
        {"name": "kyvos.connection.isRead", "value": "true", "encrypted": "false"},
        {"name": "kyvos.connection.password", "value": password, "encrypted": "true"},
        {"name": "kyvos.connection.driver", "value": driver_class, "encrypted": "false"},
        {"name": "kyvos.connection.defaultsqlengine", "value": "false", "encrypted": "false"},
        {"name": "kyvos.connection.name", "value": name, "encrypted": "false"},
    ]

    return {
        "name": name,
        "accessRights": "1",
        "configuration": {
            "property": properties,
        },
    }
