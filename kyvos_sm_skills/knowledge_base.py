"""Curated Kyvos documentation references for LLM-driven semantic model design.

This module provides a structured knowledge base of Kyvos official documentation
that the LLM can use as reference material when designing semantic models.

The knowledge base includes:
- MDX Functions Guide
- Parent-child hierarchy configuration
- Custom rollups
- Alternate hierarchies
- Semantic model design best practices

Instead of embedding static text in the LLM prompt, this module provides
a structured catalog of documentation URLs and summaries that the LLM
can research dynamically.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocReference:
    """A single Kyvos documentation reference."""

    title: str
    url: str
    category: str  # "mdx", "hierarchy", "rollup", "design", "general"
    summary: str
    key_concepts: list[str] = field(default_factory=list)


# ─── Kyvos Documentation Catalog ────────────────────────────────────────────

KNOWLEDGE_BASE: list[DocReference] = [
    DocReference(
        title="Kyvos MDX Functions Guide",
        url="https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1232535557/Kyvos+MDX+Functions+Guide",
        category="mdx",
        summary=(
            "Complete reference for all MDX functions supported by Kyvos. "
            "Covers scalar functions (SUM, AVG, MIN, MAX, COUNT), time intelligence "
            "(YTD, QTD, MTD, ParallelPeriod), conditional logic (IIF, CASE), "
            "and utility functions (DIVIDE, RANK, COALESCE)."
        ),
        key_concepts=[
            "SUM(Set, Numeric_Expression) — aggregate over a set",
            "YTD/QTD/MTD — time intelligence functions for period-to-date calculations",
            "ParallelPeriod(Level, Offset, Member) — navigate to prior period",
            "IIF(condition, true, false) — conditional expressions",
            "DIVIDE(numerator, denominator, default) — safe division",
            "RANK() — ranking within a set",
            "COALESCE() — null handling",
        ],
    ),
    DocReference(
        title="Creating Parent-Child Hierarchies",
        url="https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1228748942/Creating+parent+child+hierarchies",
        category="hierarchy",
        summary=(
            "Guide for configuring parent-child hierarchies in Kyvos semantic models. "
            "Parent-child hierarchies model recursive relationships (e.g., Employee→Manager, "
            "Organization→Parent Organization, Account→Parent Account) using a single "
            "table with self-referencing parent and child key columns."
        ),
        key_concepts=[
            "Single level with child key field and parent field — not multiple levels",
            "Parent and child columns must have the same data type",
            "Both columns must exist on the same source dataset table",
            "Root member detection: Auto, Parent is self, Parent is blank",
            "Level naming template with wildcard * (e.g., CEO,VP,Manager,Employee)",
            "Non-leaf data member visibility (Hide or Visible) and caption",
            "Custom rollup weight column for non-standard aggregations",
            "Alternate hierarchy support (hasAlternatePath)",
        ],
    ),
    DocReference(
        title="Custom Rollups in Hierarchies",
        url="https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1228748891",
        category="rollup",
        summary=(
            "Guide for custom rollup configurations in Kyvos hierarchies. "
            "Custom rollups allow non-standard aggregation behavior using a "
            "weight column that determines how child values contribute to parent totals."
        ),
        key_concepts=[
            "Custom rollup weight column specifies aggregation weights",
            "Used with parent-child hierarchies for weighted rollups",
            "Weight column must be numeric and exist on the source table",
        ],
    ),
    DocReference(
        title="Alternate Hierarchies",
        url="https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1228749037",
        category="hierarchy",
        summary=(
            "Guide for alternate hierarchy paths in Kyvos. "
            "Alternate hierarchies allow a dimension to have multiple navigation paths "
            "through the same data, enabling different analytical perspectives."
        ),
        key_concepts=[
            "hasAlternatePath flag enables alternate hierarchy support",
            "Multiple paths through the same dimension table",
            "Useful for organizational structures with multiple reporting lines",
        ],
    ),
    DocReference(
        title="Semantic Model Design Best Practices",
        url="https://docs.support.kyvosinsights.com/wiki/spaces/KD20266",
        category="design",
        summary=(
            "General best practices for designing Kyvos semantic models. "
            "Covers star schema vs snowflake, conformed dimensions, measure groups, "
            "and hierarchy design principles for enterprise-grade BI solutions."
        ),
        key_concepts=[
            "Star schema preferred for performance and simplicity",
            "Conformed dimensions enable cross-fact analysis",
            "Measure groups organize related measures",
            "Hierarchies should reflect natural business rollups",
            "Calculated measures use MDX expressions (not DAX)",
            "Time intelligence via YTD/QTD/MTD and ParallelPeriod",
        ],
    ),
]


# ─── Public API ─────────────────────────────────────────────────────────────


def get_knowledge_base_urls() -> list[dict[str, str]]:
    """Return structured list of all documentation URLs.

    Returns:
        List of dicts with keys: title, url, category, summary.
    """
    return [
        {
            "title": ref.title,
            "url": ref.url,
            "category": ref.category,
            "summary": ref.summary,
        }
        for ref in KNOWLEDGE_BASE
    ]


def get_knowledge_base_summary() -> str:
    """Return a concise summary of the knowledge base for LLM prompts.

    This replaces static MDX text in the prompt with a structured reference
    that the LLM can use for research. The summary includes:
    - Available documentation references with URLs
    - Key concepts from each document
    - Instructions to use these as a knowledge base
    """
    lines = [
        "### Knowledge Base — Kyvos Official Documentation References",
        "Use the following Kyvos documentation as your knowledge base for designing "
        "the semantic model. Research these references to ensure your design follows "
        "Kyvos best practices and uses correct MDX syntax and hierarchy configurations.",
        "",
    ]

    for ref in KNOWLEDGE_BASE:
        lines.append(f"**{ref.title}** ({ref.category})")
        lines.append(f"  URL: {ref.url}")
        lines.append(f"  {ref.summary}")
        if ref.key_concepts:
            lines.append("  Key concepts:")
            for concept in ref.key_concepts:
                lines.append(f"    - {concept}")
        lines.append("")

    lines.append(
        "IMPORTANT: When designing calculated measures, use Kyvos MDX syntax (NOT DAX). "
        "When designing hierarchies, verify that all level columns exist on the source "
        "dataset table. For parent-child hierarchies, ensure parent and child columns "
        "have the same data type."
    )

    return "\n".join(lines)


def get_references_by_category(category: str) -> list[DocReference]:
    """Return all documentation references for a given category.

    Args:
        category: One of "mdx", "hierarchy", "rollup", "design", "general".

    Returns:
        List of DocReference objects matching the category.
    """
    return [ref for ref in KNOWLEDGE_BASE if ref.category == category]


def get_mdx_reference() -> DocReference | None:
    """Return the MDX Functions Guide reference, or None if not found."""
    refs = get_references_by_category("mdx")
    return refs[0] if refs else None


def get_parent_child_reference() -> DocReference | None:
    """Return the parent-child hierarchy reference, or None if not found."""
    for ref in KNOWLEDGE_BASE:
        if "parent" in ref.title.lower() and "child" in ref.title.lower():
            return ref
    return None
