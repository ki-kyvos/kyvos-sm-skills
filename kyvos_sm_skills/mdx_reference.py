"""Kyvos MDX function catalog, expression templates, and DAX-to-MDX converter.

This module provides:
- A structured catalog of Kyvos-supported MDX functions (sourced from the official
  Kyvos MDX Functions Guide at
  https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1232535557/)
- Expression templates for common calculated measures (YTD, QTD, MTD, YoY, etc.)
- A DAX-to-MDX converter that automatically fixes common DAX patterns LLMs produce

The catalog and templates are data-driven so new functions/patterns can be added
without modifying the LLM prompt or spec builder.
"""

from __future__ import annotations

import re
from typing import Any


# ─── Kyvos MDX Function Catalog ──────────────────────────────────────────────
# Sourced from the official Kyvos MDX Functions Guide (2026.6).
# Each entry: name -> (category, syntax, description)

MDX_FUNCTIONS: dict[str, dict[str, str]] = {
    # ── Scalar Functions ──
    "SUM": {
        "category": "scalar",
        "syntax": "SUM(Set_Expression [, Numeric_Expression])",
        "description": "Sum of a numeric expression over a set.",
    },
    "AVG": {
        "category": "scalar",
        "syntax": "AVG(Set_Expression [, Numeric_Expression])",
        "description": "Average of a numeric expression over a set. Empty cells excluded by default.",
    },
    "MIN": {
        "category": "scalar",
        "syntax": "MIN(Set_Expression [, Numeric_Expression])",
        "description": "Minimum value of a numeric expression over a set.",
    },
    "MAX": {
        "category": "scalar",
        "syntax": "MAX(Set_Expression [, Numeric_Expression])",
        "description": "Maximum value of a numeric expression over a set.",
    },
    "COUNT": {
        "category": "scalar",
        "syntax": "COUNT(Set_Expression [, INCLUDEEMPTY | EXCLUDEEMPTY])",
        "description": "Count of members in a set.",
    },
    "IIF": {
        "category": "scalar",
        "syntax": "IIF(Condition, True_Value, False_Value)",
        "description": "Inline conditional — returns one of two values based on a logical condition.",
    },
    "CASE": {
        "category": "scalar",
        "syntax": "CASE WHEN Condition1 THEN Value1 ... [ELSE DefaultValue] END",
        "description": "Multi-branch conditional evaluation.",
    },
    "DIVIDE": {
        "category": "scalar",
        "syntax": "DIVIDE(Numerator, Denominator [, Default_Value])",
        "description": "Safe division with fallback for divide-by-zero.",
    },
    "Rank": {
        "category": "scalar",
        "syntax": "Rank(Member_Expression, Set_Expression [, Numeric_Expression])",
        "description": "One-based rank of a member within a set.",
    },
    "Median": {
        "category": "scalar",
        "syntax": "Median(Set_Expression [, Numeric_Expression])",
        "description": "Median value over a set.",
    },
    "Percentile": {
        "category": "scalar",
        "syntax": "Percentile(Set_Expression, Numeric_Expression, Percentile_Value)",
        "description": "Value at a specified percentile (0-100) over a set.",
    },
    "STDEVP": {
        "category": "scalar",
        "syntax": "STDEVP(Set_Expression [, Numeric_Expression])",
        "description": "Population standard deviation over a set.",
    },
    "Format": {
        "category": "scalar",
        "syntax": "Format(Value_Expression, String_Expression)",
        "description": "Formats a numeric or date value as a string.",
    },
    "ROUND": {
        "category": "scalar",
        "syntax": "ROUND(Numeric_Expression, Precision)",
        "description": "Rounds to specified decimal places.",
    },
    "ABS": {
        "category": "scalar",
        "syntax": "ABS(Numeric_Expression)",
        "description": "Absolute (non-negative) value.",
    },
    "POWER": {
        "category": "scalar",
        "syntax": "POWER(Numeric_Expression, Power)",
        "description": "Number raised to a power.",
    },
    "SQRT": {
        "category": "scalar",
        "syntax": "SQRT(Numeric_Expression)",
        "description": "Square root.",
    },
    "PROD": {
        "category": "scalar",
        "syntax": "PROD(Set_Expression [, Numeric_Expression])",
        "description": "Product (multiplication) of a numeric expression over a set.",
    },
    # ── Member Functions ──
    "CurrentMember": {
        "category": "member",
        "syntax": "Dimension_Expression.CurrentMember",
        "description": "Current member of a dimension/hierarchy during iteration.",
    },
    "ParallelPeriod": {
        "category": "member",
        "syntax": "ParallelPeriod([Level_Expression [, Lag_Number [, Member_Expression]]])",
        "description": "Member at the same relative position in a prior period.",
    },
    "FirstChild": {
        "category": "member",
        "syntax": "Member_Expression.FirstChild",
        "description": "First child of a member.",
    },
    "LastChild": {
        "category": "member",
        "syntax": "Member_Expression.LastChild",
        "description": "Last child of a member.",
    },
    "Lag": {
        "category": "member",
        "syntax": "Member_Expression.Lag(N)",
        "description": "Member N positions before the current member.",
    },
    "Lead": {
        "category": "member",
        "syntax": "Member_Expression.Lead(N)",
        "description": "Member N positions after the current member.",
    },
    "PrevMember": {
        "category": "member",
        "syntax": "Member_Expression.PrevMember",
        "description": "Previous member at the same level.",
    },
    "NextMember": {
        "category": "member",
        "syntax": "Member_Expression.NextMember",
        "description": "Next member at the same level.",
    },
    "Ancestor": {
        "category": "member",
        "syntax": "Ancestor(Member_Expression, Level_Expression)",
        "description": "Ancestor of a member at a specified level.",
    },
    # ── Set Functions ──
    "YTD": {
        "category": "periodtodate",
        "syntax": "YTD([Member_Expression])",
        "description": "Set of members from start of year to current member.",
    },
    "MTD": {
        "category": "periodtodate",
        "syntax": "MTD([Member_Expression])",
        "description": "Set of members from start of month to current member.",
    },
    "QTD": {
        "category": "periodtodate",
        "syntax": "QTD([Member_Expression])",
        "description": "Set of members from start of quarter to current member.",
    },
    "WTD": {
        "category": "periodtodate",
        "syntax": "WTD([Member_Expression])",
        "description": "Set of members from start of week to current member.",
    },
    "PeriodsToDate": {
        "category": "set",
        "syntax": "PeriodsToDate(Level_Expression, Member_Expression)",
        "description": "Set of members from start of a containing period to a member.",
    },
    "Filter": {
        "category": "set",
        "syntax": "Filter(Set_Expression, Logical_Expression)",
        "description": "Filters a set based on a logical condition.",
    },
    "Order": {
        "category": "set",
        "syntax": "Order(Set_Expression, Numeric_Expression [, ASC | DESC | BASC | BDESC])",
        "description": "Orders a set by a numeric expression.",
    },
    "TopCount": {
        "category": "set",
        "syntax": "TopCount(Set_Expression, Count [, Numeric_Expression])",
        "description": "Top N members by a numeric expression.",
    },
    "BottomCount": {
        "category": "set",
        "syntax": "BottomCount(Set_Expression, Count [, Numeric_Expression])",
        "description": "Bottom N members by a numeric expression.",
    },
    "Crossjoin": {
        "category": "set",
        "syntax": "Crossjoin(Set_Expression1, Set_Expression2)",
        "description": "Cross product of two sets.",
    },
    "Descendants": {
        "category": "set",
        "syntax": "Descendants(Member_Expression [, Level_Expression])",
        "description": "Descendants of a member at a specified level.",
    },
    "Union": {
        "category": "set",
        "syntax": "Union(Set_Expression1, Set_Expression2)",
        "description": "Union of two sets (removes duplicates).",
    },
    "Except": {
        "category": "set",
        "syntax": "Except(Set_Expression1, Set_Expression2)",
        "description": "Set difference — members in set1 not in set2.",
    },
    "Intersect": {
        "category": "set",
        "syntax": "Intersect(Set_Expression1, Set_Expression2)",
        "description": "Intersection of two sets.",
    },
    "NonEmpty": {
        "category": "set",
        "syntax": "NonEmpty(Set_Expression [, Measure_Expression])",
        "description": "Filters out empty members from a set.",
    },
    # ── Logical Functions ──
    "ISEMPTY": {
        "category": "logical",
        "syntax": "ISEMPTY(Expression)",
        "description": "Returns true if the expression evaluates to an empty cell.",
    },
    "IS": {
        "category": "logical",
        "syntax": "Expression1 IS Expression2",
        "description": "Returns true if two member expressions refer to the same member.",
    },
    "AND": {
        "category": "logical",
        "syntax": "AND(Condition1, Condition2)",
        "description": "Logical AND.",
    },
    "OR": {
        "category": "logical",
        "syntax": "OR(Condition1, Condition2)",
        "description": "Logical OR.",
    },
    "NOT": {
        "category": "logical",
        "syntax": "NOT(Condition)",
        "description": "Logical NOT.",
    },
    # ── Date Functions ──
    "DateAdd": {
        "category": "date",
        "syntax": "DateAdd(Interval, Number, Date_Expression)",
        "description": "Adds an interval to a date.",
    },
    "DateDiff": {
        "category": "date",
        "syntax": "DateDiff(Interval, Date1, Date2)",
        "description": "Difference between two dates.",
    },
    "CDate": {
        "category": "date",
        "syntax": "CDate(String_Expression)",
        "description": "Converts a string to a date.",
    },
}


# ─── Expression Templates ────────────────────────────────────────────────────
# Each template: name -> (description, callable that returns an MDX expression string)
# The callable receives kwargs: measure_name, date_hierarchy (default "[Date].[Calendar]")

def _ytd_expr(measure_name: str, date_hierarchy: str = "[Date].[Calendar]") -> str:
    return f"SUM(YTD({date_hierarchy}.CurrentMember), [Measures].[{measure_name}])"


def _qtd_expr(measure_name: str, date_hierarchy: str = "[Date].[Calendar]") -> str:
    return f"SUM(QTD({date_hierarchy}.CurrentMember), [Measures].[{measure_name}])"


def _mtd_expr(measure_name: str, date_hierarchy: str = "[Date].[Calendar]") -> str:
    return f"SUM(MTD({date_hierarchy}.CurrentMember), [Measures].[{measure_name}])"


def _prior_year_expr(measure_name: str, date_hierarchy: str = "[Date].[Calendar]") -> str:
    return (
        f"([Measures].[{measure_name}], "
        f"ParallelPeriod({date_hierarchy}.[Calendar Year], 1, {date_hierarchy}.CurrentMember))"
    )


def _yoy_growth_expr(measure_name: str, date_hierarchy: str = "[Date].[Calendar]") -> str:
    prior = _prior_year_expr(measure_name, date_hierarchy)
    return (
        f"IIF({prior} = 0, NULL, "
        f"([Measures].[{measure_name}] - {prior}) / {prior})"
    )


def _profit_margin_pct_expr(sales_measure: str, cost_measure: str, **_: Any) -> str:
    return (
        f"IIF([Measures].[{sales_measure}] = 0, NULL, "
        f"([Measures].[{sales_measure}] - [Measures].[{cost_measure}]) "
        f"/ [Measures].[{sales_measure}])"
    )


def _safe_divide_expr(numerator: str, denominator: str, default: str = "0", **_: Any) -> str:
    return f"DIVIDE([Measures].[{numerator}], [Measures].[{denominator}], {default})"


def _running_total_expr(measure_name: str, date_hierarchy: str = "[Date].[Calendar]") -> str:
    return (
        f"SUM(PeriodsToDate({date_hierarchy}.[Calendar Year], "
        f"{date_hierarchy}.CurrentMember), [Measures].[{measure_name}])"
    )


def _rank_expr(measure_name: str, hierarchy: str = "[Product].[Product Categories]", **_: Any) -> str:
    return (
        f"Rank({hierarchy}.CurrentMember, "
        f"Order({hierarchy}.Members, [Measures].[{measure_name}], BDESC), "
        f"[Measures].[{measure_name}])"
    )


EXPRESSION_TEMPLATES: dict[str, dict[str, Any]] = {
    "ytd": {
        "description": "Year-to-date cumulative measure",
        "builder": _ytd_expr,
    },
    "qtd": {
        "description": "Quarter-to-date cumulative measure",
        "builder": _qtd_expr,
    },
    "mtd": {
        "description": "Month-to-date cumulative measure",
        "builder": _mtd_expr,
    },
    "prior_year": {
        "description": "Same period in prior year",
        "builder": _prior_year_expr,
    },
    "yoy_growth": {
        "description": "Year-over-year growth percentage",
        "builder": _yoy_growth_expr,
    },
    "profit_margin_pct": {
        "description": "Profit margin percentage (safe division)",
        "builder": _profit_margin_pct_expr,
    },
    "safe_divide": {
        "description": "Safe division with divide-by-zero fallback",
        "builder": _safe_divide_expr,
    },
    "running_total": {
        "description": "Running total from start of containing period",
        "builder": _running_total_expr,
    },
    "rank": {
        "description": "Rank of current member within a hierarchy by measure value",
        "builder": _rank_expr,
    },
}


# ─── DAX-to-MDX Converter ────────────────────────────────────────────────────
# LLMs often produce DAX syntax (TOTALYTD, CALCULATE, SAMEPERIODLASTYEAR, etc.)
# These rules automatically convert common DAX patterns to Kyvos MDX equivalents.

_DAX_TO_MDX_RULES: list[tuple[re.Pattern, str | callable]] = [
    # TOTALYTD([Measure], 'date'[date]) -> SUM(YTD([Date].[Calendar].CurrentMember), [Measures].[Measure])
    (
        re.compile(
            r"TOTALYTD\s*\(\s*\[([^\]]+)\]\s*,\s*'date'\[date\]\s*\)",
            re.IGNORECASE,
        ),
        lambda m: f"SUM(YTD([Date].[Calendar].CurrentMember), [Measures].[{m.group(1)}])",
    ),
    # TOTALQTD([Measure], 'date'[date]) -> SUM(QTD(...), [Measures].[Measure])
    (
        re.compile(
            r"TOTALQTD\s*\(\s*\[([^\]]+)\]\s*,\s*'date'\[date\]\s*\)",
            re.IGNORECASE,
        ),
        lambda m: f"SUM(QTD([Date].[Calendar].CurrentMember), [Measures].[{m.group(1)}])",
    ),
    # TOTALMTD([Measure], 'date'[date]) -> SUM(MTD(...), [Measures].[Measure])
    (
        re.compile(
            r"TOTALMTD\s*\(\s*\[([^\]]+)\]\s*,\s*'date'\[date\]\s*\)",
            re.IGNORECASE,
        ),
        lambda m: f"SUM(MTD([Date].[Calendar].CurrentMember), [Measures].[{m.group(1)}])",
    ),
    # CALCULATE([Measure], SAMEPERIODLASTYEAR('date'[date]))
    #   -> ([Measures].[Measure], ParallelPeriod([Date].[Calendar].[Calendar Year], 1, [Date].[Calendar].CurrentMember))
    (
        re.compile(
            r"CALCULATE\s*\(\s*\[([^\]]+)\]\s*,\s*SAMEPERIODLASTYEAR\s*\(\s*'date'\[date\]\s*\)\s*\)",
            re.IGNORECASE,
        ),
        lambda m: (
            f"([Measures].[{m.group(1)}], "
            f"ParallelPeriod([Date].[Calendar].[Calendar Year], 1, "
            f"[Date].[Calendar].CurrentMember))"
        ),
    ),
    # SAMEPERIODLASTYEAR('date'[date]) alone -> ParallelPeriod(...)
    (
        re.compile(
            r"SAMEPERIODLASTYEAR\s*\(\s*'date'\[date\]\s*\)",
            re.IGNORECASE,
        ),
        lambda _m: (
            "ParallelPeriod([Date].[Calendar].[Calendar Year], 1, "
            "[Date].[Calendar].CurrentMember)"
        ),
    ),
    # DATEADD('date'[date], -1, YEAR) -> ParallelPeriod([Date].[Calendar].[Calendar Year], 1, ...)
    (
        re.compile(
            r"DATEADD\s*\(\s*'date'\[date\]\s*,\s*-1\s*,\s*YEAR\s*\)",
            re.IGNORECASE,
        ),
        lambda _m: (
            "ParallelPeriod([Date].[Calendar].[Calendar Year], 1, "
            "[Date].[Calendar].CurrentMember)"
        ),
    ),
    # [Measure Name] -> [Measures].[Measure Name]  (bare measure references)
    # Only convert [Name] that are NOT already [Measures].[...] or [Date].[...] etc.
    # This is handled carefully to avoid double-converting.
    # We skip this rule to avoid false positives — the LLM should use [Measures].[X] directly.
]

# DAX function names that should be flagged as unsupported
_DAX_FUNCTIONS = {
    "TOTALYTD", "TOTALQTD", "TOTALMTD",
    "CALCULATE", "SAMEPERIODLASTYEAR", "DATEADD",
    "FILTER", "ALL", "ALLEXCEPT", "VALUES",
    "DIVIDE",  # DAX DIVIDE exists but Kyvos also has DIVIDE — only flag if in DAX context
    "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX",
    "RANKX", "TOPN",
    "EARLIER", "EARLIEST",
    "RELATED", "RELATEDTABLE",
    "USERELATIONSHIP",
    "CROSSFILTER",
    "TREATAS",
    "GENERATE",
    "SUMMARIZE",
    "ADDCOLUMNS",
    "SELECTCOLUMNS",
    "GROUPBY",
}


def convert_dax_to_mdx(expression: str) -> str:
    """Convert common DAX patterns in an expression to Kyvos MDX equivalents.

    Args:
        expression: A calculated measure expression that may contain DAX syntax.

    Returns:
        The expression with DAX patterns replaced by MDX equivalents.
    """
    result = expression
    for pattern, replacement in _DAX_TO_MDX_RULES:
        if callable(replacement):
            result = pattern.sub(replacement, result)
        else:
            result = pattern.sub(replacement, result)
    return result


def validate_mdx_expression(expression: str) -> list[str]:
    """Validate that an expression uses only Kyvos-supported MDX functions.

    Args:
        expression: A calculated measure expression.

    Returns:
        List of warning messages for unsupported functions. Empty list = valid.
    """
    warnings: list[str] = []

    # Extract function-like calls: WORD followed by (
    func_pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    found_funcs = set(m.group(1) for m in func_pattern.finditer(expression))

    # Check for DAX functions (case-insensitive)
    for func in found_funcs:
        if func.upper() in _DAX_FUNCTIONS:
            # Check if it's also a valid MDX function (some names overlap)
            if func not in MDX_FUNCTIONS and func.lower() not in MDX_FUNCTIONS:
                warnings.append(
                    f"DAX function '{func}' is not a supported Kyvos MDX function. "
                    f"Use the MDX equivalent (see Kyvos MDX Functions Guide)."
                )

    return warnings


def get_mdx_prompt_summary() -> str:
    """Return a concise summary of Kyvos MDX syntax for LLM prompts.

    This is intentionally brief — the full catalog is in this module.
    The prompt tells the LLM to use Kyvos MDX, and the converter handles mistakes.
    """
    return (
        "### Calculated Measures — Use Kyvos MDX Syntax (NOT DAX)\n"
        "Kyvos uses MDX (Multidimensional Expressions) for calculated measures.\n"
        "Do NOT use DAX functions (TOTALYTD, TOTALQTD, TOTALMTD, CALCULATE, SAMEPERIODLASTYEAR, etc.).\n"
        "Use these MDX patterns instead:\n"
        "- YTD: SUM(YTD([Date].[Calendar].CurrentMember), [Measures].[Measure Name])\n"
        "- QTD: SUM(QTD([Date].[Calendar].CurrentMember), [Measures].[Measure Name])\n"
        "- MTD: SUM(MTD([Date].[Calendar].CurrentMember), [Measures].[Measure Name])\n"
        "- Prior Year: ([Measures].[Measure Name], ParallelPeriod([Date].[Calendar].[Calendar Year], 1, [Date].[Calendar].CurrentMember))\n"
        "- YoY Growth: IIF([Measures].[Prior Year Sales] = 0, NULL, ([Measures].[Sales] - [Measures].[Prior Year Sales]) / [Measures].[Prior Year Sales])\n"
        "- Safe Division: DIVIDE(numerator, denominator, 0)\n"
        "- Conditional: IIF(condition, true_value, false_value) or CASE WHEN ... THEN ... END\n"
        "Reference: https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1232535557/Kyvos+MDX+Functions+Guide\n"
    )


def build_expression(template_name: str, **kwargs: Any) -> str:
    """Build an MDX expression from a named template.

    Args:
        template_name: Name of the template (e.g., 'ytd', 'yoy_growth').
        **kwargs: Arguments for the template builder (measure_name, date_hierarchy, etc.).

    Returns:
        MDX expression string.

    Raises:
        KeyError: If template_name is not found.
    """
    template = EXPRESSION_TEMPLATES[template_name]
    return template["builder"](**kwargs)
