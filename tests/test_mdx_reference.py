"""Tests for the Kyvos MDX reference module."""

import pytest

from kyvos_sm_skills.mdx_reference import (
    MDX_FUNCTIONS,
    EXPRESSION_TEMPLATES,
    build_expression,
    convert_dax_to_mdx,
    get_mdx_prompt_summary,
    validate_mdx_expression,
)


class TestMdxFunctionCatalog:
    def test_catalog_has_key_functions(self):
        assert "SUM" in MDX_FUNCTIONS
        assert "YTD" in MDX_FUNCTIONS
        assert "ParallelPeriod" in MDX_FUNCTIONS
        assert "IIF" in MDX_FUNCTIONS
        assert "DIVIDE" in MDX_FUNCTIONS

    def test_function_entries_have_required_fields(self):
        for name, info in MDX_FUNCTIONS.items():
            assert "category" in info, f"{name} missing category"
            assert "syntax" in info, f"{name} missing syntax"
            assert "description" in info, f"{name} missing description"

    def test_categories_cover_all_types(self):
        categories = {info["category"] for info in MDX_FUNCTIONS.values()}
        assert "scalar" in categories
        assert "member" in categories
        assert "set" in categories
        assert "periodtodate" in categories
        assert "logical" in categories


class TestDaxToMdxConverter:
    def test_totalytd_converted(self):
        expr = "TOTALYTD([Sales Amount], 'date'[date])"
        result = convert_dax_to_mdx(expr)
        assert "TOTALYTD" not in result
        assert "SUM(YTD(" in result
        assert "[Measures].[Sales Amount]" in result

    def test_totalqtd_converted(self):
        expr = "TOTALQTD([Sales Amount], 'date'[date])"
        result = convert_dax_to_mdx(expr)
        assert "TOTALQTD" not in result
        assert "SUM(QTD(" in result

    def test_totalmtd_converted(self):
        expr = "TOTALMTD([Sales Amount], 'date'[date])"
        result = convert_dax_to_mdx(expr)
        assert "TOTALMTD" not in result
        assert "SUM(MTD(" in result

    def test_calculate_sameperiodlastyear_converted(self):
        expr = "CALCULATE([Sales Amount], SAMEPERIODLASTYEAR('date'[date]))"
        result = convert_dax_to_mdx(expr)
        assert "CALCULATE" not in result
        assert "SAMEPERIODLASTYEAR" not in result
        assert "ParallelPeriod" in result
        assert "[Measures].[Sales Amount]" in result

    def test_dateadd_year_converted(self):
        expr = "DATEADD('date'[date], -1, YEAR)"
        result = convert_dax_to_mdx(expr)
        assert "DATEADD" not in result
        assert "ParallelPeriod" in result

    def test_mdx_expression_not_modified(self):
        expr = "SUM(YTD([Date].[Calendar].CurrentMember), [Measures].[Sales Amount])"
        result = convert_dax_to_mdx(expr)
        assert result == expr

    def test_empty_expression(self):
        assert convert_dax_to_mdx("") == ""

    def test_non_dax_expression_unchanged(self):
        expr = "[Measures].[Sales Amount] - [Measures].[Total Product Cost]"
        result = convert_dax_to_mdx(expr)
        assert result == expr


class TestValidateMdxExpression:
    def test_valid_mdx_no_warnings(self):
        expr = "SUM(YTD([Date].[Calendar].CurrentMember), [Measures].[Sales Amount])"
        warnings = validate_mdx_expression(expr)
        assert len(warnings) == 0

    def test_dax_sumx_flagged(self):
        expr = "SUMX('table', [Sales Amount])"
        warnings = validate_mdx_expression(expr)
        assert any("SUMX" in w for w in warnings)

    def test_dax_rankx_flagged(self):
        expr = "RANKX('table', [Sales Amount])"
        warnings = validate_mdx_expression(expr)
        assert any("RANKX" in w for w in warnings)

    def test_dax_calculate_flagged(self):
        expr = "CALCULATE([Sales Amount], [Date].[Year] = 2023)"
        warnings = validate_mdx_expression(expr)
        assert any("CALCULATE" in w for w in warnings)

    def test_mdx_iif_not_flagged(self):
        expr = "IIF([Measures].[Sales] = 0, NULL, [Measures].[Sales] / [Measures].[Cost])"
        warnings = validate_mdx_expression(expr)
        assert len(warnings) == 0


class TestExpressionTemplates:
    def test_ytd_template(self):
        expr = build_expression("ytd", measure_name="Sales Amount")
        assert "SUM(YTD(" in expr
        assert "[Measures].[Sales Amount]" in expr

    def test_qtd_template(self):
        expr = build_expression("qtd", measure_name="Sales Amount")
        assert "SUM(QTD(" in expr

    def test_mtd_template(self):
        expr = build_expression("mtd", measure_name="Sales Amount")
        assert "SUM(MTD(" in expr

    def test_prior_year_template(self):
        expr = build_expression("prior_year", measure_name="Sales Amount")
        assert "ParallelPeriod" in expr
        assert "[Measures].[Sales Amount]" in expr

    def test_yoy_growth_template(self):
        expr = build_expression("yoy_growth", measure_name="Sales Amount")
        assert "IIF" in expr
        assert "ParallelPeriod" in expr

    def test_profit_margin_template(self):
        expr = build_expression("profit_margin_pct", sales_measure="Sales", cost_measure="Cost")
        assert "IIF" in expr
        assert "[Measures].[Sales]" in expr
        assert "[Measures].[Cost]" in expr

    def test_safe_divide_template(self):
        expr = build_expression("safe_divide", numerator="Sales", denominator="Cost")
        assert "DIVIDE(" in expr
        assert "[Measures].[Sales]" in expr

    def test_running_total_template(self):
        expr = build_expression("running_total", measure_name="Sales Amount")
        assert "SUM(PeriodsToDate(" in expr

    def test_rank_template(self):
        expr = build_expression("rank", measure_name="Sales Amount")
        assert "Rank(" in expr

    def test_custom_date_hierarchy(self):
        expr = build_expression("ytd", measure_name="Sales", date_hierarchy="[Date].[Fiscal]")
        assert "[Date].[Fiscal]" in expr

    def test_invalid_template_raises(self):
        with pytest.raises(KeyError):
            build_expression("nonexistent", measure_name="Sales")


class TestPromptSummary:
    def test_summary_mentions_mdx(self):
        summary = get_mdx_prompt_summary()
        assert "MDX" in summary

    def test_summary_mentions_not_dax(self):
        summary = get_mdx_prompt_summary()
        assert "DAX" in summary
        assert "NOT DAX" in summary

    def test_summary_has_reference_url(self):
        summary = get_mdx_prompt_summary()
        assert "docs.support.kyvosinsights.com" in summary

    def test_summary_has_key_patterns(self):
        summary = get_mdx_prompt_summary()
        assert "YTD" in summary
        assert "ParallelPeriod" in summary
        assert "IIF" in summary
        assert "DIVIDE" in summary
