"""Centralized prompt loader — all prompts driven by config files.

Loads system prompts from ``prompts/system_prompts.json`` and user prompt
templates from ``prompts/user_prompts.json``.  No prompt text should be
hardcoded in Python files; everything is read from these JSON config files.

Each entry in the JSON files supports:

* ``content`` — a string or array of strings (joined with ``\\n``)
* ``file``    — a relative path (from the package root) to a text file
* ``description`` — optional human-readable description

Usage::

    from kyvos_sm_skills.prompt_loader import get_system_prompt, get_user_prompt

    sys_prompt = get_system_prompt("discover_sm_from_warehouse")
    usr_prompt = get_user_prompt("intent_generation")
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PROMPTS_DIR = _PACKAGE_ROOT / "prompts"
_SYSTEM_PROMPTS_PATH = _PROMPTS_DIR / "system_prompts.json"
_USER_PROMPTS_PATH = _PROMPTS_DIR / "user_prompts.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt config file not found: {path}. "
            "Ensure kyvos-sm-skills is properly installed."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _system_prompts() -> dict:
    return _load_json(_SYSTEM_PROMPTS_PATH)


@lru_cache(maxsize=1)
def _user_prompts() -> dict:
    return _load_json(_USER_PROMPTS_PATH)


def _resolve_entry(entry: dict, key: str, kind: str) -> str:
    """Resolve a single prompt entry to its full text."""
    if "file" in entry:
        file_path = _PACKAGE_ROOT / entry["file"]
        if not file_path.exists():
            raise FileNotFoundError(
                f"Prompt file referenced in {kind} '{key}' not found: {file_path}"
            )
        return file_path.read_text(encoding="utf-8")

    content = entry.get("content")
    if content is None:
        raise KeyError(
            f"Prompt entry '{key}' in {kind} has neither 'content' nor 'file'"
        )

    if isinstance(content, list):
        return "\n".join(content)
    return str(content)


def get_system_prompt(key: str) -> str:
    """Return the full system prompt text for *key*.

    Reads from ``prompts/system_prompts.json``.  Supports ``content``
    (string or list of strings) or ``file`` (relative path to a text file).
    """
    prompts = _system_prompts()
    if key not in prompts:
        raise KeyError(
            f"System prompt '{key}' not found in {_SYSTEM_PROMPTS_PATH.name}. "
            f"Available keys: {list(prompts.keys())}"
        )
    return _resolve_entry(prompts[key], key, "system_prompts.json")


def get_user_prompt(key: str) -> str:
    """Return the full user prompt template text for *key*.

    Reads from ``prompts/user_prompts.json``.  Supports ``content``
    (string or list of strings) or ``file`` (relative path to a text file).
    """
    prompts = _user_prompts()
    if key not in prompts:
        raise KeyError(
            f"User prompt '{key}' not found in {_USER_PROMPTS_PATH.name}. "
            f"Available keys: {list(prompts.keys())}"
        )
    return _resolve_entry(prompts[key], key, "user_prompts.json")


def list_system_prompt_keys() -> list[str]:
    """Return all available system prompt keys."""
    return list(_system_prompts().keys())


def list_user_prompt_keys() -> list[str]:
    """Return all available user prompt keys."""
    return list(_user_prompts().keys())


def reload() -> None:
    """Clear the cached prompt data — useful for tests or hot-reloading."""
    _system_prompts.cache_clear()
    _user_prompts.cache_clear()
