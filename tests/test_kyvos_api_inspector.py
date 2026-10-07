"""Tests for Kyvos connection metadata inspection."""

from __future__ import annotations

import threading
import time

from kyvos_sm_skills.kyvos_api_inspector import (
    build_schema_summary,
    fetch_kyvos_metadata,
)


class _FakeService:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def ensure_authenticated(self) -> None:
        self.ensure_calls += 1

    def list_connection_tables(self, connection_name, database_name, schema_name):
        return ["t1", "t2", "t3", "t4"]

    def get_table_columns(self, connection_name, database_name, schema_name, table_name):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            return [{"name": f"{table_name}_id", "dataTypeName": "NUMBER", "subType": "long"}]
        finally:
            with self.lock:
                self.active -= 1


def test_fetch_kyvos_metadata_uses_parallel_workers_and_preserves_order(monkeypatch):
    service = _FakeService()
    monkeypatch.setenv("KYVOS_METADATA_WORKERS", "4")

    result = fetch_kyvos_metadata(
        service,
        connection_name="conn",
        database_name="db",
        schema_name="schema",
    )

    assert [t["name"] for t in result["tables"]] == ["t1", "t2", "t3", "t4"]
    assert [t["columns"][0]["name"] for t in result["tables"]] == [
        "t1_id", "t2_id", "t3_id", "t4_id",
    ]
    assert service.ensure_calls == 1
    assert service.max_active > 1


def test_fetch_kyvos_metadata_can_run_sequentially(monkeypatch):
    service = _FakeService()
    monkeypatch.setenv("KYVOS_METADATA_WORKERS", "1")

    result = fetch_kyvos_metadata(
        service,
        connection_name="conn",
        database_name="db",
        schema_name="schema",
    )

    assert len(result["tables"]) == 4
    assert service.ensure_calls == 0
    assert service.max_active == 1


def test_build_schema_summary_supports_compact_llm_output():
    raw = {
        "connection_name": "conn",
        "database_name": "db",
        "schema_name": "public",
        "tables": [
            {"name": "fact_sales", "columns": [
                {"name": "sales_id", "data_type": "INTEGER"},
                {"name": "product_key", "data_type": "INTEGER"},
                {"name": "amount", "data_type": "NUMERIC"},
            ]},
            {"name": "dim_product", "columns": [
                {"name": "product_key", "data_type": "INTEGER"},
                {"name": "product_name", "data_type": "VARCHAR"},
            ]},
        ],
    }
    inferred = {
        "tables": [
            {"name": "fact_sales", "estimated_table_type": "fact", "primary_key": "sales_id"},
            {"name": "dim_product", "estimated_table_type": "dimension", "primary_key": "product_key"},
        ],
        "relationships": [{
            "from_table": "fact_sales",
            "from_column": "product_key",
            "to_table": "dim_product",
            "to_column": "product_key",
        }],
        "detected_patterns": {"recommended_pattern": "star_schema"},
    }

    summary = build_schema_summary(raw, inferred)
    fact_cols = {c["name"]: c for c in summary["tables"][0]["columns"]}

    assert fact_cols["sales_id"]["is_pk"] is True
    assert fact_cols["product_key"]["is_fk"] is True
    assert fact_cols["product_key"]["references"] == "dim_product.product_key"
    assert summary["tables"][0]["outgoing_fk_count"] == 1
    assert summary["tables"][1]["incoming_fk_count"] == 1
