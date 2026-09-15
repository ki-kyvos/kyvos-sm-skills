"""Kyvos metadata inspector — discover schema via Kyvos REST APIs.

This inspector is an alternative to the SQLAlchemy-based warehouse inspector.
It reads table and column metadata through Kyvos v2 connection APIs and, because
those APIs do not expose primary/foreign key constraints, asks an LLM to infer:

* table type (fact / dimension / bridge / unknown)
* primary key columns
* foreign key relationships
* star / snowflake / multifact schema patterns

The returned ``schema_summary`` dict matches the shape produced by
``kyvos_sdk.warehouse_inspector.inspect_schema`` so the rest of the discovery
pipeline can be reused unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from kyvos_sm_skills.llm_designer import infer_schema_metadata


def _simplify_data_type(col: dict[str, Any]) -> str:
    """Map a Kyvos column metadata dict to a simple data-type string."""
    base = (col.get("dataTypeName") or "UNKNOWN").upper()
    subtype = (col.get("subType") or "").lower()

    if base == "NUMBER":
        if subtype == "long":
            return "INTEGER"
        if subtype == "double":
            return "DOUBLE"
        return "NUMBER"
    if base == "CHAR":
        return "VARCHAR"
    if base in {"DATE", "TIMESTAMP", "TIME"}:
        return base
    return base


def _fetch_raw_metadata(
    service: Any,
    connection_name: str,
    database_name: str,
    schema_name: str,
    max_tables: int = 500,
) -> dict[str, Any]:
    """Fetch the raw table/column metadata from Kyvos v2 connection APIs."""
    tables = service.list_connection_tables(connection_name, database_name, schema_name)
    if len(tables) > max_tables:
        raise ValueError(
            f"Schema {schema_name!r} has {len(tables)} tables "
            f"(> max_tables={max_tables}); narrow the selection."
        )

    table_metadata: list[dict[str, Any]] = []
    for table_name in tables:
        columns = service.get_table_columns(
            connection_name, database_name, schema_name, table_name
        )
        table_metadata.append({
            "name": table_name,
            "columns": [
                {
                    "name": c.get("name"),
                    "data_type": _simplify_data_type(c),
                    "original_type": c.get("dataTypeName"),
                    "sub_type": c.get("subType"),
                    "precision": c.get("precision"),
                    "scale": c.get("scale"),
                }
                for c in columns
                if c.get("name")
            ],
        })

    return {
        "connection_name": connection_name,
        "database_name": database_name,
        "schema_name": schema_name,
        "tables": table_metadata,
    }


def _build_schema_summary(
    raw_metadata: dict[str, Any],
    inferred: dict[str, Any],
) -> dict[str, Any]:
    """Combine raw metadata + LLM inference into the schema_summary dict shape."""
    schema_name = raw_metadata["schema_name"]
    database_name = raw_metadata["database_name"]

    inferred_tables = {t["name"].lower(): t for t in inferred.get("tables", [])}
    relationships: list[dict[str, str]] = inferred.get("relationships", [])

    tables: list[dict[str, Any]] = []
    for raw_table in raw_metadata["tables"]:
        table_name = raw_table["name"]
        inferred_table = inferred_tables.get(table_name.lower(), {})

        raw_columns = raw_table["columns"]
        inferred_columns = {c["name"].lower(): c for c in inferred_table.get("columns", [])}

        columns: list[dict[str, Any]] = []
        for raw_col in raw_columns:
            col_name = raw_col["name"]
            inf_col = inferred_columns.get(col_name.lower(), {})
            references = inf_col.get("references", "")
            if references and "." not in references:
                # LLM may return just a table name; normalise to table.column if we can guess the PK.
                referenced_table = references
                ref_pk_cols = [
                    c["name"] for c in inferred_tables.get(referenced_table.lower(), {}).get("columns", [])
                    if c.get("is_pk")
                ]
                if ref_pk_cols:
                    references = f"{referenced_table}.{ref_pk_cols[0]}"
                else:
                    references = ""
            columns.append({
                "name": col_name,
                "data_type": inf_col.get("data_type") or raw_col.get("data_type") or "UNKNOWN",
                "is_pk": bool(inf_col.get("is_pk", False)),
                "is_fk": bool(inf_col.get("is_fk", False)),
                "references": references,
            })

        outgoing = sum(1 for r in relationships if r.get("from_table", "").lower() == table_name.lower())
        incoming = sum(1 for r in relationships if r.get("to_table", "").lower() == table_name.lower())

        tables.append({
            "name": table_name,
            "schema": schema_name,
            "database": database_name,
            "columns": columns,
            "estimated_table_type": inferred_table.get("estimated_table_type", "unknown"),
            "outgoing_fk_count": outgoing,
            "incoming_fk_count": incoming,
        })

    detected_patterns = inferred.get("detected_patterns", {})
    # Ensure required keys exist — support both old potential_* format and
    # new recommended_pattern format.
    for key in ("potential_star_schemas", "potential_snowflake_schemas", "potential_multifact_schemas"):
        if key not in detected_patterns:
            detected_patterns[key] = []
    # Ensure new single-pattern keys exist
    if "recommended_pattern" not in detected_patterns:
        # Infer recommended_pattern from potential_* lists for backward compat
        if detected_patterns.get("potential_multifact_schemas"):
            detected_patterns["recommended_pattern"] = "multifact_star_schema"
        elif detected_patterns.get("potential_snowflake_schemas"):
            detected_patterns["recommended_pattern"] = "snowflake_schema"
        elif detected_patterns.get("potential_star_schemas"):
            detected_patterns["recommended_pattern"] = "star_schema"
        else:
            detected_patterns["recommended_pattern"] = "star_schema"

    return {
        "warehouse_type": "KYVOS",
        "schema": schema_name,
        "database": database_name,
        "connection_name": raw_metadata["connection_name"],
        "table_count": len(tables),
        "tables": tables,
        "relationships": relationships,
        "detected_patterns": detected_patterns,
    }


def inspect_schema_from_kyvos(
    config: Any,
    connection_name: str,
    database_name: str,
    schema_name: str,
    *,
    max_tables: int = 500,
    llm_provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    service: Any = None,
    trace_path: str | None = None,
) -> dict[str, Any]:
    """Build a schema_summary by reading metadata from Kyvos APIs + LLM inference.

    Args:
        config: KyvosConfig with base URL and credentials. Used only to create a
            ``KyvosService`` when ``service`` is not supplied.
        connection_name: Kyvos connection name to introspect.
        database_name: Database inside the connection.
        schema_name: Schema inside the database.
        max_tables: Safety cap on number of tables to inspect.
        llm_provider: "anthropic" or "azure_openai". Defaults to env var / anthropic.
        api_key: Optional LLM API key.
        model: Optional LLM model/deployment name.
        max_tokens: Optional LLM response token budget.
        service: Optional pre-initialized ``KyvosService`` to reuse.
        trace_path: Optional path to write a debug trace file containing the raw
            Kyvos metadata, LLM prompts, raw response, and parsed schema summary.

    Returns:
        A ``schema_summary`` dict compatible with ``run_discover_sm_from_warehouse``
        and ``build_spec_from_recommendation``.
    """
    from kyvos_sdk.client import KyvosService
    from kyvos_sm_skills.pipeline_tracer import get_tracer

    svc = service or KyvosService(config=config)

    # Wire every Kyvos HTTP call into the pipeline trace so API requests and
    # responses appear in the single-file debug log.
    tracer = get_tracer()
    if tracer is not None:
        svc.api_trace_hook = tracer.api_call

    if tracer:
        tracer.step(
            "Schema Inspection",
            f"Fetching metadata for connection={connection_name} "
            f"database={database_name} schema={schema_name} "
            f"(max_tables={max_tables})",
        )

    raw_metadata = _fetch_raw_metadata(
        service=svc,
        connection_name=connection_name,
        database_name=database_name,
        schema_name=schema_name,
        max_tables=max_tables,
    )

    if tracer:
        tracer.note(
            "Raw metadata summary",
            f"Tables: {len(raw_metadata['tables'])}\n"
            + "\n".join(
                f"  {t['name']:<40s}  {len(t['columns']):>3d} cols"
                for t in raw_metadata["tables"]
            ),
        )

    inferred = infer_schema_metadata(
        raw_metadata=raw_metadata,
        connection_name=connection_name,
        database_name=database_name,
        schema_name=schema_name,
        llm_provider=llm_provider,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        trace_path=trace_path,
    )

    schema_summary = _build_schema_summary(raw_metadata, inferred)

    if tracer:
        tracer.json_dump("Schema summary (final)", schema_summary)

    return schema_summary
