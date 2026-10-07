"""Automatic intent generation for semantic model design.

This module generates a production-ready user intent by:
1. Filling a domain-agnostic parameterized template with schema-derived values
2. Optionally refining the filled template via an LLM for domain-specific depth
3. Including Kyvos-specific requirements (MDX syntax, parent-child hierarchies)
4. Producing a structured intent document

The generated intent replaces the need for a manually crafted static intent file.
Users can also provide their own intent files that follow the same section structure.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from kyvos_sm_skills.knowledge_base import get_knowledge_base_summary
from kyvos_sm_skills.mdx_reference import get_mdx_prompt_summary
from kyvos_sm_skills.prompt_loader import get_system_prompt, get_user_prompt

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "intent_template.txt"


def _build_intent_system_prompt() -> str:
    """Build the system prompt for intent generation from config file."""
    prompt = get_system_prompt("intent_generation")
    prompt = prompt.replace("{mdx_reference}", get_mdx_prompt_summary())
    prompt = prompt.replace("{knowledge_base}", get_knowledge_base_summary())
    return prompt


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

    instructions = get_user_prompt("intent_generation")
    parts.append(instructions)

    return "\n".join(parts)


def _fill_template(
    schema_summary: dict[str, Any],
    domain: str | None = None,
) -> str | None:
    """Fill the parameterized intent template with schema-derived values.

    Reads ``templates/intent_template.txt`` and replaces ``{{placeholders}}``
    with values derived from the warehouse schema summary.

    Args:
        schema_summary: Dict from ``inspect_schema()`` with tables, columns, etc.
        domain: Optional domain name (e.g., "adventure_works", "retail_ecommerce").

    Returns:
        Filled template string, or ``None`` if the template file is not found.
    """
    if not _TEMPLATE_PATH.exists():
        return None

    template = _TEMPLATE_PATH.read_text()

    # Derive domain label
    domain_label = (domain or "the specified domain").replace("_", " ").title()

    # Derive KPI categories from schema patterns
    tables = schema_summary.get("tables", [])
    schema_summary.get("detected_patterns", {})

    kpi_parts: list[str] = []
    # Revenue-related KPIs if fact tables with amount/price columns exist
    has_amount_cols = any(
        any("amount" in c.get("name", "").lower() or "price" in c.get("name", "").lower()
            for c in t.get("columns", []))
        for t in tables
        if t.get("estimated_table_type") == "fact"
    )
    if has_amount_cols:
        kpi_parts.append("Revenue (total and per-channel), Gross Profit Margin, Customer Growth")
    # Expense-related KPIs if cost columns exist
    has_cost_cols = any(
        any("cost" in c.get("name", "").lower() or "expense" in c.get("name", "").lower()
            for c in t.get("columns", []))
        for t in tables
        if t.get("estimated_table_type") == "fact"
    )
    if has_cost_cols:
        kpi_parts.append("Expense to Revenue Ratio, Operating Profit")
    # Asset-related KPIs if asset columns exist
    has_asset_cols = any(
        any("asset" in c.get("name", "").lower()
            for c in t.get("columns", []))
        for t in tables
    )
    if has_asset_cols:
        kpi_parts.append("Return on Assets")
    if not kpi_parts:
        kpi_parts.append(
            "Revenue (total and per-channel), Gross Profit Margin, "
            "Customer Growth, Expense to Revenue Ratio, Operating Profit"
        )

    kpi_categories = "\n  - ".join(kpi_parts)

    # Replace placeholders
    filled = template.replace("{{domain}}", domain_label)
    filled = filled.replace("{{kpi_categories}}", kpi_categories)

    # Remove any remaining unreplaced placeholders
    filled = re.sub(r"\{\{[^}]+\}\}", "", filled)

    return filled


def generate_intent(
    schema_summary: dict[str, Any],
    domain: str | None = None,
    enterprise_context: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,
    llm_provider: str | None = None,
    use_template: bool = True,
) -> str:
    """Generate a production-ready user intent document.

    By default, fills the parameterized template with schema-derived values.
    When ``use_template=False`` or the template is unavailable, falls back
    to fully LLM-generated intent.

    Args:
        schema_summary: Dict from ``inspect_schema()`` with tables, columns, relationships.
        domain: Optional domain hint (e.g., "adventure_works", "retail_ecommerce").
        enterprise_context: Optional additional context about the enterprise.
        api_key: LLM API key. If None, reads from env var.
        model: Model name (Anthropic) or deployment name (Azure OpenAI).
        max_tokens: Max response tokens.
        llm_provider: "anthropic" or "azure_openai". If None, reads LLM_PROVIDER env var.
        use_template: When True (default), fill the package template first, then
            optionally refine via LLM. When False, use fully LLM-generated intent.

    Returns:
        Generated intent document as a string.

    Raises:
        ImportError: If required SDK is not installed.
        ValueError: If API key is missing and template is unavailable.
    """
    # Try template-based generation first
    if use_template:
        filled = _fill_template(schema_summary, domain)
        if filled is not None:
            return filled

    # Fall back to LLM-based generation
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()

    # Resolve model from env var or use default
    if model is None:
        model = os.environ.get("LLM_MODEL", "") or os.environ.get("ANTHROPIC_MODEL", "")
        if not model:
            model = "claude-sonnet-4-20250514"

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
    use_template: bool = True,
    **kwargs: Any,
) -> str:
    """Generate intent and save to a file.

    Args:
        intent_path: Path to save the generated intent.
        schema_summary: Dict from ``inspect_schema()``.
        domain: Optional domain hint.
        enterprise_context: Optional enterprise context.
        use_template: When True (default), use the package template.
        **kwargs: Additional arguments passed to ``generate_intent()``.

    Returns:
        Generated intent document as a string (also saved to file).
    """
    intent = generate_intent(
        schema_summary=schema_summary,
        domain=domain,
        enterprise_context=enterprise_context,
        use_template=use_template,
        **kwargs,
    )

    Path(intent_path).parent.mkdir(parents=True, exist_ok=True)
    with open(intent_path, "w") as f:
        f.write(intent)

    return intent
