"""LLM-powered automatic intent generation for semantic model design.

This module generates a production-ready user intent by:
1. Analyzing the discovered warehouse schema (tables, columns, relationships, patterns)
2. Researching the domain (e.g., "adventure_works" → bicycle manufacturing/retail)
3. Applying enterprise AI/BI best practices (star schema, conformed dimensions, KPIs)
4. Including Kyvos-specific requirements (MDX syntax, parent-child hierarchies)
5. Producing a structured intent document

The generated intent replaces the need for a manually crafted static intent file.
"""

from __future__ import annotations

import json
import os
from typing import Any

from kyvos_sm_skills.knowledge_base import get_knowledge_base_summary
from kyvos_sm_skills.mdx_reference import get_mdx_prompt_summary


def _build_intent_system_prompt() -> str:
    """Build the system prompt for intent generation."""
    return (
        "You are an expert enterprise AI/BI solutions architect specializing in "
        "Kyvos semantic model design. Your task is to generate a comprehensive, "
        "production-ready user intent document for designing a semantic model.\n\n"
        "The intent document must cover:\n"
        "1. Business Context: What the organization does, key business processes, "
        "and analytical goals.\n"
        "2. Schema Analysis: Key fact tables, dimension tables, and relationships "
        "identified from the warehouse schema.\n"
        "3. Hierarchy Requirements: Natural business hierarchies that should be "
        "modeled, including parent-child hierarchies where applicable.\n"
        "4. KPI/Measure Requirements: Base measures and calculated KPIs using "
        "Kyvos MDX syntax (NOT DAX). Include time intelligence, growth metrics, "
        "and industry-standard KPIs.\n"
        "5. Quality Bar: MVP deployment expectations — the semantic model must be "
        "production-ready with enterprise-grade hierarchies and KPIs.\n\n"
        "The intent should be specific enough to guide LLM-based semantic model "
        "design without further human input. It should reflect deep understanding "
        "of the domain and the warehouse schema.\n\n"
        + get_mdx_prompt_summary()
        + "\n"
        + get_knowledge_base_summary()
    )


def _build_intent_user_message(
    schema_summary: dict[str, Any],
    domain: str | None = None,
    enterprise_context: str | None = None,
) -> str:
    """Build the user message for intent generation.

    Args:
        schema_summary: Dict from ``inspect_schema()`` with tables, columns, relationships.
        domain: Optional domain hint (e.g., "adventure_works", "retail_ecommerce").
        enterprise_context: Optional additional context about the enterprise.

    Returns:
        Formatted user message string for the LLM.
    """
    parts = []

    if domain:
        parts.append(f"## Domain\n{domain}\n")

    if enterprise_context:
        parts.append(f"## Enterprise Context\n{enterprise_context}\n")

    # Include schema summary in compact form
    schema_compact = {
        "warehouse_type": schema_summary.get("warehouse_type"),
        "schema": schema_summary.get("schema"),
        "table_count": schema_summary.get("table_count"),
        "tables": [
            {
                "name": t["name"],
                "type": t.get("estimated_table_type", "unknown"),
                "columns": [
                    {
                        "name": c["name"],
                        "type": c.get("data_type", ""),
                        "pk": c.get("is_pk", False),
                        "fk": c.get("is_fk", False),
                        "references": c.get("references", ""),
                    }
                    for c in t.get("columns", [])
                ],
            }
            for t in schema_summary.get("tables", [])
        ],
        "relationships": schema_summary.get("relationships", []),
        "detected_patterns": schema_summary.get("detected_patterns", {}),
    }
    parts.append(
        f"## Warehouse Schema\n```json\n{json.dumps(schema_compact, indent=2)}\n```\n"
    )

    parts.append(
        "## Instructions\n"
        "Based on the warehouse schema above, generate a comprehensive user intent "
        "document for designing a production-ready Kyvos semantic model.\n\n"
        "The intent should include:\n"
        "1. **Business Context**: What this organization does and the key analytical "
        "questions users would ask.\n"
        "2. **Fact Tables**: Identify all fact tables and their key measures.\n"
        "3. **Dimension Tables**: Identify all dimension tables and their attributes.\n"
        "4. **Hierarchy Requirements**: List natural business hierarchies (e.g., "
        "Product Category → Subcategory → Product, Geography → Country → State → City, "
        "Date → Year → Quarter → Month). For any self-referencing tables (parent_key → "
        "child_key), specify parent-child hierarchies with root_member_type and "
        "level naming.\n"
        "5. **KPI Requirements**: List base measures (sum, count, distinct count) "
        "and calculated KPIs using Kyvos MDX syntax. Include:\n"
        "   - Time intelligence (YTD, QTD, MTD, prior year, YoY growth)\n"
        "   - Profitability (margin, ratio, variance)\n"
        "   - Performance (rank, percentile, running total)\n"
        "6. **Quality Bar**: The semantic model must be MVP-ready with enterprise-grade "
        "hierarchies, KPIs, and MDX calculations suitable for production deployment.\n\n"
        "Format the intent as a structured text document with clear sections. "
        "Do NOT output JSON — output a natural language intent document."
    )

    return "\n".join(parts)


def generate_intent(
    schema_summary: dict[str, Any],
    domain: str | None = None,
    enterprise_context: str | None = None,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
    llm_provider: str | None = None,
) -> str:
    """Generate a production-ready user intent document using LLM.

    Analyzes the warehouse schema, researches the domain, and applies enterprise
    AI/BI best practices to produce a structured intent document that can guide
    semantic model design without further human input.

    Args:
        schema_summary: Dict from ``inspect_schema()`` with tables, columns, relationships.
        domain: Optional domain hint (e.g., "adventure_works", "retail_ecommerce").
        enterprise_context: Optional additional context about the enterprise.
        api_key: LLM API key. If None, reads from env var.
        model: Model name (Anthropic) or deployment name (Azure OpenAI).
        max_tokens: Max response tokens.
        llm_provider: "anthropic" or "azure_openai". If None, reads LLM_PROVIDER env var.

    Returns:
        Generated intent document as a string.

    Raises:
        ImportError: If required SDK is not installed.
        ValueError: If API key is missing.
    """
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()

    system_prompt = _build_intent_system_prompt()
    user_message = _build_intent_user_message(
        schema_summary=schema_summary,
        domain=domain,
        enterprise_context=enterprise_context,
    )

    if provider == "azure_openai":
        from kyvos_sm_skills.llm_designer import _call_azure_openai

        resolved_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "Azure OpenAI API key required. Set AZURE_OPENAI_API_KEY or LLM_API_KEY env var."
            )
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "") or os.environ.get("AZURE_ENDPOINT", "")
        if not endpoint:
            raise ValueError(
                "Azure OpenAI endpoint required. Set AZURE_OPENAI_ENDPOINT or AZURE_ENDPOINT env var."
            )
        api_version = os.environ.get("AZURE_API_VERSION", "2024-12-01-preview")
        deployment = os.environ.get("AZURE_DEPLOYMENT_NAME", model)

        return _call_azure_openai(
            system_prompt=system_prompt,
            user_message=user_message,
            api_key=resolved_key,
            endpoint=endpoint,
            deployment_name=deployment,
            api_version=api_version,
            max_tokens=max_tokens,
        )
    else:
        from kyvos_sm_skills.llm_designer import _call_anthropic

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY env var."
            )

        return _call_anthropic(
            system_prompt=system_prompt,
            user_message=user_message,
            api_key=resolved_key,
            model=model,
            max_tokens=max_tokens,
        )


def generate_intent_from_file(
    intent_path: str,
    schema_summary: dict[str, Any],
    domain: str | None = None,
    enterprise_context: str | None = None,
    **kwargs: Any,
) -> str:
    """Generate intent and save to a file.

    Args:
        intent_path: Path to save the generated intent.
        schema_summary: Dict from ``inspect_schema()``.
        domain: Optional domain hint.
        enterprise_context: Optional enterprise context.
        **kwargs: Additional arguments passed to ``generate_intent()``.

    Returns:
        Generated intent document as a string (also saved to file).
    """
    intent = generate_intent(
        schema_summary=schema_summary,
        domain=domain,
        enterprise_context=enterprise_context,
        **kwargs,
    )

    with open(intent_path, "w") as f:
        f.write(intent)

    return intent
