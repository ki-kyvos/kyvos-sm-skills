"""Tests for kyvos_sm_skills.intent_generator — LLM-powered automatic intent generation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from kyvos_sm_skills.intent_generator import (
    _build_intent_system_prompt,
    _build_intent_user_message,
    generate_intent,
    generate_intent_from_file,
)


# ── Test fixtures ──────────────────────────────────────────────────────────


def _make_schema_summary() -> dict:
    """AdventureWorks-like schema for testing."""
    return {
        "warehouse_type": "postgresql",
        "schema": "public",
        "table_count": 4,
        "tables": [
            {
                "name": "fact_internet_sales",
                "estimated_table_type": "fact",
                "columns": [
                    {"name": "sales_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dim_product.product_key"},
                    {"name": "sales_amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
            {
                "name": "dim_product",
                "estimated_table_type": "dimension",
                "columns": [
                    {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "product_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
            {
                "name": "dim_employee",
                "estimated_table_type": "dimension",
                "columns": [
                    {"name": "employee_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "parent_employee_key", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
            {
                "name": "dim_date",
                "estimated_table_type": "dimension",
                "columns": [
                    {"name": "date_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "year", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
        ],
        "relationships": [],
        "detected_patterns": {},
    }


# ── System prompt tests ────────────────────────────────────────────────────


class TestBuildIntentSystemPrompt:
    def test_prompt_is_non_empty(self):
        prompt = _build_intent_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_prompt_contains_mdx_reference(self):
        prompt = _build_intent_system_prompt()
        assert "MDX" in prompt
        assert "NOT DAX" in prompt

    def test_prompt_contains_knowledge_base(self):
        prompt = _build_intent_system_prompt()
        assert "Knowledge Base" in prompt
        assert "1232535557" in prompt  # MDX guide page ID

    def test_prompt_contains_intent_sections(self):
        prompt = _build_intent_system_prompt()
        assert "Business Context" in prompt
        assert "Schema Analysis" in prompt
        assert "Hierarchy Requirements" in prompt
        assert "KPI" in prompt or "Measure" in prompt
        assert "Quality Bar" in prompt


# ── User message tests ─────────────────────────────────────────────────────


class TestBuildIntentUserMessage:
    def test_message_contains_schema(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema)
        assert "fact_internet_sales" in msg
        assert "dim_product" in msg
        assert "dim_employee" in msg

    def test_message_contains_domain(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema, domain="adventure_works")
        assert "adventure_works" in msg

    def test_message_contains_enterprise_context(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema, enterprise_context="Bicycle manufacturer")
        assert "Bicycle manufacturer" in msg

    def test_message_contains_instructions(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema)
        assert "Business Context" in msg
        assert "Fact Tables" in msg
        assert "Dimension Tables" in msg
        assert "Hierarchy Requirements" in msg
        assert "KPI Requirements" in msg
        assert "Quality Bar" in msg
        assert "parent-child" in msg.lower()

    def test_message_contains_parent_child_instructions(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema)
        assert "parent-child" in msg.lower()
        assert "root_member_type" in msg

    def test_message_contains_mdx_instructions(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema)
        assert "YTD" in msg
        assert "QTD" in msg
        assert "MTD" in msg
        assert "MDX" in msg

    def test_message_contains_column_details(self):
        schema = _make_schema_summary()
        msg = _build_intent_user_message(schema)
        assert "sales_amount" in msg
        assert "NUMERIC" in msg
        assert "parent_employee_key" in msg


# ── Generate intent tests (mocked LLM) ─────────────────────────────────────


class TestGenerateIntent:
    @patch("kyvos_sm_skills.llm_designer._call_anthropic")
    def test_generate_intent_anthropic(self, mock_call):
        mock_call.return_value = "## Business Context\nAdventure Works is a bicycle manufacturer..."
        schema = _make_schema_summary()
        result = generate_intent(
            schema_summary=schema,
            domain="adventure_works",
            api_key="test_key",
        )
        assert "Business Context" in result
        mock_call.assert_called_once()

    @patch("kyvos_sm_skills.llm_designer._call_anthropic")
    def test_generate_intent_passes_domain(self, mock_call):
        mock_call.return_value = "Intent document"
        schema = _make_schema_summary()
        generate_intent(
            schema_summary=schema,
            domain="retail_ecommerce",
            api_key="test_key",
        )
        # Check the user message contains the domain
        call_args = mock_call.call_args
        user_msg = call_args.kwargs["user_message"]
        assert "retail_ecommerce" in user_msg

    @patch("kyvos_sm_skills.llm_designer._call_anthropic")
    def test_generate_intent_passes_schema(self, mock_call):
        mock_call.return_value = "Intent document"
        schema = _make_schema_summary()
        generate_intent(
            schema_summary=schema,
            api_key="test_key",
        )
        call_args = mock_call.call_args
        user_msg = call_args.kwargs["user_message"]
        assert "fact_internet_sales" in user_msg
        assert "dim_employee" in user_msg

    @patch("kyvos_sm_skills.llm_designer._call_anthropic")
    def test_generate_intent_passes_enterprise_context(self, mock_call):
        mock_call.return_value = "Intent document"
        schema = _make_schema_summary()
        generate_intent(
            schema_summary=schema,
            enterprise_context="Global manufacturing company",
            api_key="test_key",
        )
        call_args = mock_call.call_args
        user_msg = call_args.kwargs["user_message"]
        assert "Global manufacturing company" in user_msg

    def test_generate_intent_no_api_key_raises(self):
        schema = _make_schema_summary()
        with pytest.raises(ValueError, match="API key"):
            generate_intent(
                schema_summary=schema,
                api_key="",
            )

    @patch("kyvos_sm_skills.llm_designer._call_azure_openai")
    def test_generate_intent_azure_openai(self, mock_call):
        mock_call.return_value = "## Business Context\nRetail analytics..."
        schema = _make_schema_summary()
        with patch.dict("os.environ", {"AZURE_ENDPOINT": "https://test.openai.azure.com", "AZURE_DEPLOYMENT_NAME": "test-deploy"}):
            result = generate_intent(
                schema_summary=schema,
                api_key="test_key",
                llm_provider="azure_openai",
            )
        assert "Business Context" in result
        mock_call.assert_called_once()


# ── Generate intent from file tests ────────────────────────────────────────


class TestGenerateIntentFromFile:
    @patch("kyvos_sm_skills.llm_designer._call_anthropic")
    def test_save_to_file(self, mock_call, tmp_path):
        mock_call.return_value = "## Business Context\nTest intent document"
        schema = _make_schema_summary()
        intent_path = str(tmp_path / "intent_test.txt")
        result = generate_intent_from_file(
            intent_path=intent_path,
            schema_summary=schema,
            api_key="test_key",
        )
        assert "Test intent document" in result
        with open(intent_path) as f:
            saved = f.read()
        assert "Test intent document" in saved

    @patch("kyvos_sm_skills.llm_designer._call_anthropic")
    def test_returns_generated_intent(self, mock_call):
        mock_call.return_value = "Generated intent content"
        schema = _make_schema_summary()
        result = generate_intent_from_file(
            intent_path="/tmp/test_intent_output.txt",
            schema_summary=schema,
            api_key="test_key",
        )
        assert result == "Generated intent content"
