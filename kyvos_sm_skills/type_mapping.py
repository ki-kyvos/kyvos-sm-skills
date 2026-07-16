"""Shared SQL-to-Kyvos type mapping utilities.

Used by both XML and JSON payload generators to ensure consistent
type resolution across all entity creation modes.
"""

from __future__ import annotations

from typing import Any


# ── SQL → Kyvos type mapping ──────────────────────────────────────────────────
# sql_type → (dataTypeName, pigDataType, dataSubTypeName, fieldDataFormatType)

SQL_TO_KYVOS_XML_MAP: dict[str, tuple[str, str, str, str]] = {
    "INTEGER":   ("NUMBER", "-5", "long", "2"),
    "BIGINT":    ("NUMBER", "-5", "long", "2"),
    "SMALLINT":  ("NUMBER", "-5", "long", "2"),
    "SERIAL":    ("NUMBER", "-5", "long", "2"),
    "BIGSERIAL": ("NUMBER", "-5", "long", "2"),
    "DECIMAL":   ("NUMBER", "3", "decimal", "2"),
    "NUMERIC":   ("NUMBER", "3", "decimal", "2"),
    "FLOAT":     ("NUMBER", "8", "double", "2"),
    "REAL":      ("NUMBER", "8", "double", "2"),
    "DOUBLE":    ("NUMBER", "8", "double", "2"),
    "VARCHAR":   ("CHAR", "1", "", "1"),
    "CHAR":      ("CHAR", "1", "", "1"),
    "TEXT":      ("CHAR", "1", "", "1"),
    "DATE":      ("DATE", "93", "", "4"),
    "TIMESTAMP": ("DATE", "93", "", "4"),
    "BOOLEAN":   ("BOOLEAN", "16", "boolean", "16"),
}

# Multi-word or aliased PostgreSQL types → canonical map key.
_KYVOS_TYPE_ALIAS: dict[str, str] = {
    "DOUBLE PRECISION":          "DOUBLE",
    "CHARACTER VARYING":         "VARCHAR",
    "TIMESTAMP WITH TIME ZONE":  "TIMESTAMP",
    "TIMESTAMPTZ":               "TIMESTAMP",
    "UUID":   "VARCHAR",
    "JSONB":  "TEXT",
    "JSON":   "TEXT",
    "BYTEA":  "TEXT",
    "XML":    "TEXT",
}


def resolve_sql_type(sql_type: str) -> str:
    """Resolve a raw SQL type string to its canonical Kyvos map key.

    Strips precision/length suffixes (e.g. ``VARCHAR(255)`` → ``VARCHAR``)
    and resolves multi-word aliases (e.g. ``DOUBLE PRECISION`` → ``DOUBLE``).
    """
    base = sql_type.split("(")[0].strip().upper()
    return _KYVOS_TYPE_ALIAS.get(base, base)


def map_sql_to_kyvos_type(sql_type: str) -> dict[str, str]:
    """Map a SQL type to the full set of Kyvos type fields.

    Returns a dict with keys:
        - ``dataTypeName``
        - ``pigDataType``
        - ``dataSubTypeName``
        - ``fieldDataFormatType``
        - ``originalDataTypeName``
        - ``isTimestamp``
    """
    canonical = resolve_sql_type(sql_type)
    is_timestamp = canonical == "TIMESTAMP"

    data_type_name, pig_data_type, data_sub_type, field_format_data_type = (
        SQL_TO_KYVOS_XML_MAP.get(canonical, ("CHAR", "1", "", "1"))
    )

    return {
        "dataTypeName": data_type_name,
        "pigDataType": pig_data_type,
        "dataSubTypeName": data_sub_type,
        "fieldDataFormatType": field_format_data_type,
        "originalDataTypeName": "93" if is_timestamp else pig_data_type,
        "isTimestamp": is_timestamp,
    }


def field_format_value(type_info: dict[str, Any]) -> str:
    """Return the FIELDFORMAT value for a given type info dict."""
    if type_info["isTimestamp"]:
        return "yyyy-mm-dd"
    if type_info["dataTypeName"] == "DATE":
        return "yyyy-mm-dd"
    return "0"
