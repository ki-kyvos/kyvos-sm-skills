"""Tests for parent-child hierarchy support — spec builder, models, and LLM prompt."""

from __future__ import annotations

import json

import pytest

from kyvos_sm_skills.models import HierarchySpec
from kyvos_sm_skills.spec_builder import _build_hierarchies


# ── Test fixtures ──────────────────────────────────────────────────────────


def _make_warehouse_with_employee() -> list[dict]:
    """Warehouse schema with Employee and Organization tables for parent-child testing."""
    return [
        {
            "name": "dim_employee",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "employee_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "parent_employee_key", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "full_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "title", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_organization",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "organization_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "parent_organization_key", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "organization_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_account",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "account_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "parent_account_key", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "account_description", "data_type": "VARCHAR(500)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
        {
            "name": "dim_product",
            "schema": "public",
            "estimated_table_type": "dimension",
            "outgoing_fk_count": 0,
            "incoming_fk_count": 1,
            "columns": [
                {"name": "product_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                {"name": "product_name", "data_type": "VARCHAR(255)", "is_pk": False, "is_fk": False, "references": ""},
                {"name": "category", "data_type": "VARCHAR(100)", "is_pk": False, "is_fk": False, "references": ""},
            ],
        },
    ]


def _make_wh_table_map(tables: list[dict]) -> dict[str, dict]:
    """Convert warehouse tables to the map format used by _build_hierarchies."""
    return {t["name"].lower(): t for t in tables}


# ── Model tests ────────────────────────────────────────────────────────────


class TestHierarchySpecModel:
    def test_parent_child_fields_have_defaults(self):
        h = HierarchySpec(
            name="Employee PC",
            levels=[],
            is_parent_child=True,
            parent_column="parent_employee_key",
            child_column="employee_key",
        )
        assert h.root_member_type == "auto"
        assert h.non_leaf_data_member_visible is False
        assert h.non_leaf_data_member_caption == "self"
        assert h.display_column is None
        assert h.pc_level_naming_pattern == "Level_*"

    def test_parent_child_with_custom_fields(self):
        h = HierarchySpec(
            name="Employee PC",
            levels=[],
            is_parent_child=True,
            parent_column="parent_employee_key",
            child_column="employee_key",
            root_member_type="parent_is_blank",
            non_leaf_data_member_visible=True,
            non_leaf_data_member_caption="self",
            display_column="full_name",
            pc_level_naming_pattern="CEO,VP,Manager,Employee",
        )
        assert h.root_member_type == "parent_is_blank"
        assert h.non_leaf_data_member_visible is True
        assert h.display_column == "full_name"
        assert h.pc_level_naming_pattern == "CEO,VP,Manager,Employee"


# ── Spec builder tests ─────────────────────────────────────────────────────


class TestBuildHierarchiesParentChild:
    def test_parent_child_with_empty_levels(self):
        """Parent-child hierarchy with no levels should use child_column as the level."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Employee Hierarchy",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "parent_employee_key",
                "child_column": "employee_key",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 1
        h = result[0]
        assert h.is_parent_child is True
        assert h.parent_column == "parent_employee_key"
        assert h.child_column == "employee_key"
        assert h.levels == ["employee_key"]

    def test_parent_child_with_all_fields(self):
        """Parent-child hierarchy with all optional fields should pass through."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Employee Hierarchy",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "parent_employee_key",
                "child_column": "employee_key",
                "root_member_type": "parent_is_blank",
                "display_column": "full_name",
                "pc_level_naming_pattern": "CEO,VP,Manager,Employee",
                "non_leaf_data_member_visible": True,
                "non_leaf_data_member_caption": "self",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 1
        h = result[0]
        assert h.root_member_type == "parent_is_blank"
        assert h.display_column == "full_name"
        assert h.pc_level_naming_pattern == "CEO,VP,Manager,Employee"
        assert h.non_leaf_data_member_visible is True

    def test_parent_child_missing_columns_skipped(self):
        """Parent-child hierarchy with non-existent columns should be skipped."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Bad PC Hierarchy",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "nonexistent_parent_col",
                "child_column": "employee_key",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 0

    def test_parent_child_missing_both_columns_skipped(self):
        """Parent-child hierarchy with both columns missing should be skipped."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Bad PC Hierarchy",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "no_parent",
                "child_column": "no_child",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 0

    def test_parent_child_data_type_mismatch_skipped(self):
        """Parent-child hierarchy with mismatched data types should be skipped."""
        tables = [
            {
                "name": "dim_employee",
                "schema": "public",
                "estimated_table_type": "dimension",
                "outgoing_fk_count": 0,
                "incoming_fk_count": 1,
                "columns": [
                    {"name": "employee_key", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "parent_employee_key", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
        ]
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Type Mismatch PC",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "parent_employee_key",
                "child_column": "employee_key",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 0

    def test_parent_child_no_columns_skipped(self):
        """Parent-child hierarchy with no parent/child columns should be skipped."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "No Columns PC",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 0

    def test_parent_child_column_casing_normalized(self):
        """Parent-child columns should be normalized to actual warehouse casing."""
        tables = [
            {
                "name": "DimEmployee",
                "schema": "public",
                "estimated_table_type": "dimension",
                "outgoing_fk_count": 0,
                "incoming_fk_count": 1,
                "columns": [
                    {"name": "EmployeeKey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
                    {"name": "ParentEmployeeKey", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
                ],
            },
        ]
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Employee PC",
                "levels": [],
                "source_dataset": "DimEmployee",
                "is_parent_child": True,
                "parent_column": "parentemployeekey",
                "child_column": "employeekey",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 1
        h = result[0]
        assert h.parent_column == "ParentEmployeeKey"
        assert h.child_column == "EmployeeKey"

    def test_parent_child_display_column_validated(self):
        """Display column that doesn't exist should be cleared."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Employee PC",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "parent_employee_key",
                "child_column": "employee_key",
                "display_column": "nonexistent_column",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 1
        assert result[0].display_column is None

    def test_parent_child_display_column_valid(self):
        """Display column that exists should be preserved."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Employee PC",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "parent_employee_key",
                "child_column": "employee_key",
                "display_column": "full_name",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 1
        assert result[0].display_column == "full_name"

    def test_standard_hierarchy_still_works(self):
        """Standard (non-parent-child) hierarchies should still work as before."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Product Category",
                "levels": ["category", "product_key"],
                "source_dataset": "dim_product",
            }
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 1
        assert result[0].is_parent_child is False
        assert result[0].levels == ["category", "product_key"]

    def test_multiple_parent_child_hierarchies(self):
        """Multiple parent-child hierarchies on different tables should all work."""
        tables = _make_warehouse_with_employee()
        wh_map = _make_wh_table_map(tables)
        hierarchies = [
            {
                "name": "Employee Hierarchy",
                "levels": [],
                "source_dataset": "dim_employee",
                "is_parent_child": True,
                "parent_column": "parent_employee_key",
                "child_column": "employee_key",
            },
            {
                "name": "Organization Hierarchy",
                "levels": [],
                "source_dataset": "dim_organization",
                "is_parent_child": True,
                "parent_column": "parent_organization_key",
                "child_column": "organization_key",
            },
            {
                "name": "Account Hierarchy",
                "levels": [],
                "source_dataset": "dim_account",
                "is_parent_child": True,
                "parent_column": "parent_account_key",
                "child_column": "account_key",
            },
        ]
        result = _build_hierarchies(hierarchies, wh_map)
        assert len(result) == 3
        assert result[0].child_column == "employee_key"
        assert result[1].child_column == "organization_key"
        assert result[2].child_column == "account_key"


# ── LLM prompt tests ───────────────────────────────────────────────────────


class TestLLMPromptParentChild:
    def test_prompt_contains_parent_child_docs(self):
        """LLM prompt should include parent-child hierarchy documentation."""
        from kyvos_sm_skills.llm_designer import _build_user_message
        schema = {
            "warehouse_type": "postgresql",
            "schema": "public",
            "table_count": 1,
            "tables": [
                {
                    "name": "dim_employee",
                    "estimated_table_type": "dimension",
                    "columns": [
                        {"name": "employee_key", "data_type": "INTEGER", "is_pk": True},
                        {"name": "parent_employee_key", "data_type": "INTEGER"},
                    ],
                },
            ],
            "relationships": [],
            "detected_patterns": {},
        }
        msg = _build_user_message(schema, "test intent", "test_domain")
        assert "parent-child" in msg.lower()
        assert "is_parent_child" in msg
        assert "parent_column" in msg
        assert "child_column" in msg
        assert "root_member_type" in msg
        assert "display_column" in msg
        assert "pc_level_naming_pattern" in msg
        assert "non_leaf_data_member_visible" in msg

    def test_prompt_contains_kyvos_reference_url(self):
        """LLM prompt should include the Kyvos parent-child hierarchy documentation URL."""
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
        assert "1228748942" in msg  # Kyvos parent-child hierarchy page ID

    def test_prompt_contains_employee_example(self):
        """LLM prompt should include Employee parent-child example."""
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
        assert "parentemployeekey" in msg.lower()
        assert "employeekey" in msg.lower()
