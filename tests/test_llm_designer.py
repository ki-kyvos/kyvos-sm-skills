"""Tests for kyvos_sm_skills.llm_designer — LLM-based SM design via Anthropic API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kyvos_sm_skills.llm_designer import (
    _build_user_message,
    _extract_json_from_response,
    design_sm_from_schema,
    format_recommendation_for_review,
    validate_sm_recommendation,
)


# ── Test fixtures ──────────────────────────────────────────────────────────


_SCHEMA_SUMMARY = {
    "warehouse_type": "POSTGRES",
    "schema": "public",
    "table_count": 3,
    "tables": [
        {
            "name": "fact_sales",
            "estimated_table_type": "fact",
            "columns": [
                {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True},
                {"name": "customer_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True},
                {"name": "amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False},
            ],
        },
        {
            "name": "dim_product",
            "estimated_table_type": "dimension",
            "columns": [
                {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                {"name": "category", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False},
            ],
        },
        {
            "name": "dim_customer",
            "estimated_table_type": "dimension",
            "columns": [
                {"name": "customer_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
            ],
        },
    ],
    "relationships": [
        {"from_table": "fact_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
    ],
    "detected_patterns": {
        "potential_star_schemas": [{"fact_table": "fact_sales", "dimension_tables": ["dim_product", "dim_customer"]}],
    },
}


_LLM_RESPONSE = {
    "recommended_sms": [
        {
            "name": "SalesAnalytics",
            "schema_type": "star",
            "rationale": "Standard star schema for sales analytics",
            "tables": ["fact_sales", "dim_product", "dim_customer"],
            "relationships": [
                {"from_table": "fact_sales", "from_column": "product_key", "to_table": "dim_product", "to_column": "product_key"},
                {"from_table": "fact_sales", "from_column": "customer_key", "to_table": "dim_customer", "to_column": "customer_key"},
            ],
            "measures": [
                {"name": "TotalSales", "source_dataset": "fact_sales", "aggregation_type": "sum"},
            ],
            "hierarchies": [
                {"name": "ProductCategory", "levels": ["product_key", "category"], "source_dataset": "dim_product"},
            ],
        }
    ],
    "identified_domain": "retail_ecommerce",
    "domain_research_summary": "Retail e-commerce analytics focuses on sales performance.",
    "domain_reasoning": "The fact_sales table with product and customer dimensions indicates retail.",
    "gaps_identified": ["No date dimension found — consider adding one for time-based analytics"],
}


# ── Test _extract_json_from_response ───────────────────────────────────────


class TestExtractJson:
    def test_plain_json(self):
        text = json.dumps(_LLM_RESPONSE)
        result = _extract_json_from_response(text)
        assert result["identified_domain"] == "retail_ecommerce"

    def test_json_in_code_fence(self):
        text = f"Here is the recommendation:\n```json\n{json.dumps(_LLM_RESPONSE)}\n```\nDone."
        result = _extract_json_from_response(text)
        assert result["identified_domain"] == "retail_ecommerce"

    def test_json_in_plain_code_fence(self):
        text = f"```\n{json.dumps(_LLM_RESPONSE)}\n```"
        result = _extract_json_from_response(text)
        assert "recommended_sms" in result

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json_from_response("not json at all")


# ── Test _build_user_message ───────────────────────────────────────────────


class TestBuildUserMessage:
    def test_contains_user_intent(self):
        msg = _build_user_message(_SCHEMA_SUMMARY, "I want sales analytics")
        assert "I want sales analytics" in msg

    def test_contains_domain(self):
        msg = _build_user_message(_SCHEMA_SUMMARY, "intent", domain="retail")
        assert "retail" in msg

    def test_contains_schema_tables(self):
        msg = _build_user_message(_SCHEMA_SUMMARY, "intent")
        assert "fact_sales" in msg
        assert "dim_product" in msg

    def test_contains_sm_hints(self):
        hints = {"max_sms": 2, "preferred_schema_type": "star"}
        msg = _build_user_message(_SCHEMA_SUMMARY, "intent", sm_hints=hints)
        assert "max_sms" in msg
        assert "star" in msg

    def test_contains_allow_web_research(self):
        msg = _build_user_message(_SCHEMA_SUMMARY, "intent", allow_web_research=False)
        assert "False" in msg


# ── Test validate_sm_recommendation ────────────────────────────────────────


class TestValidateSmRecommendation:
    def test_valid_recommendation(self):
        errors = validate_sm_recommendation(_LLM_RESPONSE, _SCHEMA_SUMMARY)
        assert errors == []

    def test_missing_table(self):
        rec = json.loads(json.dumps(_LLM_RESPONSE))
        rec["recommended_sms"][0]["tables"].append("nonexistent_table")
        errors = validate_sm_recommendation(rec, _SCHEMA_SUMMARY)
        assert any("nonexistent_table" in e for e in errors)

    def test_invalid_relationship_table(self):
        rec = json.loads(json.dumps(_LLM_RESPONSE))
        rec["recommended_sms"][0]["relationships"].append({
            "from_table": "nonexistent", "from_column": "x", "to_table": "dim_product", "to_column": "product_key"
        })
        errors = validate_sm_recommendation(rec, _SCHEMA_SUMMARY)
        assert any("nonexistent" in e for e in errors)

    def test_invalid_relationship_column(self):
        rec = json.loads(json.dumps(_LLM_RESPONSE))
        rec["recommended_sms"][0]["relationships"].append({
            "from_table": "fact_sales", "from_column": "nonexistent_col", "to_table": "dim_product", "to_column": "product_key"
        })
        errors = validate_sm_recommendation(rec, _SCHEMA_SUMMARY)
        assert any("nonexistent_col" in e for e in errors)

    def test_invalid_measure_source(self):
        rec = json.loads(json.dumps(_LLM_RESPONSE))
        rec["recommended_sms"][0]["measures"].append({
            "name": "BadMeasure", "source_dataset": "nonexistent_table", "aggregation_type": "sum"
        })
        errors = validate_sm_recommendation(rec, _SCHEMA_SUMMARY)
        assert any("nonexistent_table" in e for e in errors)

    def test_empty_recommended_sms(self):
        errors = validate_sm_recommendation({"recommended_sms": []}, _SCHEMA_SUMMARY)
        assert len(errors) == 1
        assert "empty" in errors[0].lower()


# ── Test format_recommendation_for_review ──────────────────────────────────


class TestFormatRecommendation:
    def test_contains_sm_name(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "SalesAnalytics" in text

    def test_contains_schema_type(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "star" in text

    def test_contains_measures(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "TotalSales" in text

    def test_contains_hierarchies(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "ProductCategory" in text

    def test_contains_gaps(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "date dimension" in text

    def test_contains_approval_prompt(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "approve" in text.lower()

    def test_contains_domain(self):
        text = format_recommendation_for_review(_LLM_RESPONSE)
        assert "retail_ecommerce" in text


# ── Test design_sm_from_schema (with mocked Anthropic API) ─────────────────


class TestDesignSmFromSchema:
    def test_successful_design(self):
        """Mock Anthropic API and verify the recommendation is parsed correctly."""
        mock_response = MagicMock()
        mock_block = MagicMock()
        mock_block.text = f"```json\n{json.dumps(_LLM_RESPONSE)}\n```"
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = design_sm_from_schema(
                schema_summary=_SCHEMA_SUMMARY,
                user_intent="I want sales analytics",
                api_key="test-key",
            )

        assert result["identified_domain"] == "retail_ecommerce"
        assert len(result["recommended_sms"]) == 1
        assert result["recommended_sms"][0]["name"] == "SalesAnalytics"

    def test_missing_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key required"):
                design_sm_from_schema(
                    schema_summary=_SCHEMA_SUMMARY,
                    user_intent="test",
                )

    def test_invalid_json_response_raises(self):
        mock_response = MagicMock()
        mock_block = MagicMock()
        mock_block.text = "This is not JSON at all"
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(ValueError, match="Failed to parse"):
                design_sm_from_schema(
                    schema_summary=_SCHEMA_SUMMARY,
                    user_intent="test",
                    api_key="test-key",
                )

    def test_api_key_from_env(self):
        """API key should be read from ANTHROPIC_API_KEY env var."""
        mock_response = MagicMock()
        mock_block = MagicMock()
        mock_block.text = json.dumps(_LLM_RESPONSE)
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key"}):
            with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
                design_sm_from_schema(
                    schema_summary=_SCHEMA_SUMMARY,
                    user_intent="test",
                )
                # Verify Anthropic was called with the env key
                mock_anthropic.assert_called_with(api_key="env-key")
