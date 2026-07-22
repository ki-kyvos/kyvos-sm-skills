"""Tests for the discover-sm-from-warehouse skill flow runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kyvos_sm_skills.skill_runner import run_discover_sm_from_warehouse


# ── Test fixtures ──────────────────────────────────────────────────────────


_SM_DESIGN = {
    "recommended_sms": [
        {
            "name": "AdventureWorksSales",
            "schema_type": "star",
            "rationale": "Standard star schema for sales analytics",
            "tables": ["fact_internet_sales", "dim_product", "dim_customer"],
            "relationships": [
                {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
                {"from_table": "fact_internet_sales", "from_column": "customer_key", "to_table": "dim_customer", "to_column": "customer_key"},
            ],
            "measures": [
                {"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"},
            ],
            "hierarchies": [
                {"name": "ProductHierarchy", "levels": ["product_key", "category"], "source_dataset": "dim_product"},
            ],
        }
    ],
    "identified_domain": "adventure_works",
    "domain_research_summary": "Adventure Works is a retail bicycle company.",
}


_WAREHOUSE_TABLES = [
    {
        "name": "fact_internet_sales",
        "schema": "public",
        "estimated_table_type": "fact",
        "outgoing_fk_count": 2,
        "incoming_fk_count": 0,
        "columns": [
            {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_product.product_key"},
            {"name": "customer_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_customer.customer_key"},
            {"name": "sales_amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dim_product",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "category", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dim_customer",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "customer_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
        ],
    },
]


def _mock_inspect_schema(config, schema_filter=None, max_tables=500):
    """Mock inspect_schema that returns predefined warehouse tables."""
    return {
        "warehouse_type": "POSTGRES",
        "schema": schema_filter or "public",
        "table_count": len(_WAREHOUSE_TABLES),
        "tables": _WAREHOUSE_TABLES,
        "relationships": [
            {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
            {"from_table": "fact_internet_sales", "from_column": "customer_key", "to_table": "dim_customer", "to_column": "customer_key"},
        ],
        "detected_patterns": {
            "potential_star_schemas": [{"fact_table": "fact_internet_sales", "dimension_tables": ["dim_product", "dim_customer"]}],
            "potential_snowflake_schemas": [],
            "potential_multifact_schemas": [],
            "single_table_candidates": [],
            "disjoint_table_groups": [],
        },
    }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestRunDiscoverSmFromWarehouse:
    def test_dry_run_with_inline_sm_design(self, tmp_path, monkeypatch):
        """Dry run with inline SM design should inspect + build spec, no API calls."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            rc = run_discover_sm_from_warehouse(
                env_file=str(env_file),
                sm_design=_SM_DESIGN,
                dry_run=True,
            )

        assert rc == 0

    def test_dry_run_with_sm_design_file(self, tmp_path, monkeypatch):
        """Dry run with SM design file path."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        design_file = tmp_path / "sm-design.json"
        design_file.write_text(json.dumps(_SM_DESIGN))

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            rc = run_discover_sm_from_warehouse(
                env_file=str(env_file),
                sm_design_path=str(design_file),
                dry_run=True,
            )

        assert rc == 0

    def test_no_sm_design_raises_error(self, tmp_path):
        """Should raise ValueError if neither sm_design_path nor sm_design is provided."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            with pytest.raises(ValueError, match="sm_design_path, sm_design, or user_intent must be provided"):
                run_discover_sm_from_warehouse(
                    env_file=str(env_file),
                    dry_run=True,
                )

    def test_empty_recommended_sms_raises(self, tmp_path):
        """Should raise ValueError if recommended_sms is empty."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            with pytest.raises(ValueError, match="at least one SM"):
                run_discover_sm_from_warehouse(
                    env_file=str(env_file),
                    sm_design={"recommended_sms": []},
                    dry_run=True,
                )

    def test_sm_design_file_not_found(self, tmp_path):
        """Should raise FileNotFoundError if sm_design_path doesn't exist."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            with pytest.raises(FileNotFoundError):
                run_discover_sm_from_warehouse(
                    env_file=str(env_file),
                    sm_design_path="/nonexistent/path/design.json",
                    dry_run=True,
                )

    def test_table_in_sm_design_not_in_warehouse_raises(self, tmp_path):
        """Should raise ValueError when SM design references unknown tables."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        bad_design = {
            "recommended_sms": [
                {
                    "name": "BadSM",
                    "schema_type": "star",
                    "tables": ["nonexistent_table"],
                    "relationships": [],
                    "measures": [],
                    "hierarchies": [],
                }
            ]
        }

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            with pytest.raises(ValueError, match="not found in warehouse schema"):
                run_discover_sm_from_warehouse(
                    env_file=str(env_file),
                    sm_design=bad_design,
                    dry_run=True,
                )

    def test_payload_format_override(self, tmp_path):
        """Payload format should be overridden when provided."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
            rc = run_discover_sm_from_warehouse(
                env_file=str(env_file),
                sm_design=_SM_DESIGN,
                payload_format="json",
                dry_run=True,
            )

        assert rc == 0

    def test_schema_filter_passed_to_inspector(self, tmp_path):
        """Schema filter should be passed to inspect_schema."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        mock_inspect = MagicMock(return_value=_mock_inspect_schema(None, schema_filter="myschema"))

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=mock_inspect):
            run_discover_sm_from_warehouse(
                env_file=str(env_file),
                sm_design=_SM_DESIGN,
                schema_filter="myschema",
                dry_run=True,
            )

        # Verify inspect_schema was called with schema_filter="myschema"
        call_kwargs = mock_inspect.call_args
        assert call_kwargs.kwargs.get("schema_filter") == "myschema" or call_kwargs[1].get("schema_filter") == "myschema"


# ── LLM mode tests ─────────────────────────────────────────────────────────


_LLM_RESPONSE = {
    "recommended_sms": [
        {
            "name": "AdventureWorksSales",
            "schema_type": "star",
            "rationale": "Standard star schema for sales analytics",
            "tables": ["fact_internet_sales", "dim_product", "dim_customer"],
            "relationships": [
                {"from_table": "fact_internet_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
                {"from_table": "fact_internet_sales", "from_column": "customer_key", "to_table": "dim_customer", "to_column": "customer_key"},
            ],
            "measures": [
                {"name": "SalesAmount", "source_dataset": "fact_internet_sales", "aggregation_type": "sum"},
            ],
            "hierarchies": [
                {"name": "ProductHierarchy", "levels": ["product_key", "category"], "source_dataset": "dim_product"},
            ],
        }
    ],
    "identified_domain": "adventure_works",
    "domain_research_summary": "Adventure Works is a retail bicycle company.",
    "domain_reasoning": "The schema matches Adventure Works patterns.",
    "gaps_identified": [],
}


class TestRunDiscoverSmFromWarehouseLLMMode:
    def test_llm_mode_dry_run(self, tmp_path):
        """LLM mode dry run should inspect + design + build spec, no API calls to Kyvos."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.llm_designer.design_sm_from_schema", return_value=_LLM_RESPONSE):
            rc = run_discover_sm_from_warehouse(
                env_file=str(env_file),
                user_intent="I want sales analytics for Adventure Works",
                domain="adventure_works",
                dry_run=True,
            )

        assert rc == 0

    def test_llm_mode_auto_approve(self, tmp_path):
        """LLM mode with auto_approve should skip the interactive approval gate."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.llm_designer.design_sm_from_schema", return_value=_LLM_RESPONSE), \
             patch("builtins.input", return_value="n") as mock_input:
            # Even though input returns "n", auto_approve=True should skip the gate
            # But we're in dry_run mode so no deployment happens anyway
            rc = run_discover_sm_from_warehouse(
                env_file=str(env_file),
                user_intent="I want sales analytics",
                auto_approve=True,
                dry_run=True,
            )

        assert rc == 0
        # input() should not be called in dry_run mode
        mock_input.assert_not_called()

    def test_llm_mode_validation_error_raises(self, tmp_path):
        """LLM mode should raise if the recommendation references unknown tables."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        bad_response = {
            "recommended_sms": [
                {
                    "name": "BadSM",
                    "schema_type": "star",
                    "tables": ["nonexistent_table"],
                    "relationships": [],
                    "measures": [],
                    "hierarchies": [],
                }
            ],
            "identified_domain": "unknown",
        }

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.llm_designer.design_sm_from_schema", return_value=bad_response):
            with pytest.raises(ValueError, match="validation error"):
                run_discover_sm_from_warehouse(
                    env_file=str(env_file),
                    user_intent="test",
                    dry_run=True,
                )

    def test_llm_mode_with_sm_hints(self, tmp_path):
        """SM hints should be passed to design_sm_from_schema."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        mock_design = MagicMock(return_value=_LLM_RESPONSE)

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.llm_designer.design_sm_from_schema", side_effect=mock_design):
            run_discover_sm_from_warehouse(
                env_file=str(env_file),
                user_intent="sales analytics",
                sm_hints={"max_sms": 2, "preferred_schema_type": "star"},
                dry_run=True,
            )

        call_kwargs = mock_design.call_args
        passed_hints = call_kwargs.kwargs.get("sm_hints") or call_kwargs[1].get("sm_hints")
        assert passed_hints == {"max_sms": 2, "preferred_schema_type": "star"}
