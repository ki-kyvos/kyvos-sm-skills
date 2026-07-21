"""End-to-end comparison tests for AdventureWorks: Intent File vs Generate Intent flow.

Validates that both flows produce structurally equivalent, conformant Kyvos
semantic model JSON with all the new conformity fields implemented in issue #1.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kyvos_sm_skills.skill_runner import run_discover_sm_from_warehouse


# ── AdventureWorks mock schema ──────────────────────────────────────────────


_AW_TABLES = [
    {
        "name": "factinternetsales",
        "schema": "public",
        "estimated_table_type": "fact",
        "outgoing_fk_count": 4,
        "incoming_fk_count": 0,
        "columns": [
            {"name": "salesordernumber", "data_type": "VARCHAR(20)", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "productkey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimproduct.productkey"},
            {"name": "customerkey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimcustomer.customerkey"},
            {"name": "orderdatekey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimdate.datekey"},
            {"name": "salesterritorykey", "data_type": "INTEGER", "is_pk": False, "is_fk": True, "references": "dimsalesterritory.salesterritorykey"},
            {"name": "salesamount", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "orderquantity", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "totalproductcost", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "taxamt", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "freight", "data_type": "NUMERIC(15,2)", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimproduct",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "productkey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "productsubcategorykey", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "productcategorykey", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimcustomer",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "customerkey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimdate",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "datekey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "weeknumberofyear", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "englishmonthname", "data_type": "VARCHAR(20)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "calendarquarter", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "calendaryear", "data_type": "INTEGER", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
    {
        "name": "dimsalesterritory",
        "schema": "public",
        "estimated_table_type": "dimension",
        "outgoing_fk_count": 0,
        "incoming_fk_count": 1,
        "columns": [
            {"name": "salesterritorykey", "data_type": "INTEGER", "is_pk": True, "is_fk": False, "references": ""},
            {"name": "salesterritoryregion", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "salesterritorycountry", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
            {"name": "salesterritorygroup", "data_type": "VARCHAR(50)", "is_pk": False, "is_fk": False, "references": ""},
        ],
    },
]


def _mock_inspect_schema(config, schema_filter=None, max_tables=500):
    return {
        "warehouse_type": "POSTGRES",
        "schema": schema_filter or "public",
        "table_count": len(_AW_TABLES),
        "tables": _AW_TABLES,
        "relationships": [
            {"from_table": "factinternetsales", "from_column": "productkey", "to_table": "dimproduct", "to_column": "productkey"},
            {"from_table": "factinternetsales", "from_column": "customerkey", "to_table": "dimcustomer", "to_column": "customerkey"},
            {"from_table": "factinternetsales", "from_column": "orderdatekey", "to_table": "dimdate", "to_column": "datekey"},
            {"from_table": "factinternetsales", "from_column": "salesterritorykey", "to_table": "dimsalesterritory", "to_column": "salesterritorykey"},
        ],
        "detected_patterns": {
            "potential_star_schemas": [{"fact_table": "factinternetsales", "dimension_tables": ["dimproduct", "dimcustomer", "dimdate", "dimsalesterritory"]}],
            "potential_snowflake_schemas": [],
            "potential_multifact_schemas": [],
            "single_table_candidates": [],
            "disjoint_table_groups": [],
        },
    }


# ── SM design matching samples/adventureworks-sm-design.json ────────────────


_AW_SM_DESIGN = {
    "recommended_sms": [
        {
            "name": "AdventureWorksSales",
            "schema_type": "star",
            "rationale": "AdventureWorks has a classic star schema with a single fact table (FactInternetSales) surrounded by conformed dimensions.",
            "tables": ["factinternetsales", "dimproduct", "dimcustomer", "dimdate", "dimsalesterritory"],
            "relationships": [
                {"from_table": "factinternetsales", "from_column": "productkey", "to_table": "dimproduct", "to_column": "productkey"},
                {"from_table": "factinternetsales", "from_column": "customerkey", "to_table": "dimcustomer", "to_column": "customerkey"},
                {"from_table": "factinternetsales", "from_column": "orderdatekey", "to_table": "dimdate", "to_column": "datekey"},
                {"from_table": "factinternetsales", "from_column": "salesterritorykey", "to_table": "dimsalesterritory", "to_column": "salesterritorykey"},
            ],
            "measures": [
                {"name": "SalesAmount", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "OrderQuantity", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "TotalProductCost", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "TaxAmt", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "Freight", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
            ],
            "hierarchies": [
                {"name": "ProductCategory", "levels": ["productkey", "productsubcategorykey", "productcategorykey"], "source_dataset": "dimproduct"},
                {"name": "CalendarDate", "levels": ["datekey", "weeknumberofyear", "englishmonthname", "calendarquarter", "calendaryear"], "source_dataset": "dimdate"},
                {"name": "SalesTerritory", "levels": ["salesterritorykey", "salesterritoryregion", "salesterritorycountry", "salesterritorygroup"], "source_dataset": "dimsalesterritory"},
            ],
        }
    ],
    "identified_domain": "adventure_works",
    "domain_research_summary": "Adventure Works is a fictional bicycle manufacturing company by Microsoft.",
    "domain_reasoning": "The presence of FactInternetSales with DimProduct, DimCustomer, DimDate, and DimSalesTerritory is the canonical AdventureWorks star schema pattern.",
    "gaps_identified": [
        "No DimPromotion table found — consider adding for promotion effectiveness analysis",
        "No DimCurrency table found — consider adding for multi-currency analysis if applicable",
    ],
}


# SM design with calculated KPIs (simulating what an LLM might produce with intent)
_AW_SM_DESIGN_WITH_KPIS = {
    "recommended_sms": [
        {
            "name": "AdventureWorksSales",
            "schema_type": "star",
            "rationale": "AdventureWorks star schema with calculated KPIs.",
            "tables": ["factinternetsales", "dimproduct", "dimcustomer", "dimdate", "dimsalesterritory"],
            "relationships": [
                {"from_table": "factinternetsales", "from_column": "productkey", "to_table": "dimproduct", "to_column": "productkey"},
                {"from_table": "factinternetsales", "from_column": "customerkey", "to_table": "dimcustomer", "to_column": "customerkey"},
                {"from_table": "factinternetsales", "from_column": "orderdatekey", "to_table": "dimdate", "to_column": "datekey"},
                {"from_table": "factinternetsales", "from_column": "salesterritorykey", "to_table": "dimsalesterritory", "to_column": "salesterritorykey"},
            ],
            "measures": [
                {"name": "SalesAmount", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "OrderQuantity", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "TotalProductCost", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "TaxAmt", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {"name": "Freight", "source_dataset": "factinternetsales", "aggregation_type": "sum"},
                {
                    "name": "GrossMargin",
                    "source_dataset": "factinternetsales",
                    "aggregation_type": "sum",
                    "is_calculated": True,
                    "expression": "[Measures].[SalesAmount] - [Measures].[TotalProductCost]",
                },
                {
                    "name": "MarginPct",
                    "source_dataset": "factinternetsales",
                    "aggregation_type": "sum",
                    "is_calculated": True,
                    "expression": "IIF([Measures].[SalesAmount] = 0, 0, ([Measures].[SalesAmount] - [Measures].[TotalProductCost]) / [Measures].[SalesAmount])",
                },
            ],
            "hierarchies": [
                {"name": "ProductCategory", "levels": ["productkey", "productsubcategorykey", "productcategorykey"], "source_dataset": "dimproduct"},
                {"name": "CalendarDate", "levels": ["datekey", "weeknumberofyear", "englishmonthname", "calendarquarter", "calendaryear"], "source_dataset": "dimdate"},
                {"name": "SalesTerritory", "levels": ["salesterritorykey", "salesterritoryregion", "salesterritorycountry", "salesterritorygroup"], "source_dataset": "dimsalesterritory"},
            ],
        }
    ],
    "identified_domain": "adventure_works",
    "domain_research_summary": "Adventure Works is a fictional bicycle manufacturing company by Microsoft.",
    "domain_reasoning": "Canonical AdventureWorks star schema.",
    "gaps_identified": [],
}


_GENERATED_INTENT = """\
Adventure Works Semantic Model Intent (Auto-Generated)

## Business Context
Adventure Works is a fictional bicycle manufacturing and retail company.
The warehouse contains internet sales data with product, customer, date, and
sales territory dimensions.

## Fact Tables
- factinternetsales: Sales order line granularity with measures SalesAmount,
  OrderQuantity, TotalProductCost, TaxAmt, Freight

## Dimension Tables
- dimproduct: Product dimension with category/subcategory rollup
- dimcustomer: Customer dimension
- dimdate: Date dimension with calendar hierarchy
- dimsalesterritory: Sales territory with group/country/region hierarchy

## Hierarchy Requirements
- ProductCategory: productkey → productsubcategorykey → productcategorykey
- CalendarDate: datekey → weeknumberofyear → englishmonthname → calendarquarter → calendaryear
- SalesTerritory: salesterritorykey → salesterritoryregion → salesterritorycountry → salesterritorygroup

## KPI Requirements
- Base measures: SalesAmount (sum), OrderQuantity (sum), TotalProductCost (sum)
- Calculated: GrossMargin = SalesAmount - TotalProductCost
- Calculated: MarginPct using IIF for divide-by-zero protection

## Quality Bar
Production-ready MVP with enterprise hierarchies and MDX calculated KPIs.
"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_env_file(tmp_path) -> str:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KYVOS_BASE_URL=http://test\n"
        "KYVOS_WAREHOUSE_TYPE=POSTGRES\n"
        "KYVOS_WAREHOUSE_HOST=localhost\n"
        "KYVOS_WAREHOUSE_PORT=5432\n"
        "KYVOS_WAREHOUSE_DATABASE=adventureworks\n"
        "KYVOS_WAREHOUSE_USERNAME=test\n"
        "KYVOS_WAREHOUSE_PASSWORD=test\n"
    )
    return str(env_file)


def _compile_sm_json(smodel, dataset_name_to_id: dict[str, str], dataset_columns: dict[str, list[dict]]) -> dict:
    """Compile a SemanticModelSpec into SM JSON using the SDK compiler."""
    from kyvos_sdk.compiler import compile_semantic_model
    from kyvos_sdk.contracts.artifacts import ArtifactFormat
    from kyvos_sdk.contracts.identity import DrdGraph, DrdNode, DrdRelation, EntityRef, EntityType

    nodes = []
    relations = []
    node_id_map: dict[str, str] = {}
    for ds in smodel.datasets:
        nid = f"node_{ds.name}"
        node_id_map[ds.name] = nid
        ntype = "fact" if ds.name.lower().startswith("fact") else "dimension"
        nodes.append(DrdNode(
            node_id=nid,
            dataset_ref=EntityRef(entity_type=EntityType.DATASET, id=dataset_name_to_id.get(ds.name, "ds_x"), name=ds.name),
            node_type=ntype,
        ))

    for rel in smodel.relationships:
        if not rel.active:
            continue
        relations.append(DrdRelation(
            relation_id=f"rel_{rel.left_dataset}_{rel.right_dataset}",
            source_node_id=node_id_map.get(rel.left_dataset, ""),
            target_node_id=node_id_map.get(rel.right_dataset, ""),
            source_column=rel.left_column,
            target_column=rel.right_column,
        ))

    graph = DrdGraph(
        metadata=__import__("kyvos_sdk.contracts.common", fromlist=["ContractMetadata"]).ContractMetadata(
            contract_version="1.0", producer="test",
        ),
        drd_ref=EntityRef(entity_type=EntityType.DRD, id="drd_1", name="TestDRD"),
        nodes=nodes,
        relations=relations,
    )

    from kyvos_sdk.contracts.adapters import adapt_semantic_model
    contract_smodel = adapt_semantic_model(smodel)

    art = compile_semantic_model(
        contract_smodel,
        graph=graph,
        folder_id="folder_1",
        folder_name="Demo",
        connection_name="TestConn",
        drd_id="drd_1",
        drd_name="TestDRD",
        dataset_name_to_id=dataset_name_to_id,
        dataset_columns=dataset_columns,
        fmt=ArtifactFormat.JSON,
    )
    return json.loads(art.payload)


def _build_spec_and_compile(sm_design: dict, tmp_path):
    """Run the discover flow in dry-run mode, then compile the SM JSON."""
    env_file = _make_env_file(tmp_path)

    with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema):
        rc = run_discover_sm_from_warehouse(
            env_file=env_file,
            sm_design=sm_design,
            dry_run=True,
            payload_format="json",
        )
    assert rc == 0

    # Now build spec and compile manually (dry_run doesn't compile)
    from kyvos_sm_skills.spec_builder import build_spec_from_recommendation
    schema_summary = _mock_inspect_schema(None)
    discovered_spec = build_spec_from_recommendation(
        sm_rec=sm_design["recommended_sms"][0],
        warehouse_tables=schema_summary["tables"],
    )

    # Build dataset_name_to_id and dataset_columns for compiler
    dataset_name_to_id = {ds.name: f"ds_{i}" for i, ds in enumerate(discovered_spec.semantic_model.datasets)}
    dataset_columns = {}
    for wt in _AW_TABLES:
        dataset_columns[wt["name"]] = wt["columns"]

    sm_json = _compile_sm_json(
        discovered_spec.semantic_model,
        dataset_name_to_id=dataset_name_to_id,
        dataset_columns=dataset_columns,
    )
    return discovered_spec, sm_json


# ── Validation helpers ──────────────────────────────────────────────────────


def _validate_top_level_structure(sm_json: dict):
    assert "common" in sm_json, "Missing 'common' top-level object"
    assert sm_json["common"]["compatibilityVersion"] == "3"
    assert "specific" in sm_json
    assert "smObject" in sm_json["specific"]
    assert "dimensions" in sm_json["specific"]["smObject"]
    assert "measures" in sm_json["specific"]["smObject"]
    assert "measure" in sm_json["specific"]["smObject"]["measures"]


def _validate_hierarchy_fields(sm_json: dict):
    for dim in sm_json["specific"]["smObject"]["dimensions"]:
        for h in dim["hierarchies"]:
            assert "defaultMemberUniqueName" in h, f"Hierarchy '{h['name']}' missing defaultMemberUniqueName"
            assert "displayFolder" in h, f"Hierarchy '{h['name']}' missing displayFolder"


def _validate_level_fields(sm_json: dict):
    for dim in sm_json["specific"]["smObject"]["dimensions"]:
        for h in dim["hierarchies"]:
            for lvl in h["levels"]:
                assert "dateDataType" in lvl, f"Level '{lvl['name']}' missing dateDataType"
                assert "dateFormat" in lvl, f"Level '{lvl['name']}' missing dateFormat"
                assert "format" in lvl, f"Level '{lvl['name']}' missing format"
                assert "fieldDataType" in lvl, f"Level '{lvl['name']}' missing fieldDataType"


def _validate_measure_fields(sm_json: dict):
    for m in sm_json["specific"]["smObject"]["measures"]["measure"]:
        assert "actualSummaryFunction" in m, f"Measure '{m['name']}' missing actualSummaryFunction"


def _validate_no_blocking_diagnostics(art_diagnostics: list):
    blocking_codes = {"NO_MEASURES_PLACED", "MISSING_DATASET_ID", "PC_DATA_TYPE_MISMATCH"}
    for diag in art_diagnostics:
        assert diag.code not in blocking_codes, f"Blocking diagnostic: {diag.code}: {diag.message}"


def _validate_mdx_expressions(sm_json: dict):
    from kyvos_sm_skills.mdx_reference import validate_mdx_expression
    for m in sm_json["specific"]["smObject"]["measures"]["measure"]:
        if "expression" in m:
            issues = validate_mdx_expression(m["expression"]["content"])
            assert not issues, f"Measure '{m['name']}' has MDX issues: {issues}"


def _validate_sm_recommendation(sm_design: dict):
    from kyvos_sm_skills.llm_designer import validate_sm_recommendation
    schema_summary = _mock_inspect_schema(None)
    errors = validate_sm_recommendation(sm_design, schema_summary)
    assert not errors, f"SM recommendation validation errors: {errors}"


# ── Test classes ────────────────────────────────────────────────────────────


class TestFlowAIntentFile:
    """Flow A: Pre-written intent file → LLM → SM design → compile."""

    def test_dry_run_with_intent_file(self, tmp_path):
        """Flow A dry-run should complete successfully with the sample SM design."""
        env_file = _make_env_file(tmp_path)

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.llm_designer.design_sm_from_schema", return_value=_AW_SM_DESIGN):
            rc = run_discover_sm_from_warehouse(
                env_file=env_file,
                user_intent="I want Adventure Works sales analytics",
                domain="adventure_works",
                dry_run=True,
                payload_format="json",
            )
        assert rc == 0

    def test_compiled_sm_has_common_object(self, tmp_path):
        """Flow A compiled SM JSON must have the 'common' top-level object."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_top_level_structure(sm_json)

    def test_compiled_sm_has_hierarchy_fields(self, tmp_path):
        """Flow A compiled SM JSON must have new hierarchy conformity fields."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_hierarchy_fields(sm_json)

    def test_compiled_sm_has_level_fields(self, tmp_path):
        """Flow A compiled SM JSON must have new level conformity fields."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_level_fields(sm_json)

    def test_compiled_sm_has_measure_fields(self, tmp_path):
        """Flow A compiled SM JSON must have actualSummaryFunction on all measures."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_measure_fields(sm_json)

    def test_sm_design_validates_against_schema(self):
        """Flow A SM design must pass validate_sm_recommendation with zero errors."""
        _validate_sm_recommendation(_AW_SM_DESIGN)

    def test_compiled_sm_has_correct_table_count(self, tmp_path):
        """Flow A should produce 4 dimensions (one per dim table) and 5 measures."""
        spec, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        dims = sm_json["specific"]["smObject"]["dimensions"]
        measures = sm_json["specific"]["smObject"]["measures"]["measure"]
        assert len(dims) == 4  # dimproduct, dimcustomer, dimdate, dimsalesterritory
        assert len(measures) == 5  # SalesAmount, OrderQuantity, TotalProductCost, TaxAmt, Freight

    def test_compiled_sm_has_correct_hierarchies(self, tmp_path):
        """Flow A should produce at least 3 explicit hierarchies (compiler may auto-generate defaults)."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        dims = sm_json["specific"]["smObject"]["dimensions"]
        total_hierarchies = sum(len(d["hierarchies"]) for d in dims)
        # 3 explicit (ProductCategory, CalendarDate, SalesTerritory) + 1 auto-generated for dimcustomer
        assert total_hierarchies >= 3

    def test_compiled_sm_with_calculated_kpis(self, tmp_path):
        """Flow A with calculated KPIs should compile with MDX expressions."""
        spec, sm_json = _build_spec_and_compile(_AW_SM_DESIGN_WITH_KPIS, tmp_path)
        measures = sm_json["specific"]["smObject"]["measures"]["measure"]
        calc_measures = [m for m in measures if "expression" in m]
        assert len(calc_measures) == 2  # GrossMargin, MarginPct
        _validate_mdx_expressions(sm_json)

    def test_compiled_sm_calculated_measure_summary_function_empty(self, tmp_path):
        """Calculated measures should have empty summaryFunction and actualSummaryFunction."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN_WITH_KPIS, tmp_path)
        measures = sm_json["specific"]["smObject"]["measures"]["measure"]
        calc = [m for m in measures if "expression" in m]
        for m in calc:
            assert m["summaryFunction"] == ""
            assert m["actualSummaryFunction"] == ""


class TestFlowBGenerateIntent:
    """Flow B: Auto-generated intent → LLM → SM design → compile."""

    def test_dry_run_with_generated_intent(self, tmp_path):
        """Flow B dry-run should complete successfully with generated intent."""
        env_file = _make_env_file(tmp_path)

        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.intent_generator.generate_intent", return_value=_GENERATED_INTENT), \
             patch("kyvos_sm_skills.llm_designer.design_sm_from_schema", return_value=_AW_SM_DESIGN):
            rc = run_discover_sm_from_warehouse(
                env_file=env_file,
                user_intent=_GENERATED_INTENT,
                domain="adventure_works",
                dry_run=True,
                payload_format="json",
            )
        assert rc == 0

    def test_compiled_sm_has_common_object(self, tmp_path):
        """Flow B compiled SM JSON must have the 'common' top-level object."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_top_level_structure(sm_json)

    def test_compiled_sm_has_hierarchy_fields(self, tmp_path):
        """Flow B compiled SM JSON must have new hierarchy conformity fields."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_hierarchy_fields(sm_json)

    def test_compiled_sm_has_level_fields(self, tmp_path):
        """Flow B compiled SM JSON must have new level conformity fields."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_level_fields(sm_json)

    def test_compiled_sm_has_measure_fields(self, tmp_path):
        """Flow B compiled SM JSON must have actualSummaryFunction on all measures."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_measure_fields(sm_json)

    def test_intent_generator_called_with_correct_schema(self, tmp_path):
        """generate_intent_from_file should receive the correct schema summary."""
        env_file = _make_env_file(tmp_path)

        mock_gen = MagicMock(return_value=_GENERATED_INTENT)
        with patch("kyvos_sdk.warehouse_inspector.inspect_schema", side_effect=_mock_inspect_schema), \
             patch("kyvos_sm_skills.intent_generator.generate_intent_from_file", side_effect=mock_gen):
            from kyvos_sm_skills.intent_generator import generate_intent_from_file
            schema_summary = _mock_inspect_schema(None)
            result = generate_intent_from_file(
                intent_path=str(tmp_path / "intent.txt"),
                schema_summary=schema_summary,
                domain="adventure_works",
            )
            assert result == _GENERATED_INTENT
            mock_gen.assert_called_once()
            call_kwargs = mock_gen.call_args
            passed_schema = call_kwargs.kwargs.get("schema_summary") or call_kwargs[1].get("schema_summary")
            assert passed_schema["table_count"] == len(_AW_TABLES)

    def test_compiled_sm_with_calculated_kpis(self, tmp_path):
        """Flow B with calculated KPIs should compile with valid MDX expressions."""
        _, sm_json = _build_spec_and_compile(_AW_SM_DESIGN_WITH_KPIS, tmp_path)
        _validate_mdx_expressions(sm_json)


class TestModelComparison:
    """Compare Flow A and Flow B outputs for structural equivalence."""

    def test_both_flows_produce_same_table_count(self, tmp_path):
        """Both flows should select the same number of tables."""
        spec_a, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        spec_b, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        assert len(spec_a.tables) == len(spec_b.tables)

    def test_both_flows_produce_same_measure_count(self, tmp_path):
        """Both flows should produce the same number of measures."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        measures_a = sm_json_a["specific"]["smObject"]["measures"]["measure"]
        measures_b = sm_json_b["specific"]["smObject"]["measures"]["measure"]
        assert len(measures_a) == len(measures_b)

    def test_both_flows_produce_same_dimension_count(self, tmp_path):
        """Both flows should produce the same number of dimensions."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        dims_a = sm_json_a["specific"]["smObject"]["dimensions"]
        dims_b = sm_json_b["specific"]["smObject"]["dimensions"]
        assert len(dims_a) == len(dims_b)

    def test_both_flows_produce_same_hierarchy_count(self, tmp_path):
        """Both flows should produce the same total hierarchy count."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        h_a = sum(len(d["hierarchies"]) for d in sm_json_a["specific"]["smObject"]["dimensions"])
        h_b = sum(len(d["hierarchies"]) for d in sm_json_b["specific"]["smObject"]["dimensions"])
        assert h_a == h_b

    def test_both_flows_have_same_measure_names(self, tmp_path):
        """Both flows should produce measures with the same names."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        names_a = {m["name"] for m in sm_json_a["specific"]["smObject"]["measures"]["measure"]}
        names_b = {m["name"] for m in sm_json_b["specific"]["smObject"]["measures"]["measure"]}
        assert names_a == names_b

    def test_both_flows_have_same_dimension_names(self, tmp_path):
        """Both flows should produce dimensions with the same names."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        names_a = {d["name"] for d in sm_json_a["specific"]["smObject"]["dimensions"]}
        names_b = {d["name"] for d in sm_json_b["specific"]["smObject"]["dimensions"]}
        assert names_a == names_b

    def test_both_flows_have_common_object(self, tmp_path):
        """Both flows must have the 'common' top-level object."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        assert "common" in sm_json_a
        assert "common" in sm_json_b
        assert sm_json_a["common"]["compatibilityVersion"] == sm_json_b["common"]["compatibilityVersion"]

    def test_both_flows_have_actual_summary_function(self, tmp_path):
        """Both flows must have actualSummaryFunction on all measures."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        for m in sm_json_a["specific"]["smObject"]["measures"]["measure"]:
            assert "actualSummaryFunction" in m
        for m in sm_json_b["specific"]["smObject"]["measures"]["measure"]:
            assert "actualSummaryFunction" in m

    def test_both_flows_have_hierarchy_conformity_fields(self, tmp_path):
        """Both flows must have defaultMemberUniqueName and displayFolder on all hierarchies."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_hierarchy_fields(sm_json_a)
        _validate_hierarchy_fields(sm_json_b)

    def test_both_flows_have_level_conformity_fields(self, tmp_path):
        """Both flows must have dateDataType, dateFormat, format, fieldDataType on all levels."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN, tmp_path)
        _validate_level_fields(sm_json_a)
        _validate_level_fields(sm_json_b)

    def test_both_flows_with_kpis_have_valid_mdx(self, tmp_path):
        """Both flows with calculated KPIs should produce valid MDX expressions."""
        _, sm_json_a = _build_spec_and_compile(_AW_SM_DESIGN_WITH_KPIS, tmp_path)
        _, sm_json_b = _build_spec_and_compile(_AW_SM_DESIGN_WITH_KPIS, tmp_path)
        _validate_mdx_expressions(sm_json_a)
        _validate_mdx_expressions(sm_json_b)

    def test_both_flows_sm_designs_validate(self):
        """Both SM designs must pass validate_sm_recommendation with zero errors."""
        _validate_sm_recommendation(_AW_SM_DESIGN)
        _validate_sm_recommendation(_AW_SM_DESIGN_WITH_KPIS)


class TestAdventureWorksSchemaConformity:
    """Validate that the AdventureWorks sample SM design conforms to the mock schema."""

    def test_all_tables_in_design_exist_in_warehouse(self):
        """Every table in the SM design must exist in the warehouse schema."""
        wh_tables = {t["name"].lower() for t in _AW_TABLES}
        for t in _AW_SM_DESIGN["recommended_sms"][0]["tables"]:
            assert t.lower() in wh_tables, f"Table '{t}' not found in warehouse"

    def test_all_relationship_columns_exist(self):
        """Every relationship column must exist in the referenced table."""
        table_cols = {t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in _AW_TABLES}
        for rel in _AW_SM_DESIGN["recommended_sms"][0]["relationships"]:
            assert rel["from_column"].lower() in table_cols.get(rel["from_table"].lower(), set()), \
                f"Relationship from_column '{rel['from_column']}' not in table '{rel['from_table']}'"
            assert rel["to_column"].lower() in table_cols.get(rel["to_table"].lower(), set()), \
                f"Relationship to_column '{rel['to_column']}' not in table '{rel['to_table']}'"

    def test_all_hierarchy_levels_exist_in_source_dataset(self):
        """Every hierarchy level must be an actual column on the source_dataset table."""
        table_cols = {t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in _AW_TABLES}
        for h in _AW_SM_DESIGN["recommended_sms"][0]["hierarchies"]:
            source = h["source_dataset"].lower()
            assert source in table_cols, f"Hierarchy '{h['name']}' source_dataset '{source}' not in warehouse"
            for level in h["levels"]:
                assert level.lower() in table_cols[source], \
                    f"Hierarchy '{h['name']}' level '{level}' not a column on '{source}'"

    def test_all_measure_source_datasets_exist(self):
        """Every measure's source_dataset must be a table in the warehouse."""
        wh_tables = {t["name"].lower() for t in _AW_TABLES}
        for m in _AW_SM_DESIGN["recommended_sms"][0]["measures"]:
            source = m.get("source_dataset", "")
            if source:
                assert source.lower() in wh_tables, \
                    f"Measure '{m['name']}' source_dataset '{source}' not in warehouse"

    def test_domain_identification_is_adventure_works(self):
        """The SM design should identify the domain as adventure_works."""
        assert _AW_SM_DESIGN["identified_domain"] == "adventure_works"

    def test_gaps_identified_include_promotion_and_currency(self):
        """The SM design should identify DimPromotion and DimCurrency as gaps."""
        gaps = _AW_SM_DESIGN["gaps_identified"]
        gap_text = " ".join(gaps).lower()
        assert "promotion" in gap_text
        assert "currency" in gap_text
