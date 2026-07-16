"""Pydantic models for semantic model generation.

Subset of the domain models needed by the generators. These are intentionally
kept minimal and self-contained — no dependency on any external project.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnSpec(BaseModel):
    """Specification for a single column in the warehouse schema."""

    name: str
    data_type: str  # e.g. "INTEGER", "VARCHAR(100)", "NUMERIC(15,2)"
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None  # "schema.table.column"
    description: str = ""
    column_samples: list[str] = Field(default_factory=list)
    source_column: str | None = None
    display_folder: str = ""


class TableSpec(BaseModel):
    """Specification for a single table in the warehouse schema."""

    name: str
    schema_name: str = "public"
    table_type: str = "dimension"  # "fact" | "dimension" | "bridge" | "snowflake_dimension"
    columns: list[ColumnSpec] = Field(default_factory=list)
    description: str = ""
    row_count_target: int | None = None
    is_hidden: bool = False


class DatasetSpec(BaseModel):
    """Kyvos dataset definition."""

    name: str
    source_table: str
    connection_name: str
    columns: list[str] = Field(default_factory=list)


class RelationshipSpec(BaseModel):
    """Logical relationship between semantic datasets."""

    left_dataset: str
    left_column: str
    right_dataset: str
    right_column: str
    relationship_type: str = "many_to_one"
    semantic_role: str = "auto"
    active: bool = True
    description: str = ""


class MeasureSpec(BaseModel):
    """Semantic model measure / calculated metric."""

    name: str
    expression: str
    format_string: str = "#,##0"
    description: str = ""
    is_calculated: bool = False
    source_dataset: str | None = None
    aggregation_type: str = "sum"
    source_column: str | None = None
    is_hidden: bool = False


class HierarchySpec(BaseModel):
    """Dimension hierarchy for the semantic model."""

    name: str
    levels: list[str]
    source_dataset: str | None = None
    is_parent_child: bool = False
    has_alternate_path: bool = False
    parent_column: str | None = None
    child_column: str | None = None
    custom_rollup_weight_column: str | None = None
    pc_level_naming_pattern: str = "Level_*"


class SemanticModelSpec(BaseModel):
    """Full semantic model definition for Kyvos."""

    name: str
    datasets: list[DatasetSpec] = Field(default_factory=list)
    relationships: list[RelationshipSpec] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)
    hierarchies: list[HierarchySpec] = Field(default_factory=list)
