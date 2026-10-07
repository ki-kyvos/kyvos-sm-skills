"""Role-playing (custom rollup) dimensions become one dataset per role."""

from __future__ import annotations

import json

import pytest

from kyvos_sm_skills.models import (
    ColumnSpec,
    DatasetSpec,
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)
from kyvos_sm_skills.role_playing import role_dataset_name, split_role_playing_dimensions

try:
    import kyvos_sdk  # noqa: F401
    _has_sdk = True
except ImportError:
    _has_sdk = False

_sdk_required = pytest.mark.skipif(not _has_sdk, reason="requires kyvos-sdk-python")


def _col(name: str, pk: bool = False) -> ColumnSpec:
    return ColumnSpec(name=name, data_type="INTEGER", is_primary_key=pk, nullable=not pk)


def _datasets(tables: list[TableSpec]) -> list[DatasetSpec]:
    # Mirrors spec_builder: one DatasetSpec per table, named after the table.
    return [
        DatasetSpec(name=t.name, source_table=t.name, connection_name="", columns=[c.name for c in t.columns])
        for t in tables
    ]


def _rel(left: str, lcol: str, right: str, rcol: str) -> RelationshipSpec:
    return RelationshipSpec(
        left_dataset=left, left_column=lcol, right_dataset=right, right_column=rcol,
        relationship_type="many_to_one",
    )


def _aw_tables() -> list[TableSpec]:
    date_cols = [_col("datekey", pk=True), _col("calendaryear"), _col("englishmonthname")]
    sales_cols = [_col("orderdatekey"), _col("duedatekey"), _col("shipdatekey"), _col("salesamount")]
    return [
        TableSpec(name="factinternetsales", schema_name="adventureworks", table_type="fact", columns=sales_cols),
        TableSpec(name="factresellersales", schema_name="adventureworks", table_type="fact", columns=sales_cols),
        TableSpec(name="factfinance", schema_name="adventureworks", table_type="fact",
                  columns=[_col("datekey"), _col("amount")]),
        TableSpec(name="dimdate", schema_name="adventureworks", table_type="dimension", columns=date_cols),
    ]


def _aw_rels() -> list[RelationshipSpec]:
    rels = []
    for fact in ("factinternetsales", "factresellersales"):
        for fk in ("orderdatekey", "duedatekey", "shipdatekey"):
            rels.append(_rel(fact, fk, "dimdate", "datekey"))
    rels.append(_rel("factfinance", "datekey", "dimdate", "datekey"))
    return rels


_CAL = HierarchySpec(name="Calendar", levels=["calendaryear", "englishmonthname"], source_dataset="dimdate")


class TestSplit:
    def test_creates_one_table_per_non_base_role(self):
        split = split_role_playing_dimensions(tables=_aw_tables(), relationships=_aw_rels(), hierarchies=[_CAL])
        assert split.changed
        assert split.source_table == {"dimdate_due": "dimdate", "dimdate_ship": "dimdate"}
        assert split.dataset_name == {"dimdate_due": "Dimdate_Due", "dimdate_ship": "Dimdate_Ship"}
        names = [t.name for t in split.tables]
        assert names.count("dimdate_due") == 1 and names.count("dimdate_ship") == 1
        clone = next(t for t in split.tables if t.name == "dimdate_ship")
        assert clone.schema_name == "adventureworks"
        assert [c.name for c in clone.columns] == ["datekey", "calendaryear", "englishmonthname"]

    def test_relationships_routed_to_role_tables_across_facts(self):
        split = split_role_playing_dimensions(tables=_aw_tables(), relationships=_aw_rels(), hierarchies=[_CAL])
        targets = {(r.left_dataset, r.left_column): r.right_dataset for r in split.relationships}
        for fact in ("factinternetsales", "factresellersales"):
            assert targets[(fact, "orderdatekey")] == "dimdate"
            assert targets[(fact, "duedatekey")] == "dimdate_due"
            assert targets[(fact, "shipdatekey")] == "dimdate_ship"
        # Single non role-playing join keeps the base dimension.
        assert targets[("factfinance", "datekey")] == "dimdate"

    def test_datasets_cloned_per_role(self):
        tables = _aw_tables()
        split = split_role_playing_dimensions(
            tables=tables, relationships=_aw_rels(), hierarchies=[_CAL], datasets=_datasets(tables),
        )
        clones = {d.name: d.source_table for d in split.datasets if d.name.startswith("dimdate_")}
        assert clones == {"dimdate_due": "dimdate", "dimdate_ship": "dimdate"}

    def test_hierarchies_cloned_per_role(self):
        split = split_role_playing_dimensions(tables=_aw_tables(), relationships=_aw_rels(), hierarchies=[_CAL])
        hier = {(h.name, h.source_dataset) for h in split.hierarchies}
        assert hier == {
            ("Calendar", "dimdate"),
            ("Calendar (Due)", "dimdate_due"),
            ("Calendar (Ship)", "dimdate_ship"),
        }

    def test_no_role_playing_is_noop(self):
        rels = [_rel("factfinance", "datekey", "dimdate", "datekey")]
        split = split_role_playing_dimensions(tables=_aw_tables(), relationships=rels, hierarchies=[_CAL])
        assert not split.changed
        assert split.relationships == rels

    def test_dataset_name_uses_kyvos_safe_characters(self):
        assert role_dataset_name("dimdate", "Ship") == "Dimdate_Ship"
        assert role_dataset_name("dim_date", "DueDate") == "DimDate_DueDate"


@_sdk_required
class TestCompile:
    def test_dataset_artifact_keeps_source_sql_with_role_name(self):
        from kyvos_sm_skills.contract_adapter import compile_dataset_artifact

        base = _aw_tables()[3]
        art = compile_dataset_artifact(base, connection_name="PSdatabricks", fmt="json", dataset_name="Dimdate_Ship")
        payload = json.loads(art.payload)
        assert payload["name"] == "Dimdate_Ship"
        assert payload["sql"] == "SELECT * FROM adventureworks.dimdate"

        xml_art = compile_dataset_artifact(base, connection_name="PSdatabricks", fmt="xml", dataset_name="Dimdate_Ship")
        plain = compile_dataset_artifact(base, connection_name="PSdatabricks", fmt="xml")
        assert 'NAME="Dimdate_Ship"' in xml_art.payload
        assert "SELECT * FROM adventureworks.dimdate" in xml_art.payload
        assert xml_art.payload.split('ID="')[1][:20] != plain.payload.split('ID="')[1][:20]

    def test_drd_and_smodel_use_separate_datasets(self):
        from kyvos_sm_skills.contract_adapter import build_drd_graph, compile_smodel_artifact
        from kyvos_sm_skills.generators.drd_xml import SimpleRel

        tables = _aw_tables()
        split = split_role_playing_dimensions(
            tables=tables, relationships=_aw_rels(), hierarchies=[_CAL], datasets=_datasets(tables),
        )
        aliases = {t.name: split.dataset_name.get(t.name, t.name.capitalize()) for t in split.tables}
        name_to_id = {v: f"id_{v.lower()}" for v in aliases.values()}
        facts = {aliases[t.name] for t in split.tables if t.table_type == "fact"}
        rels = [
            SimpleRel(
                left_dataset=r.left_dataset, left_column=r.left_column,
                right_dataset=r.right_dataset, right_column=r.right_column,
                relationship_type=r.relationship_type,
            )
            for r in split.relationships
        ]

        graph = build_drd_graph(
            drd_name="T", drd_id="d", dataset_name_to_id=name_to_id, relationships=rels,
            dataset_aliases=aliases, fact_dataset_names=facts,
        )
        date_nodes = [n for n in graph.nodes if n.dataset_ref.name.startswith("Dimdate")]
        # One node per dataset — no alias nodes sharing a dataset id.
        assert sorted(n.dataset_ref.name for n in date_nodes) == ["Dimdate", "Dimdate_Due", "Dimdate_Ship"]
        assert len({n.dataset_ref.id for n in date_nodes}) == 3
        assert all(n.alias == n.dataset_ref.name for n in date_nodes)

        cols = [{"name": c, "datatype": "INTEGER"} for c in ("datekey", "calendaryear", "englishmonthname")]
        fact_cols = [{"name": c, "datatype": "INTEGER"} for c in ("orderdatekey", "duedatekey", "shipdatekey", "salesamount", "datekey", "amount")]
        dataset_columns = {n: (cols if n.startswith("Dimdate") else fact_cols) for n in name_to_id}
        smodel = SemanticModelSpec(
            name="RP",
            datasets=split.datasets,
            relationships=split.relationships,
            hierarchies=split.hierarchies,
            measures=[MeasureSpec(name="Sales", expression="SUM(salesamount)", source_dataset="factinternetsales",
                                  source_column="salesamount")],
        )
        art = compile_smodel_artifact(
            smodel, drd_name="T", drd_id="d", folder_id="f", folder_name="F",
            connection_name="c", dataset_name_to_id=name_to_id, relationships=rels,
            dataset_aliases=aliases, fact_dataset_names=facts, dataset_columns=dataset_columns, fmt="json",
        )
        dims = json.loads(art.payload)["iro"]["specific"]["smObject"]["dimensions"]
        by_name = {d["name"]: d for d in dims if d.get("id") != "Dim_Measures"}
        assert {"Dimdate", "Dimdate_Due", "Dimdate_Ship"} <= set(by_name)
        assert [h["name"] for h in by_name["Dimdate_Ship"]["hierarchies"]] == ["Calendar (Ship)"]
        assert [h["name"] for h in by_name["Dimdate"]["hierarchies"]] == ["Calendar"]
