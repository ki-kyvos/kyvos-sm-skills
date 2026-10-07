"""Tests for kyvos_sm_skills.llm_designer — LLM-based SM design via Anthropic API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kyvos_sm_skills.llm_designer import (
    _build_user_message,
    _call_anthropic,
    _extract_json_from_response,
    _is_transient,
    design_sm_from_schema,
    format_recommendation_for_review,
    repair_sm_hierarchies,
    repair_sm_relationships,
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
                {"name": "subcategory", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False},
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
                {"name": "ProductCategory", "levels": ["category", "subcategory"], "source_dataset": "dim_product"},
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

    def test_top_level_array_raises(self):
        """A bare JSON array must be rejected; we need a JSON object."""
        text = json.dumps([{"recommended_sms": []}])
        with pytest.raises(json.JSONDecodeError):
            _extract_json_from_response(text)

    def test_unescaped_string_characters_are_recovered(self):
        text = '{"summary":"line one\nline two at C:\\warehouse"}'
        result = _extract_json_from_response(text)
        assert result["summary"] == "line one\nline two at C:\\warehouse"

    def test_multiple_fences_prefers_recommended_sms(self):
        """A small helper object fenced before the real recommendation must not win."""
        glossary = {
            "fact_loan_portfolio": "fact table of loans",
            "dim_date": "calendar dimension",
        }
        text = (
            "Column glossary:\n```json\n" + json.dumps(glossary) + "\n```\n"
            "And the recommendation:\n```json\n" + json.dumps(_LLM_RESPONSE) + "\n```\n"
        )
        result = _extract_json_from_response(text)
        assert "recommended_sms" in result
        assert result["identified_domain"] == "retail_ecommerce"

    def test_multiple_fences_without_recommendation_returns_largest(self):
        """With no recommended_sms-bearing block, the largest parsed object wins."""
        small = {"a": 1}
        large = {"b": list(range(50)), "c": "x" * 100}
        text = (
            "```json\n" + json.dumps(small) + "\n```\n"
            "some prose\n```\n" + json.dumps(large) + "\n```\n"
        )
        result = _extract_json_from_response(text)
        assert result == large


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


def _make_mock_stream(response_text: str, stop_reason: str = "end_turn"):
    """Create a mock for Anthropic streaming API."""
    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=None)
    mock_stream.text_stream = iter([response_text])
    mock_final_msg = MagicMock()
    mock_final_msg.stop_reason = stop_reason
    mock_stream.get_final_message.return_value = mock_final_msg
    return mock_stream


class TestDesignSmFromSchema:
    def test_successful_design(self):
        """Mock Anthropic API and verify the recommendation is parsed correctly."""
        response_text = f"```json\n{json.dumps(_LLM_RESPONSE)}\n```"
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = _make_mock_stream(response_text)

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = design_sm_from_schema(
                schema_summary=_SCHEMA_SUMMARY,
                user_intent="I want sales analytics",
                api_key="test-key",
            )

        assert result["identified_domain"] == "retail_ecommerce"
        assert len(result["recommended_sms"]) == 1
        assert result["recommended_sms"][0]["name"] == "SalesAnalytics"

    def test_validated_response_is_cached(self, tmp_path, monkeypatch):
        """A repeated identical schema+intent should not call the LLM again."""
        monkeypatch.setenv("KYVOS_LLM_CACHE_DIR", str(tmp_path))
        response_text = f"```json\n{json.dumps(_LLM_RESPONSE)}\n```"
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = _make_mock_stream(response_text)

        with patch("anthropic.Anthropic", return_value=mock_client):
            first = design_sm_from_schema(
                schema_summary=_SCHEMA_SUMMARY,
                user_intent="cache test",
                api_key="test-key",
            )

        with patch("anthropic.Anthropic", side_effect=AssertionError("LLM should not be called")):
            second = design_sm_from_schema(
                schema_summary=_SCHEMA_SUMMARY,
                user_intent="cache test",
                api_key="test-key",
            )

        assert second == first

    def test_missing_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key required"):
                design_sm_from_schema(
                    schema_summary=_SCHEMA_SUMMARY,
                    user_intent="test",
                    use_cache=False,
                )

    def test_invalid_json_response_raises(self):
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = _make_mock_stream("This is not JSON at all")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(ValueError, match="Failed to parse"):
                design_sm_from_schema(
                    schema_summary=_SCHEMA_SUMMARY,
                    user_intent="test",
                    api_key="test-key",
                    use_cache=False,
                )

    def test_api_key_from_env(self):
        """API key should be read from ANTHROPIC_API_KEY env var."""
        response_text = json.dumps(_LLM_RESPONSE)
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = _make_mock_stream(response_text)

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key"}, clear=True):
            with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
                design_sm_from_schema(
                    schema_summary=_SCHEMA_SUMMARY,
                    user_intent="test",
                    use_cache=False,
                )
                # Verify Anthropic was called with the env key
                mock_anthropic.assert_called_with(api_key="env-key")


# ── Test _is_transient helper ──────────────────────────────────────────────


class TestIsTransient:
    def test_connection_error(self):
        assert _is_transient(ConnectionError("reset")) is True

    def test_timeout_error(self):
        assert _is_transient(TimeoutError("timed out")) is True

    def test_remote_protocol_error_by_name(self):
        """httpx.RemoteProtocolError is detected by class name."""

        class RemoteProtocolError(Exception):
            pass

        assert _is_transient(RemoteProtocolError("peer closed")) is True

    def test_api_connection_error_by_name(self):
        """Anthropic APIConnectionError is detected by class name."""

        class APIConnectionError(Exception):
            pass

        assert _is_transient(APIConnectionError("conn lost")) is True

    def test_value_error_not_transient(self):
        assert _is_transient(ValueError("bad input")) is False

    def test_key_error_not_transient(self):
        assert _is_transient(KeyError("missing")) is False


# ── Test _call_anthropic retry behaviour ───────────────────────────────────


class TestCallAnthropicRetry:
    def test_retries_on_transient_then_succeeds(self):
        """Streaming fails once with RemoteProtocolError, succeeds on retry."""

        class RemoteProtocolError(Exception):
            pass

        success_stream = _make_mock_stream("hello world")

        def _failing_iter():
            raise RemoteProtocolError("peer closed connection")
            yield  # noqa: unreachable — makes this a generator

        fail_stream = MagicMock()
        fail_stream.__enter__ = MagicMock(return_value=fail_stream)
        fail_stream.__exit__ = MagicMock(return_value=None)
        fail_stream.text_stream = _failing_iter()

        mock_client = MagicMock()
        mock_client.messages.stream = MagicMock(
            side_effect=[fail_stream, success_stream]
        )

        with (
            patch("anthropic.Anthropic", return_value=mock_client),
            patch("kyvos_sm_skills.llm_designer.time.sleep"),
        ):
            result = _call_anthropic(
                system_prompt="sys",
                user_message="msg",
                api_key="key",
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
            )

        assert result == "hello world"
        assert mock_client.messages.stream.call_count == 2

    def test_non_transient_error_not_retried(self):
        """A ValueError should propagate immediately without retry."""
        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = ValueError("bad param")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(ValueError, match="bad param"):
                _call_anthropic(
                    system_prompt="sys",
                    user_message="msg",
                    api_key="key",
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                )

        assert mock_client.messages.stream.call_count == 1

    def test_exhausted_retries_raises(self):
        """After all retries exhausted, the transient error should be raised."""

        class RemoteProtocolError(Exception):
            pass

        def _make_failing_stream():
            def _failing_iter():
                raise RemoteProtocolError("peer closed connection")
                yield  # noqa: unreachable

            s = MagicMock()
            s.__enter__ = MagicMock(return_value=s)
            s.__exit__ = MagicMock(return_value=None)
            s.text_stream = _failing_iter()
            return s

        mock_client = MagicMock()
        # Each attempt gets a fresh failing stream (generator can't be reused)
        mock_client.messages.stream = MagicMock(
            side_effect=[_make_failing_stream() for _ in range(4)]
        )

        with (
            patch("anthropic.Anthropic", return_value=mock_client),
            patch("kyvos_sm_skills.llm_designer.time.sleep"),
        ):
            with pytest.raises(RemoteProtocolError, match="peer closed"):
                _call_anthropic(
                    system_prompt="sys",
                    user_message="msg",
                    api_key="key",
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                )

        # 1 initial + 3 retries = 4 attempts
        assert mock_client.messages.stream.call_count == 4


# ── Test repair_sm_hierarchies ─────────────────────────────────────────────

# Helper schema for repair tests — mimics a credit_risk warehouse.
_REPAIR_SCHEMA = {
    "tables": [
        {
            "name": "dim_date",
            "columns": [
                {"name": "date_key", "data_type": "INTEGER"},
                {"name": "full_date", "data_type": "DATE"},
                {"name": "year", "data_type": "INTEGER"},
                {"name": "quarter", "data_type": "INTEGER"},
                {"name": "quarter_label", "data_type": "VARCHAR"},
                {"name": "month", "data_type": "INTEGER"},
                {"name": "month_name", "data_type": "VARCHAR"},
                {"name": "month_short", "data_type": "VARCHAR"},
            ],
        },
        {
            "name": "dim_product",
            "columns": [
                {"name": "product_key", "data_type": "INTEGER"},
                {"name": "product_name", "data_type": "VARCHAR"},
                {"name": "product_type", "data_type": "VARCHAR"},
                {"name": "product_category_key", "data_type": "INTEGER"},
            ],
        },
        {
            "name": "dim_product_category",
            "columns": [
                {"name": "product_category_key", "data_type": "INTEGER"},
                {"name": "product_category_code", "data_type": "VARCHAR"},
                {"name": "product_category_name", "data_type": "VARCHAR"},
            ],
        },
    ],
}


class TestRepairSmHierarchies:
    def test_mixed_types_allowed_without_type_repair(self):
        """DATE + VARCHAR levels in the same hierarchy are allowed — no type-family repair."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_date"],
                "hierarchies": [{
                    "name": "Calendar",
                    "levels": ["month_name", "full_date"],
                    "source_dataset": "dim_date",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        h = rec["recommended_sms"][0]["hierarchies"][0]
        # Both levels present — mixed type (VARCHAR + DATE) is fine
        assert h["levels"] == ["month_name", "full_date"]
        # No type-family repairs fired
        assert not any("mixed type" in r.lower() for r in repairs)

    def test_removes_nonexistent_level(self):
        """Level that doesn't exist in the source table."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product", "dim_product_category"],
                "hierarchies": [{
                    "name": "Product Hierarchy",
                    "levels": ["product_category_code", "product_name"],
                    "source_dataset": "dim_product",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        h = rec["recommended_sms"][0]["hierarchies"]
        # product_category_code is not in dim_product — hierarchy drops to 1 level,
        # so the whole hierarchy gets removed.
        assert len(h) == 0
        assert any("product_category_code" in r for r in repairs)

    def test_relocates_source_dataset(self):
        """Hierarchy whose levels all exist in a different table."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product", "dim_product_category"],
                "hierarchies": [{
                    "name": "Category Hierarchy",
                    "levels": ["product_category_code", "product_category_name"],
                    "source_dataset": "nonexistent_table",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        h = rec["recommended_sms"][0]["hierarchies"]
        assert len(h) == 1
        assert h[0]["source_dataset"] == "dim_product_category"
        assert any("relocated" in r for r in repairs)

    def test_no_repair_when_genuinely_valid(self):
        """A hierarchy using only VARCHAR columns needs no repair."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "hierarchies": [{
                    "name": "Product Hierarchy",
                    "levels": ["product_name", "product_type"],
                    "source_dataset": "dim_product",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        assert repairs == []
        h = rec["recommended_sms"][0]["hierarchies"][0]
        assert h["levels"] == ["product_name", "product_type"]

    def test_drops_standard_hierarchy_with_too_few_levels(self):
        """Standard hierarchy reduced to 1 level gets dropped entirely."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "hierarchies": [{
                    "name": "Tiny",
                    "levels": ["product_name", "full_date"],
                    "source_dataset": "dim_product",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        # full_date doesn't exist in dim_product → dropped → 1 level → hierarchy dropped
        assert len(rec["recommended_sms"][0]["hierarchies"]) == 0
        assert any("dropped entirely" in r for r in repairs)

    def test_drops_single_level_standard_hierarchy(self):
        """A standard hierarchy that starts with only 1 level is dropped."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "hierarchies": [{
                    "name": "Lone",
                    "levels": ["product_name"],
                    "source_dataset": "dim_product",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        assert len(rec["recommended_sms"][0]["hierarchies"]) == 0
        assert any("dropped entirely" in r for r in repairs)

    def test_alternate_path_single_level_preserved(self):
        """An alternate-path hierarchy with 1 level is NOT dropped."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "hierarchies": [{
                    "name": "Alt Path",
                    "levels": ["product_name"],
                    "source_dataset": "dim_product",
                    "has_alternate_path": True,
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        assert len(rec["recommended_sms"][0]["hierarchies"]) == 1

    def test_parent_child_hierarchy_not_modified(self):
        """Parent-child hierarchies should be left alone by repair."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "hierarchies": [{
                    "name": "PC Hier",
                    "levels": [],
                    "source_dataset": "dim_product",
                    "is_parent_child": True,
                    "parent_column": "product_category_key",
                    "child_column": "product_key",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        assert repairs == []
        assert len(rec["recommended_sms"][0]["hierarchies"]) == 1

    def test_malformed_recommendation_list_raises(self):
        """repair_sm_hierarchies must raise a clear error if rec is a list."""
        with pytest.raises(ValueError, match="expected a dict recommendation"):
            repair_sm_hierarchies([{"recommended_sms": []}], _REPAIR_SCHEMA)


# ── Test repair_sm_relationships ────────────────────────────────────────────


_FACT_TO_FACT_SCHEMA = {
    "tables": [
        {
            "name": "fact_internet_sales",
            "estimated_table_type": "fact",
            "columns": [
                {"name": "sales_key", "data_type": "INTEGER"},
                {"name": "product_key", "data_type": "INTEGER"},
            ],
        },
        {
            "name": "fact_reseller_sales",
            "estimated_table_type": "fact",
            "columns": [
                {"name": "sales_key", "data_type": "INTEGER"},
                {"name": "product_key", "data_type": "INTEGER"},
            ],
        },
        {
            "name": "dim_product",
            "estimated_table_type": "dimension",
            "columns": [
                {"name": "product_key", "data_type": "INTEGER"},
                {"name": "product_name", "data_type": "VARCHAR"},
            ],
        },
    ],
}


class TestRepairSmRelationships:
    def test_removes_fact_to_fact_relationship(self):
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["fact_internet_sales", "fact_reseller_sales", "dim_product"],
                "relationships": [
                    {"from_table": "fact_internet_sales", "from_column": "product_key",
                     "to_table": "dim_product", "to_column": "product_key"},
                    {"from_table": "fact_internet_sales", "from_column": "sales_key",
                     "to_table": "fact_reseller_sales", "to_column": "sales_key"},
                    {"from_table": "fact_reseller_sales", "from_column": "product_key",
                     "to_table": "dim_product", "to_column": "product_key"},
                ],
            }],
        }
        repairs = repair_sm_relationships(rec, _FACT_TO_FACT_SCHEMA)
        assert len(repairs) == 1
        assert "fact-to-fact" in repairs[0].lower()
        # Only 2 relationships remain (fact->dim for each fact)
        assert len(rec["recommended_sms"][0]["relationships"]) == 2

    def test_keeps_valid_relationships(self):
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["fact_internet_sales", "dim_product"],
                "relationships": [
                    {"from_table": "fact_internet_sales", "from_column": "product_key",
                     "to_table": "dim_product", "to_column": "product_key"},
                ],
            }],
        }
        repairs = repair_sm_relationships(rec, _FACT_TO_FACT_SCHEMA)
        assert repairs == []
        assert len(rec["recommended_sms"][0]["relationships"]) == 1

    def test_uses_table_classifications_override(self):
        """When LLM provides table_classifications, those override schema types."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["fact_internet_sales", "fact_reseller_sales"],
                "table_classifications": {
                    "fact_internet_sales": "fact",
                    "fact_reseller_sales": "dimension",  # LLM overrides to dim
                },
                "relationships": [
                    {"from_table": "fact_internet_sales", "from_column": "sales_key",
                     "to_table": "fact_reseller_sales", "to_column": "sales_key"},
                ],
            }],
        }
        repairs = repair_sm_relationships(rec, _FACT_TO_FACT_SCHEMA)
        # Should NOT be removed because LLM classified reseller_sales as dimension
        assert repairs == []
        assert len(rec["recommended_sms"][0]["relationships"]) == 1


# ── Test validate_sm_recommendation — fact-to-fact checks ──────────────────


class TestValidateFactToFact:
    def test_fact_to_fact_flagged(self):
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["fact_internet_sales", "fact_reseller_sales", "dim_product"],
                "relationships": [
                    {"from_table": "fact_internet_sales", "from_column": "sales_key",
                     "to_table": "fact_reseller_sales", "to_column": "sales_key"},
                ],
                "measures": [],
                "hierarchies": [],
            }],
        }
        errors = validate_sm_recommendation(rec, _FACT_TO_FACT_SCHEMA)
        assert any("fact-to-fact" in e.lower() for e in errors)

    def test_fact_to_dim_not_flagged(self):
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["fact_internet_sales", "dim_product"],
                "relationships": [
                    {"from_table": "fact_internet_sales", "from_column": "product_key",
                     "to_table": "dim_product", "to_column": "product_key"},
                ],
                "measures": [],
                "hierarchies": [],
            }],
        }
        errors = validate_sm_recommendation(rec, _FACT_TO_FACT_SCHEMA)
        assert not any("fact-to-fact" in e.lower() for e in errors)


class TestValidateFactTableMeasures:
    """Tests for mandatory fact-table measure validation."""

    def test_fact_table_without_measure_is_error(self):
        """A fact table with no measures should produce a validation error."""
        schema = {
            "tables": [
                {
                    "name": "fact_a",
                    "estimated_table_type": "fact",
                    "columns": [
                        {"name": "id", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                    ],
                },
                {
                    "name": "dim_b",
                    "estimated_table_type": "dimension",
                    "columns": [
                        {"name": "id", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                    ],
                },
            ]
        }
        rec = {
            "recommended_sms": [{
                "name": "SM1",
                "schema_type": "star",
                "tables": ["fact_a", "dim_b"],
                "table_classifications": {"fact_a": "fact", "dim_b": "dimension"},
                "relationships": [
                    {"from_table": "fact_a", "from_column": "id", "to_table": "dim_b", "to_column": "id"},
                ],
                "measures": [],
                "hierarchies": [],
            }]
        }
        errors = validate_sm_recommendation(rec, schema)
        assert any("fact table 'fact_a' has no measures" in e for e in errors)

    def test_fact_table_with_measure_passes(self):
        """A fact table with at least one measure should pass."""
        schema = {
            "tables": [
                {
                    "name": "fact_a",
                    "estimated_table_type": "fact",
                    "columns": [
                        {"name": "id", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                        {"name": "amount", "data_type": "NUMERIC", "is_pk": False, "is_fk": False},
                    ],
                },
                {
                    "name": "dim_b",
                    "estimated_table_type": "dimension",
                    "columns": [
                        {"name": "id", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                    ],
                },
            ]
        }
        rec = {
            "recommended_sms": [{
                "name": "SM1",
                "schema_type": "star",
                "tables": ["fact_a", "dim_b"],
                "table_classifications": {"fact_a": "fact", "dim_b": "dimension"},
                "relationships": [
                    {"from_table": "fact_a", "from_column": "id", "to_table": "dim_b", "to_column": "id"},
                ],
                "measures": [
                    {"name": "Total", "source_dataset": "fact_a", "aggregation_type": "sum", "source_column": "amount"},
                ],
                "hierarchies": [],
            }]
        }
        errors = validate_sm_recommendation(rec, schema)
        assert not any("has no measures" in e for e in errors)


# ── Test validator — fact/bridge hierarchies and MDX refs ──────────────────

_FACT_HIER_SCHEMA = {
    "tables": [
        {
            "name": "fact_loan",
            "estimated_table_type": "fact",
            "columns": [
                {"name": "loan_id", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                {"name": "dpd_bucket", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
                {"name": "amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False},
            ],
        },
        {
            "name": "dim_region",
            "estimated_table_type": "dimension",
            "columns": [
                {"name": "region_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                {"name": "region_name", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
            ],
        },
    ],
}


class TestValidateFactBridgeHierarchy:
    def test_hierarchy_on_fact_table_flagged(self):
        """A hierarchy whose source_dataset is a fact table must be rejected."""
        rec = {
            "recommended_sms": [{
                "name": "LoanSM",
                "tables": ["fact_loan", "dim_region"],
                "relationships": [],
                "measures": [
                    {"name": "TotalAmount", "source_dataset": "fact_loan", "aggregation_type": "sum"},
                ],
                "hierarchies": [
                    {"name": "DPD Bucket", "levels": ["dpd_bucket"], "source_dataset": "fact_loan"},
                ],
            }],
        }
        errors = validate_sm_recommendation(rec, _FACT_HIER_SCHEMA)
        assert any("is defined on fact/bridge table" in e for e in errors)

    def test_calc_measure_fact_reference_flagged_once(self):
        """[fact].[x] refs in a calc measure get the fact-specific error, deduped."""
        rec = {
            "recommended_sms": [{
                "name": "LoanSM",
                "tables": ["fact_loan", "dim_region"],
                "relationships": [],
                "measures": [
                    {"name": "TotalAmount", "source_dataset": "fact_loan", "aggregation_type": "sum"},
                    {
                        "name": "BadCalc",
                        "is_calculated": True,
                        "expression": (
                            "COUNT(FILTER([fact_loan].[dpd_bucket].Members, "
                            "[fact_loan].[dpd_bucket].CurrentMember))"
                        ),
                    },
                ],
                "hierarchies": [],
            }],
        }
        errors = validate_sm_recommendation(rec, _FACT_HIER_SCHEMA)
        fact_errors = [e for e in errors if "which is a fact/bridge table" in e]
        assert len(fact_errors) == 1
        assert "BadCalc" in fact_errors[0]

    def test_calc_measure_dim_reference_still_validated(self):
        """Unknown hierarchy on a real dimension still produces the generic error."""
        rec = {
            "recommended_sms": [{
                "name": "LoanSM",
                "tables": ["fact_loan", "dim_region"],
                "relationships": [],
                "measures": [
                    {"name": "TotalAmount", "source_dataset": "fact_loan", "aggregation_type": "sum"},
                    {
                        "name": "BadCalc",
                        "is_calculated": True,
                        "expression": "[dim_region].[nonexistent_hier].Members",
                    },
                ],
                "hierarchies": [],
            }],
        }
        errors = validate_sm_recommendation(rec, _FACT_HIER_SCHEMA)
        assert any("unknown hierarchy 'nonexistent_hier'" in e for e in errors)

    def _attr_rec(self, expression: str, hierarchies: list | None = None) -> dict:
        return {
            "recommended_sms": [{
                "name": "LoanSM",
                "tables": ["fact_loan", "dim_region"],
                "relationships": [],
                "measures": [
                    {"name": "TotalAmount", "source_dataset": "fact_loan", "aggregation_type": "sum"},
                    {"name": "Calc", "is_calculated": True, "expression": expression},
                ],
                "hierarchies": hierarchies or [],
            }],
        }

    def test_calc_measure_dimension_attribute_reference_allowed(self):
        """Regression (job e55e5308): [dimscenario].[scenarioname].CurrentMember on a
        dimension with no hierarchy is a valid Kyvos attribute reference."""
        expr = (
            'IIF([dim_region].[region_name].CurrentMember.Name = "West", '
            "[Measures].[TotalAmount], NULL)"
        )
        errors = validate_sm_recommendation(self._attr_rec(expr), _FACT_HIER_SCHEMA)
        assert not any("unknown hierarchy" in e or "unknown level" in e for e in errors)

    def test_calc_measure_attribute_member_reference_allowed(self):
        expr = "([dim_region].[region_name].[West], [Measures].[TotalAmount])"
        errors = validate_sm_recommendation(self._attr_rec(expr), _FACT_HIER_SCHEMA)
        assert not any("unknown hierarchy" in e or "unknown level" in e for e in errors)

    def test_hierarchy_level_column_is_not_an_attribute(self):
        """A column used as a hierarchy level is not exposed as an attribute by the
        SM compiler, so [dim].[level_column] must still be rejected."""
        hiers = [{"name": "Region", "levels": ["region_key", "region_name"], "source_dataset": "dim_region"}]
        expr = "[dim_region].[region_name].CurrentMember.Name"
        errors = validate_sm_recommendation(self._attr_rec(expr, hiers), _FACT_HIER_SCHEMA)
        assert any("unknown hierarchy 'region_name'" in e for e in errors)


# ── Test repair/validator — referenced single-level hierarchy exemption ─────


class TestReferencedSingleLevelHierarchy:
    def test_referenced_single_level_hierarchy_kept(self):
        """A 1-level dim hierarchy referenced by a calc measure is kept."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "measures": [{
                    "name": "Calc",
                    "is_calculated": True,
                    "expression": "[dim_product].[Lone].Members",
                }],
                "hierarchies": [{
                    "name": "Lone",
                    "levels": ["product_name"],
                    "source_dataset": "dim_product",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        hiers = rec["recommended_sms"][0]["hierarchies"]
        assert len(hiers) == 1
        assert hiers[0]["levels"] == ["product_name"]
        assert any("kept with 1 level" in r for r in repairs)

    def test_unreferenced_single_level_hierarchy_dropped(self):
        """The same hierarchy with no measure reference is dropped as before."""
        rec = {
            "recommended_sms": [{
                "name": "TestSM",
                "tables": ["dim_product"],
                "measures": [{
                    "name": "Calc",
                    "is_calculated": True,
                    "expression": "[dim_product].[Other].Members",
                }],
                "hierarchies": [{
                    "name": "Lone",
                    "levels": ["product_name"],
                    "source_dataset": "dim_product",
                }],
            }],
        }
        repairs = repair_sm_hierarchies(rec, _REPAIR_SCHEMA)
        assert len(rec["recommended_sms"][0]["hierarchies"]) == 0
        assert any("dropped entirely" in r for r in repairs)

    def test_validator_accepts_referenced_single_level_hierarchy(self):
        """The validator must not re-flag a single-level hierarchy that repair kept."""
        schema = {
            "tables": [
                {
                    "name": "fact_sales",
                    "estimated_table_type": "fact",
                    "columns": [
                        {"name": "amount", "data_type": "NUMERIC", "is_pk": False, "is_fk": False},
                    ],
                },
                {
                    "name": "dim_region",
                    "estimated_table_type": "dimension",
                    "columns": [
                        {"name": "region_name", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
                    ],
                },
            ],
        }
        rec = {
            "recommended_sms": [{
                "name": "SM1",
                "tables": ["fact_sales", "dim_region"],
                "relationships": [],
                "measures": [
                    {"name": "Total", "source_dataset": "fact_sales", "aggregation_type": "sum"},
                    {
                        "name": "Calc",
                        "is_calculated": True,
                        "expression": "[dim_region].[Region].Members",
                    },
                ],
                "hierarchies": [
                    {"name": "Region", "levels": ["region_name"], "source_dataset": "dim_region"},
                ],
            }],
        }
        errors = validate_sm_recommendation(rec, schema)
        assert not any("at least 2 levels" in e for e in errors)
        assert errors == []


# ── Test validator/repair — key-column level flags ─────────────────────────

_FLAG_SCHEMA = {
    "tables": [
        {
            "name": "fact_loan",
            "estimated_table_type": "fact",
            "columns": [
                {"name": "loan_id", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                {"name": "amount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False},
            ],
        },
        {
            "name": "dim_account",
            "estimated_table_type": "dimension",
            "columns": [
                {"name": "account_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False},
                {"name": "customer_key", "data_type": "INTEGER", "is_pk": False, "is_fk": True},
                {"name": "account_type", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
                {"name": "account_status", "data_type": "VARCHAR", "is_pk": False, "is_fk": False},
            ],
        },
    ],
}


def _flag_rec(levels: list[str]) -> dict:
    return {
        "recommended_sms": [{
            "name": "SM1",
            "tables": ["fact_loan", "dim_account"],
            "relationships": [],
            "measures": [
                {"name": "Total", "source_dataset": "fact_loan", "aggregation_type": "sum"},
            ],
            "hierarchies": [
                {"name": "Acct", "levels": levels, "source_dataset": "dim_account"},
            ],
        }],
    }


class TestHierarchyLevelFlags:
    def test_fk_level_flagged(self):
        """A foreign-key column used as a hierarchy level is rejected."""
        errors = validate_sm_recommendation(
            _flag_rec(["customer_key", "account_type"]), _FLAG_SCHEMA
        )
        assert any("foreign-key column" in e for e in errors)

    def test_pk_at_leaf_ok(self):
        """A primary key as the last (leaf) level is allowed."""
        errors = validate_sm_recommendation(
            _flag_rec(["account_type", "account_key"]), _FLAG_SCHEMA
        )
        assert errors == []

    def test_pk_above_other_levels_flagged(self):
        """A primary key above another level is rejected."""
        errors = validate_sm_recommendation(
            _flag_rec(["account_key", "account_type"]), _FLAG_SCHEMA
        )
        assert any("may only be the last (leaf) level" in e for e in errors)

    def test_unflagged_columns_unaffected(self):
        """Ordinary business columns produce no flag-related errors."""
        errors = validate_sm_recommendation(
            _flag_rec(["account_type", "account_status"]), _FLAG_SCHEMA
        )
        assert errors == []

    def test_repair_drops_fk_level(self):
        """Repair removes an FK level; reduced to 1 level it is then dropped."""
        rec = _flag_rec(["customer_key", "account_type"])
        repairs = repair_sm_hierarchies(rec, _FLAG_SCHEMA)
        assert any("foreign-key column" in r for r in repairs)
        assert rec["recommended_sms"][0]["hierarchies"] == []
        assert any("dropped entirely" in r for r in repairs)
