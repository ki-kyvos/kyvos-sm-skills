"""Tests for kyvos_sm_skills.knowledge_base — Kyvos documentation references."""

from __future__ import annotations

from kyvos_sm_skills.knowledge_base import (
    DocReference,
    KNOWLEDGE_BASE,
    get_knowledge_base_summary,
    get_knowledge_base_urls,
    get_mdx_reference,
    get_parent_child_reference,
    get_references_by_category,
)


class TestKnowledgeBaseCatalog:
    def test_catalog_not_empty(self):
        assert len(KNOWLEDGE_BASE) > 0

    def test_all_entries_have_required_fields(self):
        for ref in KNOWLEDGE_BASE:
            assert ref.title
            assert ref.url
            assert ref.category
            assert ref.summary
            assert isinstance(ref.key_concepts, list)

    def test_mdx_reference_exists(self):
        refs = get_references_by_category("mdx")
        assert len(refs) >= 1
        assert "MDX" in refs[0].title

    def test_hierarchy_references_exist(self):
        refs = get_references_by_category("hierarchy")
        assert len(refs) >= 1

    def test_all_urls_are_kyvos_docs(self):
        for ref in KNOWLEDGE_BASE:
            assert "kyvosinsights.com" in ref.url

    def test_categories_covered(self):
        categories = {ref.category for ref in KNOWLEDGE_BASE}
        assert "mdx" in categories
        assert "hierarchy" in categories


class TestGetKnowledgeBaseURLs:
    def test_returns_list_of_dicts(self):
        urls = get_knowledge_base_urls()
        assert isinstance(urls, list)
        assert len(urls) == len(KNOWLEDGE_BASE)

    def test_each_dict_has_required_keys(self):
        urls = get_knowledge_base_urls()
        for u in urls:
            assert "title" in u
            assert "url" in u
            assert "category" in u
            assert "summary" in u


class TestGetKnowledgeBaseSummary:
    def test_summary_is_non_empty_string(self):
        summary = get_knowledge_base_summary()
        assert isinstance(summary, str)
        assert len(summary) > 100

    def test_summary_contains_mdx_reference(self):
        summary = get_knowledge_base_summary()
        assert "MDX" in summary
        assert "1232535557" in summary  # MDX Functions Guide page ID

    def test_summary_contains_parent_child_reference(self):
        summary = get_knowledge_base_summary()
        assert "Parent-Child" in summary or "parent-child" in summary
        assert "1228748942" in summary  # Parent-child hierarchy page ID

    def test_summary_contains_key_concepts(self):
        summary = get_knowledge_base_summary()
        assert "SUM" in summary
        assert "YTD" in summary
        assert "ParallelPeriod" in summary
        assert "root member" in summary.lower() or "Root member" in summary

    def test_summary_contains_instructions(self):
        summary = get_knowledge_base_summary()
        assert "knowledge base" in summary.lower()
        assert "MDX" in summary
        assert "NOT DAX" in summary


class TestGetReferencesByCategory:
    def test_mdx_category(self):
        refs = get_references_by_category("mdx")
        assert all(r.category == "mdx" for r in refs)
        assert len(refs) >= 1

    def test_hierarchy_category(self):
        refs = get_references_by_category("hierarchy")
        assert all(r.category == "hierarchy" for r in refs)
        assert len(refs) >= 1

    def test_unknown_category_returns_empty(self):
        refs = get_references_by_category("nonexistent")
        assert refs == []


class TestGetMdxReference:
    def test_returns_mdx_doc(self):
        ref = get_mdx_reference()
        assert ref is not None
        assert ref.category == "mdx"
        assert "MDX" in ref.title

    def test_url_is_correct(self):
        ref = get_mdx_reference()
        assert ref is not None
        assert "1232535557" in ref.url


class TestGetParentChildReference:
    def test_returns_parent_child_doc(self):
        ref = get_parent_child_reference()
        assert ref is not None
        assert "parent" in ref.title.lower()
        assert "child" in ref.title.lower()

    def test_url_is_correct(self):
        ref = get_parent_child_reference()
        assert ref is not None
        assert "1228748942" in ref.url

    def test_key_concepts_include_root_member(self):
        ref = get_parent_child_reference()
        assert ref is not None
        concepts_text = " ".join(ref.key_concepts)
        assert "root" in concepts_text.lower()

    def test_key_concepts_include_data_type_requirement(self):
        ref = get_parent_child_reference()
        assert ref is not None
        concepts_text = " ".join(ref.key_concepts)
        assert "data type" in concepts_text.lower()


class TestLLMPromptIntegration:
    def test_knowledge_base_summary_in_llm_prompt(self):
        """Verify the knowledge base summary is included in the LLM prompt."""
        from kyvos_sm_skills.llm_designer import _build_user_message
        schema = {
            "warehouse_type": "postgresql",
            "schema": "public",
            "table_count": 1,
            "tables": [],
            "relationships": [],
            "detected_patterns": {},
        }
        msg = _build_user_message(schema, "test intent", "test_domain")
        assert "Knowledge Base" in msg
        assert "Kyvos Official Documentation" in msg
        assert "1232535557" in msg  # MDX guide page ID
        assert "1228748942" in msg  # Parent-child hierarchy page ID
