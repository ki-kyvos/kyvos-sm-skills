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
import re
import time
from typing import Any

from kyvos_sm_skills.knowledge_base import get_knowledge_base_summary
from kyvos_sm_skills.mdx_reference import get_mdx_prompt_summary
from kyvos_sm_skills.prompt_loader import get_system_prompt, get_user_prompt
from kyvos_sm_skills.spec_builder import _hierarchy_type_family

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
    """Load the system prompt for SM discovery from the config file."""
    return get_system_prompt("discover_sm_from_warehouse")


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
                    {"name": c["name"], "type": c.get("data_type", ""),
                     "pk": c.get("is_pk", False), "fk": c.get("is_fk", False)}
                    for c in t.get("columns", [])
                ],
            }
            for t in schema_summary.get("tables", [])
        ],
        "relationships": schema_summary.get("relationships", []),
        "detected_patterns": schema_summary.get("detected_patterns", {}),
    }
    parts.append(f"## Existing Schema Context\n```json\n{json.dumps(schema_compact, indent=2)}\n```\n")

    instructions = get_user_prompt("discover_sm_from_warehouse")
    instructions = instructions.replace("{mdx_reference}", get_mdx_prompt_summary())
    instructions = instructions.replace("{knowledge_base}", get_knowledge_base_summary())
    parts.append(instructions)

    return "\n".join(parts)


def _parse_candidate(raw: str) -> dict[str, Any] | None:
    """Try to parse *raw* as a top-level JSON object using the repair pipeline.

    Stages: direct parse → trailing-comma removal → control-char/escape
    sanitization → truncated-brace close → trim-to-last-closing-brace.

    Returns the parsed dict, or ``None`` when no stage yields a JSON object.
    """
    import re as _re

    def _parse_object(s: str) -> dict[str, Any] | None:
        """Parse *s* and return it only if it is a JSON object."""
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    # Stage 1: direct parse
    parsed = _parse_object(raw)
    if parsed is not None:
        return parsed

    # Stage 2: remove trailing commas (common LLM mistake)
    # Trailing commas in objects: ,} or ,\s*}
    # Trailing commas in arrays: ,] or ,\s*]
    cleaned = _re.sub(r",\s*([}\]])", r"\1", raw)
    parsed = _parse_object(cleaned)
    if parsed is not None:
        return parsed

    # Stage 3: sanitize stray control characters / invalid escapes in strings
    sanitized_chars: list[str] = []
    in_string = False
    escaped = False
    for index, char in enumerate(cleaned):
        if in_string and ord(char) < 0x20:
            sanitized_chars.append(json.dumps(char)[1:-1])
            escaped = False
            continue
        if in_string and char == "\\" and not escaped:
            next_char = cleaned[index + 1] if index + 1 < len(cleaned) else ""
            if next_char not in '"\\/bfnrtu':
                sanitized_chars.append("\\")
        sanitized_chars.append(char)
        if char == '"' and not escaped:
            in_string = not in_string
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    sanitized = "".join(sanitized_chars)
    parsed = _parse_object(sanitized)
    if parsed is not None:
        return parsed

    # Stage 4: try to fix truncated JSON by closing open braces/brackets
    _open_braces = sanitized.count("{") - sanitized.count("}")
    _open_brackets = sanitized.count("[") - sanitized.count("]")
    if _open_braces > 0 or _open_brackets > 0:
        _fixed = sanitized
        # Remove any trailing incomplete key-value or string
        _fixed = _re.sub(r'[\s,]*"[^"]*"\s*:\s*$', "", _fixed)
        _fixed = _re.sub(r'[\s,]*"[^"]*"\s*$', "", _fixed)
        # Also remove trailing incomplete string values (unterminated quotes)
        _fixed = _re.sub(r'"[^"]*$', '', _fixed)
        # Remove trailing incomplete content after last complete value
        _fixed = _re.sub(r'[\s,]*$', '', _fixed)
        _fixed += "]" * max(_open_brackets, 0)
        _fixed += "}" * max(_open_braces, 0)
        parsed = _parse_object(_fixed)
        if parsed is not None:
            return parsed

    # Stage 5: find the last valid JSON object by trimming from the end.
    # Instead of trying every position (O(n²)), only try positions of closing braces.
    brace_positions = [i for i, ch in enumerate(cleaned) if ch == "}"]
    for trim_pos in reversed(brace_positions[-20:]):
        _candidate = cleaned[:trim_pos + 1]
        _candidate = _re.sub(r",\s*([}\]])", r"\1", _candidate)
        parsed = _parse_object(_candidate)
        if parsed is not None:
            return parsed

    return None


def _extract_json_from_response(text: str) -> dict[str, Any]:
    """Extract JSON from an LLM response that may contain markdown code fences.

    Handles common LLM JSON issues:
    - Markdown code fences (```json ... ``` or ``` ... ```)
    - Trailing commas (common LLM mistake)
    - Truncated responses (attempts to close braces)
    - Multiple code fence blocks

    When several fenced blocks are present, every block is parsed through the
    repair pipeline and the best candidate is selected: prefer the first block
    with a non-empty ``recommended_sms`` list, else the first containing any
    of ``tables``/``relationships``/``identified_domain``, else the largest
    parsed object.

    Raises:
        ValueError: If the parsed JSON is not a top-level object (dict).
    """
    import re as _re

    # Collect every fenced block (```json ... ``` or ``` ... ```).
    fence_re = _re.compile(r"```(?:json)?\s*(.*?)```", _re.DOTALL)
    matches = list(fence_re.finditer(text))
    candidates: list[str] = [m.group(1).strip() for m in matches]

    # A trailing fence that was never closed — take everything after it.
    rest = text[matches[-1].end():] if matches else text
    unclosed = rest.find("```")
    if unclosed != -1:
        tail_body = _re.sub(r"^```(?:json)?\s*", "", rest[unclosed:]).strip()
        if tail_body:
            candidates.append(tail_body)

    # No fences at all — try parsing the whole text as JSON.
    if not candidates:
        candidates = [text.strip()]

    parsed_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        parsed = _parse_candidate(candidate)
        if parsed is not None:
            parsed_candidates.append(parsed)

    if not parsed_candidates:
        json_str = candidates[0]
        raise json.JSONDecodeError(
            f"Failed to parse a top-level JSON object after cleanup attempts. "
            f"(Arrays/lists are not accepted — the response must be a JSON object with a 'recommended_sms' key.) "
            f"First 200 chars: {json_str[:200]}... "
            f"Last 200 chars: ...{json_str[-200:]}",
            json_str,
            0,
        )

    # Prefer the block that carries the actual SM recommendation — the LLM
    # sometimes emits smaller helper objects (glossaries, notes) in earlier
    # fenced blocks before the real one.
    for parsed in parsed_candidates:
        if isinstance(parsed.get("recommended_sms"), list) and parsed["recommended_sms"]:
            return parsed
    for parsed in parsed_candidates:
        if any(k in parsed for k in ("tables", "relationships", "identified_domain")):
            return parsed
    return max(parsed_candidates, key=lambda d: len(json.dumps(d, default=str)))


# Transient errors that justify an automatic retry.
_TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    ConnectionResetError,
    TimeoutError,
)

# Max retries and base delay (seconds) for transient LLM connection failures.
_LLM_MAX_RETRIES = 3
_LLM_RETRY_BASE_DELAY = 2.0


def _is_transient(exc: Exception) -> bool:
    """Return True if *exc* looks like a transient network / protocol error."""
    # Catch the built-in connection family first.
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True
    # httpx / httpcore surface RemoteProtocolError for incomplete chunked reads.
    exc_name = type(exc).__name__
    if exc_name in ("RemoteProtocolError", "ReadError", "ConnectError"):
        return True
    # Anthropic wraps httpx errors in its own APIConnectionError.
    if exc_name in ("APIConnectionError", "APITimeoutError"):
        return True
    return False


def _call_anthropic(
    system_prompt: str,
    user_message: str,
    api_key: str,
    model: str,
    max_tokens: int,
) -> str:
    """Call Anthropic API and return response text.

    Supports a custom base URL via the ANTHROPIC_BASE_URL env var,
    which enables Azure AI Services endpoints that proxy Anthropic models.

    Uses streaming to avoid the 10-minute non-streaming timeout for large
    max_tokens values.

    Retries up to ``_LLM_MAX_RETRIES`` times on transient connection errors
    (e.g. RemoteProtocolError, ConnectionReset) with exponential back-off.
    """
    _ensure_anthropic()
    import anthropic

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)

    last_exc: Exception | None = None
    for attempt in range(_LLM_MAX_RETRIES + 1):
        try:
            response_text = ""
            stop_reason = None
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                for text in stream.text_stream:
                    response_text += text
                stop_reason = stream.get_final_message().stop_reason

            if stop_reason == "max_tokens":
                print(f"  WARNING: LLM response truncated (stop_reason=max_tokens, max_tokens={max_tokens}). "
                      f"Response may be incomplete — consider increasing max_tokens or simplifying the intent.")

            return response_text
        except Exception as exc:
            if _is_transient(exc) and attempt < _LLM_MAX_RETRIES:
                delay = _LLM_RETRY_BASE_DELAY * (2 ** attempt)
                print(
                    f"  Transient LLM error (attempt {attempt + 1}/{_LLM_MAX_RETRIES + 1}): "
                    f"{type(exc).__name__}: {exc}"
                )
                print(f"  Retrying in {delay:.0f}s...")
                time.sleep(delay)
                last_exc = exc
                continue
            raise

    # Should not reach here, but just in case all retries exhausted.
    assert last_exc is not None
    raise last_exc


def _call_azure_openai(
    system_prompt: str,
    user_message: str,
    api_key: str,
    endpoint: str,
    deployment_name: str,
    api_version: str,
    max_tokens: int,
) -> str:
    """Call Azure OpenAI API and return response text.

    Retries up to ``_LLM_MAX_RETRIES`` times on transient connection errors
    with exponential back-off.
    """
    _ensure_openai()
    from openai import AzureOpenAI

    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )

    last_exc: Exception | None = None
    for attempt in range(_LLM_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=deployment_name,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )

            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                print(f"  WARNING: LLM response truncated (finish_reason=length, max_tokens={max_tokens}). "
                      f"Response may be incomplete — consider increasing max_tokens or simplifying the intent.")

            return response.choices[0].message.content or ""
        except Exception as exc:
            if _is_transient(exc) and attempt < _LLM_MAX_RETRIES:
                delay = _LLM_RETRY_BASE_DELAY * (2 ** attempt)
                print(
                    f"  Transient LLM error (attempt {attempt + 1}/{_LLM_MAX_RETRIES + 1}): "
                    f"{type(exc).__name__}: {exc}"
                )
                print(f"  Retrying in {delay:.0f}s...")
                time.sleep(delay)
                last_exc = exc
                continue
            raise

    assert last_exc is not None
    raise last_exc


def design_sm_from_schema(
    schema_summary: dict[str, Any],
    user_intent: str,
    domain: str | None = None,
    allow_web_research: bool = True,
    sm_hints: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    llm_provider: str | None = None,
    trace_path: str | None = None,
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
        - ``AZURE_OPENAI_ENDPOINT`` or ``AZURE_ENDPOINT`` env var
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
        trace_path: Optional path to a trace file to append the SM design prompts
            and response to.

    Returns:
        SM recommendation dict matching the skill's output schema.

    Raises:
        ImportError: If required SDK is not installed.
        ValueError: If API key is missing or response cannot be parsed as JSON.
    """
    # Resolve provider
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()

    # Resolve model from env var or use default
    if model is None:
        model = os.environ.get("LLM_MODEL", "") or os.environ.get("ANTHROPIC_MODEL", "")
        if not model:
            model = "claude-sonnet-4-20250514"

    # Resolve max_tokens from env var or use default
    if max_tokens is None:
        env_max = os.environ.get("LLM_MAX_TOKENS", "")
        max_tokens = int(env_max) if env_max else 32768

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

    from kyvos_sm_skills.pipeline_tracer import get_tracer
    tracer = get_tracer()
    if tracer:
        tracer.step(
            "SM Design",
            f"Provider: {provider}  Model: {model}  "
            f"Schema: {schema_summary.get('schema')}  "
            f"Tables: {schema_summary.get('table_count')}  "
            f"Intent: {user_intent[:120]}"
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
        deployment = (
            model if model != "claude-sonnet-4-20250514"
            else os.environ.get("AZURE_DEPLOYMENT_NAME", "gpt-4.1")
        )
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
        if tracer:
            tracer.llm_exchange(
                f"SM Design attempt {attempt + 1}",
                system_prompt=system_prompt if attempt == 0 else "",
                user_message=current_message,
            )
        response_text = _call_llm(current_message)

        if tracer:
            tracer.note(
                f"LLM raw response (attempt {attempt + 1})",
                response_text[:50_000],
            )

        # Parse JSON from response
        try:
            recommendation = _extract_json_from_response(response_text)
        except (json.JSONDecodeError, ValueError) as exc:
            if tracer:
                tracer.error(
                    f"JSON parse error (attempt {attempt + 1})",
                    str(exc),
                )
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

        # Auto-repair common hierarchy issues before validation.
        hierarchy_repairs = repair_sm_hierarchies(recommendation, schema_summary)
        if hierarchy_repairs:
            print(f"  Auto-repaired {len(hierarchy_repairs)} hierarchy issue(s):")
            for note in hierarchy_repairs:
                print(f"    - {note}")
            if tracer:
                tracer.note(
                    f"Hierarchy auto-repair (attempt {attempt + 1})",
                    "\n".join(hierarchy_repairs),
                )

        # Auto-remove fact-to-fact relationships before validation.
        rel_repairs = repair_sm_relationships(recommendation, schema_summary)
        if rel_repairs:
            print(f"  Auto-repaired {len(rel_repairs)} relationship issue(s):")
            for note in rel_repairs:
                print(f"    - {note}")
            if tracer:
                tracer.note(
                    f"Relationship auto-repair (attempt {attempt + 1})",
                    "\n".join(rel_repairs),
                )

        # Validate against schema
        errors = validate_sm_recommendation(recommendation, schema_summary)
        if not errors:
            if tracer:
                tracer.json_dump(
                    f"SM design parsed recommendation (attempt {attempt + 1})",
                    recommendation,
                )
            if trace_path:
                _append_sm_design_trace(
                    trace_path=trace_path,
                    system_prompt=system_prompt,
                    user_message=current_message,
                    response_text=response_text,
                    recommendation=recommendation,
                    provider=provider,
                    model=model,
                )
            return recommendation

        if tracer:
            tracer.note(
                f"Validation errors (attempt {attempt + 1})",
                f"{len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors),
            )

        if attempt < max_retries:
            print(f"  LLM attempt {attempt + 1} had {len(errors)} validation error(s), retrying...")
            for err in errors:
                print(f"    - {err}")

            # Build detailed retry feedback with column/type hints for hierarchy
            # errors so the LLM can self-correct.
            retry_feedback = (
                f"\n\n## Validation Errors (Attempt {attempt + 1})\n"
                f"Your previous response had these validation errors against the warehouse schema:\n"
            )
            for err in errors:
                retry_feedback += f"- {err}\n"

            # Add column inventory for tables mentioned in hierarchy errors.
            hierarchy_err_tables: set[str] = set()
            for err in errors:
                if "hierarchy" in err.lower():
                    for t in schema_summary.get("tables", []):
                        if t["name"].lower() in err.lower():
                            hierarchy_err_tables.add(t["name"].lower())
            if hierarchy_err_tables:
                retry_feedback += "\n### Column reference for relevant tables\n"
                for tname in sorted(hierarchy_err_tables):
                    for t in schema_summary.get("tables", []):
                        if t["name"].lower() == tname:
                            col_info = ", ".join(
                                f"{c['name']} ({c.get('data_type', '?')})"
                                for c in t.get("columns", [])
                            )
                            retry_feedback += f"- **{t['name']}**: {col_info}\n"

            retry_feedback += (
                "\nPlease fix these errors and return the corrected JSON. "
                "Make sure all table names, column names, and source_dataset values "
                "exactly match the schema context provided above. "
                "For hierarchies: every level must be a column that exists in the "
                "hierarchy's source_dataset. "
                "CRITICAL: No fact-to-fact joins allowed. Two fact tables must NOT have "
                "a direct relationship. They connect through shared dimensions only. "
                "CRITICAL: Return exactly ONE SM in recommended_sms — one comprehensive model "
                "covering ALL tables from the warehouse schema. Do NOT split into multiple SMs."
            )
            current_message = user_message + retry_feedback
        else:
            print(f"  LLM produced {len(errors)} validation error(s) after {max_retries + 1} attempts.")
            for err in errors:
                print(f"    - {err}")
            raise ValueError(
                f"LLM-generated SM design has {len(errors)} validation error(s) "
                f"after {max_retries + 1} attempts. Last errors: {errors[:5]}"
            )

    # Capture final prompt/response/recommendation for the trace file before any
    # exception is raised so operators can diagnose failures.
    if trace_path:
        _append_sm_design_trace(
            trace_path=trace_path,
            system_prompt=system_prompt,
            user_message=current_message,
            response_text=response_text,
            recommendation=recommendation,
            provider=provider,
            model=model,
        )

    raise ValueError("LLM SM design exhausted all retries without producing a valid recommendation.")


def _append_sm_design_trace(
    trace_path: str,
    system_prompt: str,
    user_message: str,
    response_text: str,
    recommendation: dict[str, Any] | None,
    provider: str,
    model: str,
) -> None:
    """Append the SM design prompt/response to an existing trace file."""
    from pathlib import Path

    lines: list[str] = [
        "",
        "-" * 78,
        "  6. SM design system prompt sent to LLM",
        "-" * 78,
        system_prompt,
        "",
        "-" * 78,
        "  7. SM design user prompt sent to LLM",
        "-" * 78,
        user_message,
        "",
        "-" * 78,
        "  8. SM design raw response from LLM",
        "-" * 78,
        response_text,
        "",
        "-" * 78,
        "  9. SM design parsed recommendation",
        "-" * 78,
        json.dumps(recommendation if recommendation is not None else {}, indent=2, default=str),
        "",
    ]

    try:
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


def repair_sm_hierarchies(
    rec: dict[str, Any],
    schema_summary: dict[str, Any],
) -> list[str]:
    """Auto-repair common hierarchy issues in an SM recommendation.

    Repairs performed (in-place on *rec*):
    1. Relocate a hierarchy whose ``source_dataset`` is wrong but whose levels
       all exist in another SM table.
    2. Remove hierarchy levels whose column doesn't exist in the source table.
    3. Substitute numeric levels (INTEGER/FLOAT/…) with the nearest string
       equivalent on the same table (e.g. ``month`` → ``month_name``).
       Kyvos silently drops numeric hierarchy levels, producing empty hierarchies.
    4. Drop any standard (non-PC, non-alternate-path) hierarchy left with fewer
       than 2 levels after cleanup.

    Mixed data types across levels are intentionally allowed — callers should
    not enforce type homogeneity because mixed-type hierarchies (e.g. VARCHAR
    month_name + DATE full_date) are perfectly valid business constructs in Kyvos.

    Returns a list of human-readable repair notes (empty if nothing changed).
    """
    repairs: list[str] = []

    if not isinstance(rec, dict):
        raise ValueError(
            f"repair_sm_hierarchies expected a dict recommendation, got {type(rec).__name__}. "
            f"The LLM response must be a JSON object with a 'recommended_sms' key."
        )
    if not isinstance(schema_summary, dict):
        raise ValueError(
            f"repair_sm_hierarchies expected a dict schema_summary, got {type(schema_summary).__name__}."
        )

    # Build lookup structures from schema_summary.
    table_col_map: dict[str, set[str]] = {}
    table_col_type_map: dict[str, dict[str, str]] = {}
    # actual casing: {table_lower: {col_lower: col_actual_name}}
    table_col_actual_name: dict[str, dict[str, str]] = {}
    # foreign-key columns per table: {table_lower: {col_lower}}
    table_col_fk: dict[str, set[str]] = {}
    for t in schema_summary.get("tables", []):
        tname = t["name"].lower()
        cols_list = t.get("columns", [])
        table_col_map[tname] = {c["name"].lower() for c in cols_list}
        table_col_type_map[tname] = {
            c["name"].lower(): c.get("data_type", "")
            for c in cols_list
        }
        table_col_actual_name[tname] = {c["name"].lower(): c["name"] for c in cols_list}
        table_col_fk[tname] = {c["name"].lower() for c in cols_list if c.get("is_fk")}

    for sm in rec.get("recommended_sms", []):
        sm_tables_lower = {t.lower() for t in sm.get("tables", [])}
        repaired_hierarchies: list[dict[str, Any]] = []

        # Hierarchies referenced by calculated measures ([table].[hier]) are
        # exempt from the <2-level drop below — Kyvos accepts single-level
        # hierarchies and dropping a referenced one would break the measure.
        referenced_hiers: set[tuple[str, str]] = set()
        for m in sm.get("measures", []):
            if not m.get("is_calculated"):
                continue
            expr = m.get("expression", "")
            if not isinstance(expr, str):
                continue
            for _ref in re.finditer(
                r"\[\s*([^\]]+?)\s*\]\s*\.\s*\[\s*([^\]]+?)\s*\]",
                expr,
                re.IGNORECASE,
            ):
                referenced_hiers.add(
                    (_ref.group(1).strip().lower(), _ref.group(2).strip().lower())
                )

        for h in sm.get("hierarchies", []):
            h_name = h.get("name", "?")
            source = h.get("source_dataset", "")
            is_pc = h.get("is_parent_child", False)
            has_alt = h.get("has_alternate_path", False)
            if is_pc or not source:
                repaired_hierarchies.append(h)
                continue

            levels: list[str] = h.get("levels", [])
            source_lower = source.lower()
            cols = table_col_map.get(source_lower, set())
            col_types = table_col_type_map.get(source_lower, {})
            actual_names = table_col_actual_name.get(source_lower, {})

            # ── Repair 1: relocate if source_dataset is wrong ──────────
            if source_lower not in table_col_map:
                relocated = False
                for candidate in sm_tables_lower:
                    cand_cols = table_col_map.get(candidate, set())
                    if all(lv.lower() in cand_cols for lv in levels):
                        old_source = source
                        source = candidate
                        source_lower = candidate
                        h["source_dataset"] = candidate
                        cols = cand_cols
                        col_types = table_col_type_map.get(candidate, {})
                        actual_names = table_col_actual_name.get(candidate, {})
                        repairs.append(
                            f"Hierarchy '{h_name}': relocated source_dataset "
                            f"'{old_source}' -> '{candidate}'"
                        )
                        relocated = True
                        break
                if not relocated:
                    repaired_hierarchies.append(h)
                    continue

            # ── Repair 2: drop levels not in the source table / FK levels ──
            fk_cols = table_col_fk.get(source_lower, set())
            valid_levels: list[str] = []
            for lv in levels:
                if lv.lower() in cols:
                    if lv.lower() in fk_cols:
                        repairs.append(
                            f"Hierarchy '{h_name}': removed level '{lv}' "
                            f"(foreign-key column, not a drill-down level)"
                        )
                        continue
                    valid_levels.append(lv)
                else:
                    found_in = None
                    for candidate in sm_tables_lower:
                        if lv.lower() in table_col_map.get(candidate, set()):
                            found_in = candidate
                            break
                    repairs.append(
                        f"Hierarchy '{h_name}': removed level '{lv}' "
                        + (f"(exists in '{found_in}', not in '{source}')" if found_in
                           else f"(column not found in '{source}')")
                    )

            # ── Repair 3: drop standard hierarchy with < 2 levels ──────
            # Parent-child and alternate-path hierarchies are exempt, and so
            # are hierarchies referenced by a calculated measure — Kyvos
            # accepts single-level hierarchies, and dropping a referenced one
            # would break that measure. Hierarchies with 0 valid levels are
            # still dropped.
            if not has_alt and len(valid_levels) < 2:
                if valid_levels and (source_lower, h_name.lower()) in referenced_hiers:
                    h["levels"] = valid_levels
                    repaired_hierarchies.append(h)
                    repairs.append(
                        f"Hierarchy '{h_name}': kept with 1 level because it is "
                        f"referenced by a calculated measure"
                    )
                    continue
                repairs.append(
                    f"Hierarchy '{h_name}': dropped entirely "
                    f"(only {len(valid_levels)} valid level(s) remain; "
                    f"standard hierarchies need at least 2)"
                )
                continue

            h["levels"] = valid_levels
            repaired_hierarchies.append(h)

        sm["hierarchies"] = repaired_hierarchies

    return repairs


def repair_sm_relationships(
    rec: dict[str, Any],
    schema_summary: dict[str, Any],
) -> list[str]:
    """Auto-remove fact-to-fact relationships from the SM recommendation.

    Fact-to-fact joins are not allowed in Kyvos. This function removes any
    relationship where both endpoints are classified as fact tables.

    Returns a list of human-readable repair notes.
    """
    repairs: list[str] = []

    if not isinstance(rec, dict) or not isinstance(schema_summary, dict):
        return repairs

    # Build table type lookup from schema + LLM table_classifications
    table_type_map: dict[str, str] = {}
    for t in schema_summary.get("tables", []):
        table_type_map[t["name"].lower()] = t.get("estimated_table_type", "unknown")

    for sm in rec.get("recommended_sms", []):
        sm_name = sm.get("name", "?")
        # Overlay LLM-provided table_classifications if present
        for tname, ttype in sm.get("table_classifications", {}).items():
            table_type_map[tname.lower()] = ttype.lower()

        clean_rels: list[dict[str, Any]] = []
        for rel in sm.get("relationships", []):
            from_table = rel.get("from_table", "")
            to_table = rel.get("to_table", "")
            from_type = table_type_map.get(from_table.lower(), "unknown")
            to_type = table_type_map.get(to_table.lower(), "unknown")
            if from_type == "fact" and to_type == "fact":
                repairs.append(
                    f"SM '{sm_name}': removed fact-to-fact relationship "
                    f"{from_table} -> {to_table}"
                )
            else:
                clean_rels.append(rel)
        sm["relationships"] = clean_rels

    return repairs


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

    if not isinstance(rec, dict):
        errors.append(
            f"SM recommendation must be a JSON object with a 'recommended_sms' key, "
            f"got {type(rec).__name__}."
        )
        return errors
    if not isinstance(schema_summary, dict):
        errors.append(
            f"schema_summary must be a dict, got {type(schema_summary).__name__}."
        )
        return errors

    # Build set of available table names (case-insensitive)
    available_tables = {t["name"].lower() for t in schema_summary.get("tables", [])}
    table_col_map: dict[str, set[str]] = {}
    for t in schema_summary.get("tables", []):
        table_col_map[t["name"].lower()] = {c["name"].lower() for c in t.get("columns", [])}

    recommended_sms = rec.get("recommended_sms", [])
    if not recommended_sms:
        errors.append("No SMs in recommendation (recommended_sms is empty)")
        return errors

    # MANDATORY: Exactly one SM covering all tables — not multiple domain-specific SMs.
    if len(recommended_sms) > 1:
        errors.append(
            f"recommended_sms must contain exactly ONE semantic model, got {len(recommended_sms)}. "
            f"All tables must be consolidated into a single comprehensive SM."
        )
        return errors

    for i, sm in enumerate(recommended_sms):
        sm_name = sm.get("name", f"SM_{i}")
        sm_tables = sm.get("tables", [])
        sm_table_set = {t.lower() for t in sm_tables}

        # MANDATORY: Every warehouse table must be in the SM.
        missing = sorted(available_tables - sm_table_set)
        if missing:
            errors.append(
                f"SM '{sm_name}': {len(missing)} warehouse table(s) missing from the model: "
                f"{', '.join(missing)}. All tables must be included in the SM."
            )

        # Check tables exist
        for table_name in sm_tables:
            if table_name.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': table '{table_name}' not found in warehouse schema"
                )

        # Build table type lookup from schema + SM-provided classifications
        table_type_map: dict[str, str] = {}
        for t in schema_summary.get("tables", []):
            table_type_map[t["name"].lower()] = t.get("estimated_table_type", "unknown")
        # Overlay LLM-provided table_classifications if present
        for tname, ttype in sm.get("table_classifications", {}).items():
            table_type_map[tname.lower()] = ttype.lower()

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

            # Fact-to-fact join check
            from_type = table_type_map.get(from_table.lower(), "unknown")
            to_type = table_type_map.get(to_table.lower(), "unknown")
            if from_type == "fact" and to_type == "fact":
                errors.append(
                    f"SM '{sm_name}': fact-to-fact join detected between '{from_table}' and '{to_table}'. "
                    f"Fact tables must NOT have direct relationships — they connect through shared dimensions."
                )

        # Check measure source_dataset matches a table (skip for calculated measures)
        all_measure_names = {m.get("name", "").strip() for m in sm.get("measures", [])}
        for measure in sm.get("measures", []):
            is_calc = measure.get("is_calculated", False)
            source = measure.get("source_dataset", "")
            if source and not is_calc and source.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': measure '{measure.get('name', '?')}' "
                    f"source_dataset '{source}' not in warehouse"
                )

            # Calculated measures that reference other measures by display name are
            # allowed; the SDK compiler rewrites [Measures].[Name] references to
            # internal Kyvos measure IDs before deployment. Warn if a referenced
            # measure name does not exist in this SM.
            if is_calc:
                expr = measure.get("expression", "")
                if isinstance(expr, str):
                    refs = set(
                        match.group(1).strip()
                        for match in re.finditer(r"\[\s*Measures\s*\]\.\[\s*([^\]]+?)\s*\]", expr, re.IGNORECASE)
                    )
                    invalid = refs - set(all_measure_names)
                    if invalid:
                        errors.append(
                            f"SM '{sm_name}': calculated measure '{measure.get('name', '?')}' "
                            f"references unknown measure(s): {sorted(invalid)}. "
                            f"Only reference measures that exist in this semantic model."
                        )

        # Validate MDX dimension/hierarchy/level references in calculated measures.
        # Build a set of valid [dim].[hier].[level] paths from the SM design.
        valid_dim_hier_levels: set[tuple[str, str, str]] = set()
        valid_dim_hiers: set[tuple[str, str]] = set()
        # Fact/bridge tables are not dimensions in Kyvos — exclude them so
        # MDX references to them are checked consistently below.
        valid_dims: set[str] = {
            t for t in sm_table_set
            if table_type_map.get(t) not in ("fact", "bridge")
        }
        for h in sm.get("hierarchies", []):
            h_name = h.get("name", "").strip()
            source = h.get("source_dataset", "").strip()
            if not h_name or not source:
                continue
            if table_type_map.get(source.lower()) in ("fact", "bridge"):
                continue
            valid_dim_hiers.add((source.lower(), h_name.lower()))
            valid_dims.add(source.lower())
            for lvl in h.get("levels", []):
                if isinstance(lvl, str):
                    valid_dim_hier_levels.add((source.lower(), h_name.lower(), lvl.lower()))
            # Parent-child hierarchies expose the child column as a level
            child_col = h.get("child_column")
            if isinstance(child_col, str):
                valid_dim_hier_levels.add((source.lower(), h_name.lower(), child_col.lower()))

        _mdx_ref_re = re.compile(
            r"\[\s*([^\]]+?)\s*\]\s*\.\s*\[\s*([^\]]+?)\s*\](?:\s*\.\s*\[\s*([^\]]+?)\s*\])?",
            re.IGNORECASE,
        )
        seen: set[str] = set()
        for measure in sm.get("measures", []):
            if not measure.get("is_calculated"):
                continue
            expr = measure.get("expression", "")
            if not isinstance(expr, str):
                continue
            for match in _mdx_ref_re.finditer(expr):
                part1, part2, part3 = match.group(1).strip(), match.group(2).strip(), (match.group(3) or "").strip()
                # Skip measure references (already validated above)
                if part1.lower() == "measures":
                    continue
                # Fact/bridge tables are not dimensions — a [fact].[x] MDX
                # reference can never resolve, so name the real problem.
                if table_type_map.get(part1.lower()) in ("fact", "bridge"):
                    msg = (
                        f"SM '{sm_name}': calculated measure '{measure.get('name', '?')}' "
                        f"references '[{part1}]' which is a fact/bridge table. "
                        f"Fact columns are not dimension attributes in Kyvos and cannot be used "
                        f"in MDX member/hierarchy references (e.g. FILTER/MEMBERS/CurrentMember). "
                        f"Rewrite the measure using only dimension-table hierarchies, "
                        f"or drop this calculated measure."
                    )
                    if msg not in seen:
                        seen.add(msg)
                        errors.append(msg)
                    continue
                if part3:
                    if (part1.lower(), part2.lower(), part3.lower()) not in valid_dim_hier_levels:
                        msg = (
                            f"SM '{sm_name}': calculated measure '{measure.get('name', '?')}' "
                            f"references unknown level/member '{part3}' in hierarchy '{part2}' of dimension '{part1}'. "
                            f"Use actual column/level names that exist in the SM design."
                        )
                        if msg not in seen:
                            seen.add(msg)
                            errors.append(msg)
                else:
                    if (part1.lower(), part2.lower()) not in valid_dim_hiers:
                        msg = (
                            f"SM '{sm_name}': calculated measure '{measure.get('name', '?')}' "
                            f"references unknown hierarchy '{part2}' on dimension '{part1}'. "
                            f"Use hierarchy names defined in the SM design."
                        )
                        if msg not in seen:
                            seen.add(msg)
                            errors.append(msg)

        # Every fact table must have at least one measure — fact tables with no
        # measures get dropped by the connectivity sweep (they are not valid
        # BFS starting points and are unreachable from other facts).
        measure_sources = {
            m.get("source_dataset", "").lower()
            for m in sm.get("measures", [])
            if m.get("source_dataset")
        }
        for table_name in sm_table_set:
            ttype = table_type_map.get(table_name, "unknown")
            if ttype == "fact" and table_name not in measure_sources:
                errors.append(
                    f"SM '{sm_name}': fact table '{table_name}' has no measures. "
                    f"Every fact table must have at least one measure "
                    f"(e.g. a COUNT or SUM aggregation on a numeric column)."
                )

        # Check hierarchies are logically valid:
        # - source_dataset exists in the warehouse
        # - every level is an actual column on the source table
        # - all levels in a standard hierarchy share the same data-type family
        # - parent-child hierarchies have parent/child columns that share the same data-type family
        table_col_type_map: dict[str, dict[str, str]] = {}
        table_col_flags: dict[str, dict[str, tuple[bool, bool]]] = {}
        for t in schema_summary.get("tables", []):
            table_col_type_map[t["name"].lower()] = {
                c["name"].lower(): c.get("data_type", "")
                for c in t.get("columns", [])
            }
            table_col_flags[t["name"].lower()] = {
                c["name"].lower(): (bool(c.get("is_pk")), bool(c.get("is_fk")))
                for c in t.get("columns", [])
            }

        # Hierarchies referenced by calculated measures ([table].[hier]) are
        # exempt from the "at least 2 levels" rule — repair_sm_hierarchies
        # keeps them because Kyvos accepts single-level hierarchies and
        # dropping one would break the referencing measure.
        referenced_hiers: set[tuple[str, str]] = set()
        for measure in sm.get("measures", []):
            if not measure.get("is_calculated"):
                continue
            expr = measure.get("expression", "")
            if not isinstance(expr, str):
                continue
            for _ref in re.finditer(
                r"\[\s*([^\]]+?)\s*\]\s*\.\s*\[\s*([^\]]+?)\s*\]",
                expr,
                re.IGNORECASE,
            ):
                referenced_hiers.add(
                    (_ref.group(1).strip().lower(), _ref.group(2).strip().lower())
                )

        for h in sm.get("hierarchies", []):
            h_name = h.get("name", "?")
            source = h.get("source_dataset", "")
            is_pc = h.get("is_parent_child", False)
            parent_col = h.get("parent_column")
            child_col = h.get("child_column")

            if source and source.lower() not in available_tables:
                errors.append(
                    f"SM '{sm_name}': hierarchy '{h_name}' source_dataset '{source}' not in warehouse"
                )
                continue

            if not source:
                continue

            if table_type_map.get(source.lower()) in ("fact", "bridge"):
                errors.append(
                    f"SM '{sm_name}': hierarchy '{h_name}' is defined on fact/bridge table '{source}'. "
                    f"Fact and bridge tables are not dimensions in Kyvos and cannot carry hierarchies; "
                    f"define hierarchies only on dimension tables. "
                    f"If this attribute is needed for slicing, it must live in a dimension table."
                )
                continue

            col_types = table_col_type_map.get(source.lower(), {})
            col_flags = table_col_flags.get(source.lower(), {})
            has_alt = h.get("has_alternate_path", False)
            if not is_pc:
                levels = h.get("levels", [])
                existing_levels: list[str] = []
                for level in levels:
                    if level.lower() not in col_types:
                        errors.append(
                            f"SM '{sm_name}': hierarchy '{h_name}' level '{level}' not in table '{source}'"
                        )
                    else:
                        _lv_is_pk, lv_is_fk = col_flags.get(level.lower(), (False, False))
                        if lv_is_fk:
                            errors.append(
                                f"SM '{sm_name}': hierarchy '{h_name}' uses foreign-key column "
                                f"'{level}' as a level. Foreign keys reference other tables and are "
                                f"not business drill-down levels; remove it (it will be exposed "
                                f"as an attribute automatically)."
                            )
                            continue
                        existing_levels.append(level)

                # A primary key / leaf identifier may only be the last (leaf)
                # level — placing it above other levels is not a drill-down.
                for i, level in enumerate(existing_levels):
                    lv_is_pk, _lv_is_fk = col_flags.get(level.lower(), (False, False))
                    if lv_is_pk and i != len(existing_levels) - 1:
                        errors.append(
                            f"SM '{sm_name}': hierarchy '{h_name}' places primary-key column "
                            f"'{level}' above other levels. A key may only be the last (leaf) level."
                        )

                # Standard hierarchies need at least 2 levels — unless the
                # hierarchy is referenced by a calculated measure (Kyvos
                # accepts single-level hierarchies; repair keeps those).
                _kept_for_measure = (
                    len(existing_levels) >= 1
                    and (source.lower(), h_name.lower()) in referenced_hiers
                )
                if not has_alt and len(existing_levels) < 2 and not _kept_for_measure and not errors:
                    errors.append(
                        f"SM '{sm_name}': hierarchy '{h_name}' has only {len(existing_levels)} level(s). "
                        f"Standard hierarchies must have at least 2 levels."
                    )

            else:
                if parent_col and parent_col.lower() not in col_types:
                    errors.append(
                        f"SM '{sm_name}': hierarchy '{h_name}' parent_column '{parent_col}' not in table '{source}'"
                    )
                if child_col and child_col.lower() not in col_types:
                    errors.append(
                        f"SM '{sm_name}': hierarchy '{h_name}' child_column '{child_col}' not in table '{source}'"
                    )
                if (
                    parent_col
                    and child_col
                    and parent_col.lower() in col_types
                    and child_col.lower() in col_types
                    and _hierarchy_type_family(col_types[parent_col.lower()])
                    != _hierarchy_type_family(col_types[child_col.lower()])
                ):
                    errors.append(
                        f"SM '{sm_name}': hierarchy '{h_name}' parent_column '{parent_col}' "
                        f"and child_column '{child_col}' have different data types. "
                        f"Parent-child hierarchies require both columns to have the same data type."
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
    lines.append("\n  Domain Research Summary:")
    lines.append(f"  {rec.get('domain_research_summary', 'N/A')}")

    if rec.get("domain_reasoning"):
        lines.append("\n  Domain Reasoning:")
        lines.append(f"  {rec['domain_reasoning']}")

    # Gaps
    gaps = rec.get("gaps_identified", [])
    if gaps:
        lines.append("\n  Gaps Identified:")
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


def infer_schema_metadata(
    raw_metadata: dict[str, Any],
    connection_name: str,
    database_name: str,
    schema_name: str,
    llm_provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    trace_path: str | None = None,
) -> dict[str, Any]:
    """Infer PK/FK, table types, relationships, and patterns from Kyvos metadata via LLM.

    The Kyvos ``/rest/v2/connections/columns`` endpoint returns table and column
    metadata but does not expose primary/foreign keys or table classifications.
    This function asks the LLM to infer that structural information so the
    downstream ``discover-sm-from-warehouse`` flow can consume it as a normal
    ``schema_summary``.

    Args:
        raw_metadata: Dict with tables and their column lists from Kyvos APIs.
        connection_name: Kyvos connection name (for prompt context only).
        database_name: Database selected in Kyvos.
        schema_name: Schema selected in Kyvos.
        llm_provider: "anthropic" or "azure_openai". Defaults to ``LLM_PROVIDER`` env var.
        api_key: Optional API key; otherwise read from env vars.
        model: Optional model/deployment override.
        max_tokens: Optional token limit; defaults to 32768.
        trace_path: Optional path to write a debug trace file containing the raw
            metadata, LLM prompts, raw response, and parsed result.

    Returns:
        Dict matching the schema_summary table/relationship/pattern structure
        produced by ``inspect_schema`` in the SDK.
    """
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()

    if model is None:
        model = os.environ.get("LLM_MODEL", "") or os.environ.get("ANTHROPIC_MODEL", "")
        if not model:
            model = "claude-sonnet-4-20250514"

    if max_tokens is None:
        env_max = os.environ.get("LLM_MAX_TOKENS", "")
        max_tokens = int(env_max) if env_max else 32768

    system_prompt = get_system_prompt("infer_schema_from_kyvos_metadata")
    user_message = get_user_prompt("infer_schema_from_kyvos_metadata").format(
        connection_name=connection_name,
        database_name=database_name,
        schema_name=schema_name,
        raw_metadata=json.dumps(raw_metadata, indent=2),
    )

    from kyvos_sm_skills.pipeline_tracer import get_tracer
    tracer = get_tracer()
    if tracer:
        tracer.step(
            "Schema Inference",
            f"Provider: {provider}  Model: {model}  "
            f"Connection: {connection_name}  Database: {database_name}  "
            f"Schema: {schema_name}  Tables: {len(raw_metadata.get('tables', []))}"
        )

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
        deployment = (
            model if model != "claude-sonnet-4-20250514"
            else os.environ.get("AZURE_DEPLOYMENT_NAME", "gpt-4.1")
        )
        api_version = os.environ.get("AZURE_API_VERSION", "2024-12-01-preview")

        response_text = _call_azure_openai(
            system_prompt=system_prompt,
            user_message=user_message,
            api_key=resolved_key,
            endpoint=endpoint,
            deployment_name=deployment,
            api_version=api_version,
            max_tokens=max_tokens,
        )
    else:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY env var or pass api_key parameter."
            )
        response_text = _call_anthropic(
            system_prompt=system_prompt,
            user_message=user_message,
            api_key=resolved_key,
            model=model,
            max_tokens=max_tokens,
        )

    if tracer:
        tracer.llm_exchange(
            "Schema Inference",
            system_prompt=system_prompt,
            user_message=user_message,
            response=response_text,
        )

    if trace_path:
        # Write trace before parsing so the raw LLM response is captured even if
        # JSON extraction fails.
        try:
            _write_schema_inference_trace(
                trace_path=trace_path,
                raw_metadata=raw_metadata,
                system_prompt=system_prompt,
                user_message=user_message,
                response_text=response_text,
                result=None,
                provider=provider,
                model=model,
            )
        except OSError:
            pass

    try:
        result = _extract_json_from_response(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        if tracer:
            tracer.error("Schema inference JSON parse error", str(exc))
        raise

    if tracer:
        tracer.json_dump("Schema inference parsed result", result)

    if trace_path:
        # Overwrite the trace with the parsed result once available.
        try:
            _write_schema_inference_trace(
                trace_path=trace_path,
                raw_metadata=raw_metadata,
                system_prompt=system_prompt,
                user_message=user_message,
                response_text=response_text,
                result=result,
                provider=provider,
                model=model,
            )
        except OSError:
            pass

    return result


def _write_schema_inference_trace(
    trace_path: str,
    raw_metadata: dict[str, Any],
    system_prompt: str,
    user_message: str,
    response_text: str,
    result: dict[str, Any],
    provider: str,
    model: str,
) -> None:
    """Write a human-readable trace of the schema inference to disk.

    The trace contains the Kyvos API metadata, the LLM prompts, the raw
    LLM response, and the parsed recommendation so failures can be diagnosed
    without re-running the pipeline.
    """
    from pathlib import Path

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("  Kyvos Schema Discovery Trace")
    lines.append("=" * 78)
    lines.append(f"Provider: {provider}")
    lines.append(f"Model: {model}")
    lines.append("")

    lines.append("-" * 78)
    lines.append("  1. Raw metadata from Kyvos APIs")
    lines.append("-" * 78)
    lines.append(json.dumps(raw_metadata, indent=2, default=str))
    lines.append("")

    lines.append("-" * 78)
    lines.append("  2. System prompt sent to LLM")
    lines.append("-" * 78)
    lines.append(system_prompt)
    lines.append("")

    lines.append("-" * 78)
    lines.append("  3. User prompt sent to LLM")
    lines.append("-" * 78)
    lines.append(user_message)
    lines.append("")

    lines.append("-" * 78)
    lines.append("  4. Raw response from LLM")
    lines.append("-" * 78)
    lines.append(response_text)
    lines.append("")

    lines.append("-" * 78)
    lines.append("  5. Parsed LLM recommendation")
    lines.append("-" * 78)
    lines.append(json.dumps(result, indent=2, default=str))
    lines.append("")

    lines.append("=" * 78)
    lines.append("  End of trace")
    lines.append("=" * 78)

    try:
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        with open(trace_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        pass
