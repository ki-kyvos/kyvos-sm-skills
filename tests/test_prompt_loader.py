"""Tests for kyvos_sm_skills.prompt_loader — centralized prompt config loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyvos_sm_skills.prompt_loader import (
    get_system_prompt,
    get_user_prompt,
    list_system_prompt_keys,
    list_user_prompt_keys,
    reload,
)


class TestSystemPrompts:
    def test_discover_sm_from_warehouse_exists(self):
        prompt = get_system_prompt("discover_sm_from_warehouse")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_intent_generation_exists(self):
        prompt = get_system_prompt("intent_generation")
        assert isinstance(prompt, str)
        assert "Kyvos" in prompt
        assert "Business Context" in prompt

    def test_template_generation_exists(self):
        prompt = get_system_prompt("template_generation")
        assert isinstance(prompt, str)
        assert "data warehouse" in prompt.lower()

    def test_xmla_phase1_exists(self):
        prompt = get_system_prompt("xmla_template_agent_phase1")
        assert isinstance(prompt, str)
        assert "table_enrichments" in prompt

    def test_xmla_phase2_exists(self):
        prompt = get_system_prompt("xmla_template_agent_phase2")
        assert isinstance(prompt, str)
        assert "data_gen_spec" in prompt

    def test_invalid_key_raises(self):
        with pytest.raises(KeyError, match="nonexistent"):
            get_system_prompt("nonexistent")

    def test_list_keys(self):
        keys = list_system_prompt_keys()
        assert "discover_sm_from_warehouse" in keys
        assert "intent_generation" in keys


class TestUserPrompts:
    def test_discover_sm_from_warehouse_exists(self):
        prompt = get_user_prompt("discover_sm_from_warehouse")
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "{mdx_reference}" in prompt
        assert "{knowledge_base}" in prompt

    def test_intent_generation_exists(self):
        prompt = get_user_prompt("intent_generation")
        assert isinstance(prompt, str)
        assert "Business Context" in prompt
        assert "Fact Tables" in prompt

    def test_template_generation_exists(self):
        prompt = get_user_prompt("template_generation")
        assert isinstance(prompt, str)
        assert "Domain" in prompt

    def test_xmla_prompts_exist(self):
        for key in [
            "xmla_template_agent_phase1",
            "xmla_template_agent_phase2",
            "xmla_template_agent_xmla",
            "xmla_template_agent_xmla_lean",
        ]:
            prompt = get_user_prompt(key)
            assert isinstance(prompt, str)
            assert len(prompt) > 50

    def test_doc_template_agent_prompts_exist(self):
        for key in [
            "doc_template_agent_tables_only",
            "doc_template_agent_data_gen",
            "doc_template_agent_hierarchies_attributes",
            "doc_template_agent_relationships_measures",
            "doc_template_agent_semantic_model",
            "doc_template_agent",
        ]:
            prompt = get_user_prompt(key)
            assert isinstance(prompt, str)
            assert len(prompt) > 50

    def test_invalid_key_raises(self):
        with pytest.raises(KeyError, match="nonexistent"):
            get_user_prompt("nonexistent")

    def test_list_keys(self):
        keys = list_user_prompt_keys()
        assert "discover_sm_from_warehouse" in keys
        assert "intent_generation" in keys


class TestPromptConfigFiles:
    def test_system_prompts_json_exists(self):
        from kyvos_sm_skills.prompt_loader import _SYSTEM_PROMPTS_PATH

        assert _SYSTEM_PROMPTS_PATH.exists()

    def test_user_prompts_json_exists(self):
        from kyvos_sm_skills.prompt_loader import _USER_PROMPTS_PATH

        assert _USER_PROMPTS_PATH.exists()

    def test_system_prompts_are_valid_json(self):
        from kyvos_sm_skills.prompt_loader import _SYSTEM_PROMPTS_PATH

        with open(_SYSTEM_PROMPTS_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) >= 4

    def test_user_prompts_are_valid_json(self):
        from kyvos_sm_skills.prompt_loader import _USER_PROMPTS_PATH

        with open(_USER_PROMPTS_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) >= 10

    def test_reload_clears_cache(self):
        get_system_prompt("intent_generation")
        reload()
        get_system_prompt("intent_generation")
