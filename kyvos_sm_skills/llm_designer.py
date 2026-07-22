"""LLM-powered SM design — uses Anthropic API to generate semantic model recommendations.

Takes a warehouse schema inspection result + user intent, sends them to Claude
with the discover-sm-from-warehouse skill system prompt, and returns a structured
SM recommendation dict ready for the spec builder.

All Anthropic SDK imports are lazy so the module can be imported without
the anthropic package installed.

Usage::

    from kyvos_sm_skills.llm_designer import design_sm_from_schema

    recommendation = design_sm_from_schema(
        schema_summary=inspected_schema,
        user_intent="I want sales analytics for Adventure Works",
        domain="adventure_works",
    )
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kyvos_sm_skills.mdx_reference import get_mdx_prompt_summary
from kyvos_sm_skills.knowledge_base import get_knowledge_base_summary


def _ensure_anthropic() -> None:
    """Import anthropic lazily and raise a helpful error if not installed."""
    try:
        import anthropic  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "anthropic is required for LLM-based SM design. "
            "Install with: pip install kyvos-sm-skills[anthropic]"
        ) from exc


def _ensure_openai() -> None:
    """Import openai lazily and raise a helpful error if not installed."""
    try:
        import openai  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "openai is required for Azure OpenAI-based SM design. "
            "Install with: pip install openai"
        ) from exc


def _load_skill_system_prompt() -> str:
    """Load the discover-sm-from-warehouse skill file as the system prompt."""
    skill_path = Path(__file__).resolve().parent / "skills" / "discover-sm-from-warehouse.md"
    if not skill_path.exists():
        raise FileNotFoundError(
            f"Skill file not found: {skill_path}. "
            "Ensure kyvos-sm-skills is properly installed."
        )
    return skill_path.read_text()


def _build_user_message(
    schema_summary: dict[str, Any],
    user_intent: str,
    domain: str | None = None,
    allow_web_research: bool = True,
    sm_hints: dict[str, Any] | None = None,
) -> str:
    """Build the user message from schema summary + intent + hints."""
    parts = []

    parts.append(f"## User Intent\n{user_intent}\n")

    if domain:
        parts.append(f"## Domain\n{domain}\n")

    parts.append(f"## Allow Web Research\n{allow_web_research}\n")

    if sm_hints:
        parts.append(f"## SM Hints\n{json.dumps(sm_hints, indent=2)}\n")

    # Include schema summary (compact form to save tokens)
    schema_compact = {
        "warehouse_type": schema_summary.get("warehouse_type"),
        "schema": schema_summary.get("schema"),
        "table_count": schema_summary.get("table_count"),
        "tables": [
            {
                "name": t["name"],
                "type": t.get("estimated_table_type", "unknown"),
                "columns": [
                    {"name": c["name"], "type": c.get("data_type", ""), "pk": c.get("is_pk", False), "fk": c.get("is_fk", False)}
                    for c in t.get("columns", [])
                ],
            }
            for t in schema_summary.get("tables", [])
        ],
        "relationships": schema_summary.get("relationships", []),
        "detected_patterns": schema_summary.get("detected_patterns", {}),
    }
    parts.append(f"## Existing Schema Context\n```json\n{json.dumps(schema_compact, indent=2)}\n```\n")

    parts.append(
        "## Instructions\n"
        "Based on the warehouse schema above and the user's analytics intent, "
        "recommend one or more enterprise-quality semantic models. "
        "Return your response as JSON matching the output schema in the system prompt. "
        "Include: recommended_sms (with name, schema_type, rationale, tables, relationships, measures, hierarchies), "
        "identified_domain, domain_research_summary, domain_reasoning, and gaps_identified.\n\n"
        "## Advanced Design Requirements\n"
        "When the user intent calls for advanced analytics, incorporate these capabilities:\n\n"
        "### Multiple Fact Tables (Multifact Schema)\n"
        "If the warehouse has multiple fact tables sharing conformed dimensions, recommend a multifact schema. "
        "Include all relevant fact tables and their shared dimensions.\n\n"
        "### Many-to-Many Relationships\n"
        "For bridge/junction tables (e.g., sales_reasons linking orders to sales_reason, exchange_rates linking currencies), "
        "model them as many-to-many relationships. Set relationship_type to 'many_to_many' for these. "
        "Include the bridge table in the tables list.\n\n"
        "### Calculated Measures\n"
        "For derived KPIs that don't map directly to a single column, create calculated measures. "
        "Set is_calculated to true and provide the expression.\n\n"
        + get_mdx_prompt_summary() +
        "\n" + get_knowledge_base_summary() +
        "\n### Time Intelligence Measures\n"
        "If a date dimension exists or can be derived, include time intelligence calculated measures using MDX:\n"
        "- YTD (Year-to-Date): SUM(YTD([Date].[Calendar].CurrentMember), [Measures].[Sales Amount])\n"
        "- QTD (Quarter-to-Date): SUM(QTD([Date].[Calendar].CurrentMember), [Measures].[Sales Amount])\n"
        "- MTD (Month-to-Date): SUM(MTD([Date].[Calendar].CurrentMember), [Measures].[Sales Amount])\n"
        "- Prior Year: ([Measures].[Sales Amount], ParallelPeriod([Date].[Calendar].[Calendar Year], 1, [Date].[Calendar].CurrentMember))\n"
        "- YoY Growth: IIF([Measures].[Prior Year Sales] = 0, NULL, ([Measures].[Sales Amount] - [Measures].[Prior Year Sales]) / [Measures].[Prior Year Sales])\n"
        "These should be calculated measures (is_calculated=true) with the expression field populated.\n\n"
        "### Relationship Types\n"
        "For each relationship, include a 'relationship_type' field: 'many_to_one' (default) or 'many_to_many'.\n\n"
        "### No Self-Join Relationships\n"
        "Do NOT create relationships where from_table and to_table are the same table (self-joins). "
        "Parent-child relationships (e.g., employee.parentemployeekey -> employee.employeekey, "
        "account.parentaccountkey -> account.accountkey, organization.parentorganizationkey -> organization.organizationkey) "
        "should be modeled as hierarchies in the hierarchies list, NOT as relationships. "
        "Kyvos DRD does not support self-join relationships.\n\n"
        "### Date Dimension Relationships\n"
        "Only create relationships between a date dimension and fact tables if the fact table has a column "
        "with the same name and compatible type as the date dimension's primary key. "
        "Do NOT create date relationships using mismatched column types (e.g., INTEGER datekey to DATE startdate). "
        "If no proper date key FK exists in the fact table, omit the date relationship.\n\n"
        "### Measure Output Format\n"
        "Each measure should include: name, source_dataset, aggregation_type, and optionally expression and is_calculated. "
        "For base measures, source_dataset and aggregation_type are required. "
        "For calculated measures, expression and is_calculated=true are required; source_dataset may be omitted.\n\n"
        "### Hierarchies\n"
        "Include rich hierarchies reflecting business rollups. Each hierarchy MUST:\n"
        "1. Specify a 'source_dataset' — the dimension table the hierarchy belongs to.\n"
        "2. List 'levels' as ACTUAL COLUMN NAMES that exist on that source_dataset table. "
        "Each level must be a real column from the table's schema (shown in the Existing Schema Context above). "
        "Do NOT use made-up or business-friendly names — use the exact column names from the warehouse schema.\n"
        "3. Order levels from the broadest (top of hierarchy) to the most granular (leaf level).\n"
        "4. For parent-child hierarchies (e.g., Employee, Organization, Account), set is_parent_child=true, "
        "and provide parent_column and child_column as the actual column names. "
        "Parent-child hierarchies do NOT need a 'levels' list — they use parent_column and child_column instead. "
        "Both parent_column and child_column MUST exist on the same source_dataset table and have the same data type. "
        "You may also provide: root_member_type ('auto', 'parent_is_self', or 'parent_is_blank'), "
        "display_column (a column name for display, e.g., 'fullname'), "
        "pc_level_naming_pattern (e.g., 'Level_*' or 'CEO,VP,Manager,Employee'), "
        "non_leaf_data_member_visible (true/false), and non_leaf_data_member_caption (e.g., 'self').\n\n"
        "Reference: https://docs.support.kyvosinsights.com/wiki/spaces/KD20266/pages/1228748942/Creating+parent+child+hierarchies\n\n"
        "Examples (levels must match actual columns on the dimension table):\n"
        "- Product (source_dataset=Product): [productcategorykey, productsubcategorykey, productkey] if those columns exist on the Product table\n"
        "- Sales Territory (source_dataset=SalesTerritory): [salesterritorygroup, salesterritorycountry, salesterritorykey]\n"
        "- Date (source_dataset=Date): [calendaryear, calendarquarter, monthnumber, datekey] or similar columns that exist on the Date table\n"
        "- Employee (source_dataset=Employee, parent-child): parent_column=parentemployeekey, child_column=employeekey, "
        "display_column=fullname, root_member_type=parent_is_blank, pc_level_naming_pattern=CEO,VP,Manager,Employee\n"
        "- Organization (source_dataset=Organization, parent-child): parent_column=parentorganizationkey, child_column=organizationkey\n"
        "- Account (source_dataset=Account, parent-child): parent_column=parentaccountkey, child_column=accountkey\n"
        "IMPORTANT: Before listing a level, verify the column exists on the source_dataset table in the schema context. "
        "If a natural hierarchy column doesn't exist, omit that hierarchy rather than guessing.\n"
    )

    return "\n".join(parts)


def _extract_json_from_response(text: str) -> dict[str, Any]:
    """Extract JSON from an LLM response that may contain markdown code fences.

    Handles common LLM JSON issues:
    - Markdown code fences (```json ... ``` or ``` ... ```)
    - Trailing commas (common LLM mistake)
    - Truncated responses (attempts to close braces)
    - Multiple code fence blocks
    """
    import re as _re

    json_str = None

    # Try to find JSON in code fences first
    if "```json" in text:
        start = text.index("```json") + 7
        # Find the matching closing fence
        end_idx = text.find("```", start)
        if end_idx != -1:
            json_str = text[start:end_idx].strip()
        else:
            # No closing fence — take everything after ```json
            json_str = text[start:].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end_idx = text.find("```", start)
        if end_idx != -1:
            json_str = text[start:end_idx].strip()
        else:
            json_str = text[start:].strip()
    else:
        # Try parsing the whole text as JSON
        json_str = text.strip()

    # Attempt 1: direct parse
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Attempt 2: remove trailing commas (common LLM mistake)
    # Trailing commas in objects: ,} or ,\s*}
    # Trailing commas in arrays: ,] or ,\s*]
    cleaned = _re.sub(r",\s*([}\]])", r"\1", json_str)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 3: try to fix truncated JSON by closing open braces/brackets
    _open_braces = cleaned.count("{") - cleaned.count("}")
    _open_brackets = cleaned.count("[") - cleaned.count("]")
    if _open_braces > 0 or _open_brackets > 0:
        _fixed = cleaned
        # Remove any trailing incomplete key-value or string
        _fixed = _re.sub(r'[\s,]*"[^"]*"\s*:\s*$', "", _fixed)
        _fixed = _re.sub(r'[\s,]*"[^"]*"\s*$', "", _fixed)
        _fixed += "]" * max(_open_brackets, 0)
        _fixed += "}" * max(_open_braces, 0)
        try:
            return json.loads(_fixed)
        except json.JSONDecodeError:
            pass

    # Attempt 4: find the last valid JSON object by trimming from the end
    for trim_pos in range(len(cleaned) - 1, 0, -1):
        if cleaned[trim_pos] == "}":
            _candidate = cleaned[:trim_pos + 1]
            _candidate = _re.sub(r",\s*([}\]])", r"\1", _candidate)
            try:
                return json.loads(_candidate)
            except json.JSONDecodeError:
                continue

    # All attempts failed — raise with diagnostic info
    raise json.JSONDecodeError(
        f"Failed to parse JSON after cleanup attempts. "
        f"First 200 chars: {json_str[:200]}... "
        f"Last 200 chars: ...{json_str[-200:]}",
        json_str,
        0,
    )


def _call_anthropic(
    system_prompt: str,
    user_message: str,
    api_key: str,
    model: str,
    max_tokens: int,
) -> str:
    """Call Anthropic API and return response text."""
    _ensure_anthropic()
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    response_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            response_text += block.text
    return response_text


def _call_azure_openai(
    system_prompt: str,
    user_message: str,
    api_key: str,
    endpoint: str,
    deployment_name: str,
    api_version: str,
    max_tokens: int,
) -> str:
    """Call Azure OpenAI API and return response text."""
    _ensure_openai()
    from openai import AzureOpenAI

    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )
    response = client.chat.completions.create(
        model=deployment_name,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content or ""


def design_sm_from_schema(
    schema_summary: dict[str, Any],
    user_intent: str,
    domain: str | None = None,
    allow_web_research: bool = True,
    sm_hints: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 16384,
    llm_provider: str | None = None,
) -> dict[str, Any]:
    """Generate an SM recommendation from warehouse schema + user intent via LLM.

    Supports two LLM providers:
    - ``anthropic`` (default): Uses Anthropic API with Claude models.
    - ``azure_openai``: Uses Azure OpenAI with GPT models.

    The provider is selected via:
    1. Explicit ``llm_provider`` parameter
    2. ``LLM_PROVIDER`` env var
    3. Default: ``anthropic``

    For Anthropic:
        - ``api_key`` or ``ANTHROPIC_API_KEY`` env var
        - ``model`` parameter (default: claude-sonnet-4-20250514)

    For Azure OpenAI:
        - ``AZURE_OPENAI_API_KEY`` or ``LLM_API_KEY`` env var (or ``api_key`` param)
        - ``AZURE_ENDPOINT`` or ``AZURE_OPENAI_ENDPOINT`` env var
        - ``AZURE_DEPLOYMENT_NAME`` env var (or ``model`` param)
        - ``AZURE_API_VERSION`` env var

    Args:
        schema_summary: Dict from ``inspect_schema()``.
        user_intent: Natural language description of desired analytics.
        domain: Optional domain hint.
        allow_web_research: If False, instruct LLM to use built-in knowledge only.
        sm_hints: Optional dict with max_sms, preferred_schema_type, etc.
        api_key: LLM API key. If None, reads from env var.
        model: Model name (Anthropic) or deployment name (Azure OpenAI).
        max_tokens: Max response tokens.
        llm_provider: "anthropic" or "azure_openai". If None, reads LLM_PROVIDER env var.

    Returns:
        SM recommendation dict matching the skill's output schema.

    Raises:
        ImportError: If required SDK is not installed.
        ValueError: If API key is missing or response cannot be parsed as JSON.
    """
    # Resolve provider
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()

    # Load system prompt from skill file
    system_prompt = _load_skill_system_prompt()

    # Build user message
    user_message = _build_user_message(
        schema_summary=schema_summary,
        user_intent=user_intent,
        domain=domain,
        allow_web_research=allow_web_research,
        sm_hints=sm_hints,
    )

    # Resolve provider-specific config
    if provider == "azure_openai":
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
        deployment = model if model != "claude-sonnet-4-20250514" else os.environ.get("AZURE_DEPLOYMENT_NAME", "gpt-4.1")
        api_version = os.environ.get("AZURE_API_VERSION", "2024-12-01-preview")

        def _call_llm(msg: str) -> str:
            return _call_azure_openai(
                system_prompt=system_prompt,
                user_message=msg,
                api_key=resolved_key,
                endpoint=endpoint,
                deployment_name=deployment,
                api_version=api_version,
                max_tokens=max_tokens,
            )
    else:
        # Anthropic (default)
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY env var or pass api_key parameter."
            )

        def _call_llm(msg: str) -> str:
            return _call_anthropic(
                system_prompt=system_prompt,
                user_message=msg,
                api_key=resolved_key,
                model=model,
                max_tokens=max_tokens,
            )

    # Call LLM with retry on validation failure
    max_retries = 2
    current_message = user_message
    recommendation = None

    for attempt in range(max_retries + 1):
        response_text = _call_llm(current_message)

        # Parse JSON from response
        try:
            recommendation = _extract_json_from_response(response_text)
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt < max_retries:
                print(f"  LLM attempt {attempt + 1} returned invalid JSON, retrying...")
                print(f"    Parse error: {exc}")
                retry_feedback = (
                    f"\n\n## JSON Parse Error (Attempt {attempt + 1})\n"
                    f"Your previous response could not be parsed as valid JSON.\n"
                    f"Error: {exc}\n\n"
                    "Please return a valid JSON object. Common issues to fix:\n"
                    "- Remove trailing commas before } or ]\n"
                    "- Ensure all strings are properly quoted\n"
                    "- Do not include any text outside the JSON object\n"
                    "- Make sure the JSON is complete and not truncated\n"
                )
                current_message = user_message + retry_feedback
                continue
            raise ValueError(
                f"Failed to parse LLM response as JSON after {max_retries + 1} attempts: {exc}\n"
                f"Response text (first 500 chars): {response_text[:500]}\n"
                f"Response text (last 500 chars): {response_text[-500:]}"
            ) from exc

        # Validate against schema
        errors = validate_sm_recommendation(recommendation, schema_summary)
        if not errors:
            return recommendation

        if attempt < max_retries:
            print(f"  LLM attempt {attempt + 1} had {len(errors)} validation error(s), retrying...")
            for err in errors:
                print(f"    - {err}")

            # Build retry message with validation feedback
            retry_feedback = (
                f"\n\n## Validation Errors (Attempt {attempt + 1})\n"
                f"Your previous response had these validation errors against the warehouse schema:\n"
            )
            for err in errors:
                retry_feedback += f"- {err}\n"
            retry_feedback += (
                "\nPlease fix these errors and return the corrected JSON. "
                "Make sure all table names, column names, and source_dataset values "
                "exactly match the schema context provided above."
            )
            current_message = user_message + retry_feedback
        else:
            print(f"  LLM produced {len(errors)} validation error(s) after {max_retries + 1} attempts.")

    return recommendation


def validate_sm_recommendation(
    rec: dict[str, Any],
    schema_summary: dict[str, Any],
) -> list[str]:
    """Validate an SM recommendation against the inspected warehouse schema.

    Args:
        rec: SM recommendation dict from ``design_sm_from_schema()``.
        schema_summary: Dict from ``inspect_schema()``.

    Returns:
        List of validation error strings. Empty list = valid.
    """
    errors: list[str] = []

    # Build set of available table names (case-insensitive)
    available_tables = {t["name"].lower() for t in schema_summary.get("tables", [])}
    table_col_map: dict[str, set[str]] = {}
    for t in schema_summary.get("tables", []):
        table_col_map[t["name"].lower()] = {c["name"].lower() for c in t.get("columns", [])}

    recommended_sms = rec.get("recommended_sms", [])
    if not recommended_sms:
        errors.append("No SMs in recommendation (recommended_sms is empty)")
        return errors

    for i, sm in enumerate(recommended_sms):
        sm_name = sm.get("name", f"SM_{i}")
        sm_tables = sm.get("tables", [])

        # Check tables exist
        for table_name in sm_tables:
            if table_name.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': table '{table_name}' not found in warehouse schema"
                )

        # Check relationships reference valid tables and columns
        for rel in sm.get("relationships", []):
            from_table = rel.get("from_table", "")
            to_table = rel.get("to_table", "")
            from_column = rel.get("from_column", "")
            to_column = rel.get("to_column", "")

            if from_table.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': relationship from_table '{from_table}' not in warehouse"
                )
            elif from_column.lower() not in table_col_map.get(from_table.lower(), set()):
                errors.append(
                    f"SM '{sm_name}': relationship from_column '{from_column}' not in table '{from_table}'"
                )

            if to_table.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': relationship to_table '{to_table}' not in warehouse"
                )
            elif to_column.lower() not in table_col_map.get(to_table.lower(), set()):
                errors.append(
                    f"SM '{sm_name}': relationship to_column '{to_column}' not in table '{to_table}'"
                )

        # Check measure source_dataset matches a table (skip for calculated measures)
        for measure in sm.get("measures", []):
            is_calc = measure.get("is_calculated", False)
            source = measure.get("source_dataset", "")
            if source and not is_calc and source.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': measure '{measure.get('name', '?')}' "
                    f"source_dataset '{source}' not in warehouse"
                )

    return errors


def format_recommendation_for_review(rec: dict[str, Any]) -> str:
    """Pretty-print an SM recommendation for user review at an approval gate.

    Args:
        rec: SM recommendation dict from ``design_sm_from_schema()``.

    Returns:
        Formatted string suitable for printing to the console.
    """
    lines = []
    lines.append("=" * 70)
    lines.append("  SM Design Recommendation — Review for Approval")
    lines.append("=" * 70)

    # Domain info
    domain = rec.get("identified_domain", "unknown")
    lines.append(f"\n  Identified Domain: {domain}")
    lines.append(f"\n  Domain Research Summary:")
    lines.append(f"  {rec.get('domain_research_summary', 'N/A')}")

    if rec.get("domain_reasoning"):
        lines.append(f"\n  Domain Reasoning:")
        lines.append(f"  {rec['domain_reasoning']}")

    # Gaps
    gaps = rec.get("gaps_identified", [])
    if gaps:
        lines.append(f"\n  Gaps Identified:")
        for gap in gaps:
            lines.append(f"    - {gap}")

    # SMs
    recommended_sms = rec.get("recommended_sms", [])
    lines.append(f"\n  Recommended SMs: {len(recommended_sms)}")
    lines.append("")

    for i, sm in enumerate(recommended_sms):
        lines.append(f"  ── SM {i + 1}: {sm.get('name', 'unnamed')} ──")
        lines.append(f"  Schema type: {sm.get('schema_type', 'unknown')}")
        lines.append(f"  Rationale: {sm.get('rationale', 'N/A')}")
        lines.append(f"  Tables ({len(sm.get('tables', []))}): {', '.join(sm.get('tables', []))}")

        rels = sm.get("relationships", [])
        lines.append(f"  Relationships ({len(rels)}):")
        for rel in rels:
            lines.append(
                f"    {rel.get('from_table', '')}.{rel.get('from_column', '')} → "
                f"{rel.get('to_table', '')}.{rel.get('to_column', '')}"
            )

        measures = sm.get("measures", [])
        lines.append(f"  Measures ({len(measures)}):")
        for m in measures:
            lines.append(
                f"    {m.get('name', '?')} ({m.get('aggregation_type', 'sum')}) "
                f"from {m.get('source_dataset', '?')}"
            )

        hierarchies = sm.get("hierarchies", [])
        lines.append(f"  Hierarchies ({len(hierarchies)}):")
        for h in hierarchies:
            lines.append(
                f"    {h.get('name', '?')}: {' → '.join(h.get('levels', []))} "
                f"(from {h.get('source_dataset', '?')})"
            )

        lines.append("")

    lines.append("=" * 70)
    lines.append("  Review the recommendation above.")
    lines.append("  Type 'y' to approve and proceed to deployment,")
    lines.append("  or 'n' to reject and provide adjusted hints.")
    lines.append("=" * 70)

    return "\n".join(lines)
