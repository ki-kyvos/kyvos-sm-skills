"""Regression tests for bridge table detection logic.

Tests are designed to be deterministic and self-contained — no live Kyvos
deployment needed.  They verify that:

* A classic SalesReasons-like bridge table (composite PK of FKs, 0 non-PK
  columns, incoming from fact, outgoing to dimension) is classified as bridge.
* Tables with many non-PK columns (ExchangeRates, FinancialReporting) are NOT
  bridges.
* Tables with many outgoing relationships (InternetCustomers, ResellerOrders)
  are NOT bridges.
* Tables with no outgoing to dimensions (Organization) are NOT bridges.
* Tables with measures assigned are NOT bridges.
* Tables with outgoing to fact tables are NOT bridges.
"""

from __future__ import annotations

from kyvos_sm_skills.bridge_detector import detect_bridges
from kyvos_sm_skills.models import ColumnSpec, MeasureSpec, RelationshipSpec, TableSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pk_col(name: str, data_type: str = "INTEGER") -> ColumnSpec:
    return ColumnSpec(name=name, data_type=data_type, is_primary_key=True)


def _fk_col(name: str, ref: str, data_type: str = "INTEGER") -> ColumnSpec:
    return ColumnSpec(
        name=name, data_type=data_type, is_primary_key=True,
        is_foreign_key=True, references=ref,
    )


def _reg_col(name: str, data_type: str = "VARCHAR") -> ColumnSpec:
    return ColumnSpec(name=name, data_type=data_type)


def _fact_table(name: str, cols: list[ColumnSpec]) -> TableSpec:
    return TableSpec(name=name, table_type="fact", columns=cols)


def _dim_table(name: str, cols: list[ColumnSpec]) -> TableSpec:
    return TableSpec(name=name, table_type="dimension", columns=cols)


def _bridge_table(name: str, cols: list[ColumnSpec]) -> TableSpec:
    return TableSpec(name=name, table_type="bridge", columns=cols)


def _unknown_table(name: str, cols: list[ColumnSpec]) -> TableSpec:
    return TableSpec(name=name, table_type="unknown", columns=cols)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _classic_bridge_fixture():
    """SalesReasons: the canonical bridge table.

    Structure:
        InternetSales (fact) --salesordernumber--> SalesReasons (bridge) --salesreasonkey--> SalesReason (dim)

    SalesReasons has 4 columns:
        salesreasonkey (PK, FK → SalesReason.salesreasonkey)
        salesordernumber (PK, FK → InternetSales.salesordernumber)
        salesorderlinenumber (PK)
        salesreasonreasontype (non-PK, descriptive)
    """
    tables = [
        _fact_table("InternetSales", [
            _pk_col("salesordernumber"),
            _reg_col("salesamount", "NUMERIC"),
        ]),
        _dim_table("SalesReason", [
            _pk_col("salesreasonkey"),
            _reg_col("reasontype"),
        ]),
        _bridge_table("SalesReasons", [
            _fk_col("salesreasonkey", "SalesReason.salesreasonkey"),
            _fk_col("salesordernumber", "InternetSales.salesordernumber"),
            _pk_col("salesorderlinenumber"),
            _reg_col("salesreasonreasontype"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetSales", left_column="salesordernumber",
            right_dataset="SalesReasons", right_column="salesordernumber",
        ),
        RelationshipSpec(
            left_dataset="SalesReasons", left_column="salesreasonkey",
            right_dataset="SalesReason", right_column="salesreasonkey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales", source_column="salesamount"),
    ]
    return tables, rels, measures


def _exchange_rates_fixture():
    """ExchangeRates: a fact-like table misclassified as bridge by LLM.

    Has 5+ non-PK columns — too many for a true bridge.
    """
    tables = [
        _fact_table("InternetSales", [
            _pk_col("salesordernumber"),
            _reg_col("currencyname"),
        ]),
        _dim_table("DestinationCurrency", [
            _pk_col("currencykey"),
            _reg_col("currencyname"),
        ]),
        _bridge_table("ExchangeRates", [
            _pk_col("exchangeratekey"),
            _reg_col("currencykey"),
            _reg_col("date"),
            _reg_col("averagerate"),
            _reg_col("endofdayrate"),
            _reg_col("currencyname"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetSales", left_column="currencyname",
            right_dataset="ExchangeRates", right_column="currencyname",
        ),
        RelationshipSpec(
            left_dataset="ExchangeRates", left_column="currencykey",
            right_dataset="DestinationCurrency", right_column="currencykey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales"),
    ]
    return tables, rels, measures


def _many_outgoing_fixture():
    """InternetCustomers: a fact table misclassified as bridge by LLM.

    Has 5 outgoing relationships — too many for a bridge.
    """
    tables = [
        _fact_table("InternetSales", [_pk_col("salesordernumber")]),
        _dim_table("Customer", [_pk_col("customerkey")]),
        _dim_table("Product", [_pk_col("productkey")]),
        _dim_table("SalesTerritory", [_pk_col("salesterritorykey")]),
        _dim_table("Promotion", [_pk_col("promotionkey")]),
        _dim_table("Currency", [_pk_col("currencykey")]),
        _bridge_table("InternetCustomers", [
            _pk_col("customerkey"),
            _reg_col("salesordernumber"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetCustomers", left_column="customerkey",
            right_dataset="Customer", right_column="customerkey",
        ),
        RelationshipSpec(
            left_dataset="InternetCustomers", left_column="productkey",
            right_dataset="Product", right_column="productkey",
        ),
        RelationshipSpec(
            left_dataset="InternetCustomers", left_column="salesterritorykey",
            right_dataset="SalesTerritory", right_column="salesterritorykey",
        ),
        RelationshipSpec(
            left_dataset="InternetCustomers", left_column="promotionkey",
            right_dataset="Promotion", right_column="promotionkey",
        ),
        RelationshipSpec(
            left_dataset="InternetCustomers", left_column="currencykey",
            right_dataset="Currency", right_column="currencykey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales"),
    ]
    return tables, rels, measures


def _no_outgoing_to_dim_fixture():
    """Organization: a dimension misclassified as bridge by LLM.

    Has no outgoing to any dimension.
    """
    tables = [
        _fact_table("InternetSales", [_pk_col("salesordernumber")]),
        _bridge_table("Organization", [
            _pk_col("organizationkey"),
            _reg_col("parentorganizationkey"),
            _reg_col("organizationname"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetSales", left_column="organizationkey",
            right_dataset="Organization", right_column="organizationkey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales"),
    ]
    return tables, rels, measures


def _bridge_with_measures_fixture():
    """A bridge-classified table that has measures assigned — should be reclassified."""
    tables = [
        _fact_table("InternetSales", [_pk_col("salesordernumber")]),
        _dim_table("SalesReason", [_pk_col("salesreasonkey")]),
        _bridge_table("SalesReasons", [
            _fk_col("salesreasonkey", "SalesReason.salesreasonkey"),
            _fk_col("salesordernumber", "InternetSales.salesordernumber"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetSales", left_column="salesordernumber",
            right_dataset="SalesReasons", right_column="salesordernumber",
        ),
        RelationshipSpec(
            left_dataset="SalesReasons", left_column="salesreasonkey",
            right_dataset="SalesReason", right_column="salesreasonkey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales"),
        MeasureSpec(name="ReasonCount", expression="COUNT()",
                    source_dataset="SalesReasons"),
    ]
    return tables, rels, measures


def _bridge_outgoing_to_fact_fixture():
    """A bridge-classified table with outgoing to a fact — should be reclassified."""
    tables = [
        _fact_table("InternetSales", [_pk_col("salesordernumber")]),
        _fact_table("ResellerSales", [_pk_col("resellerordernumber")]),
        _dim_table("SalesReason", [_pk_col("salesreasonkey")]),
        _bridge_table("SalesReasons", [
            _fk_col("salesreasonkey", "SalesReason.salesreasonkey"),
            _fk_col("salesordernumber", "InternetSales.salesordernumber"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetSales", left_column="salesordernumber",
            right_dataset="SalesReasons", right_column="salesordernumber",
        ),
        RelationshipSpec(
            left_dataset="SalesReasons", left_column="salesordernumber",
            right_dataset="ResellerSales", right_column="resellerordernumber",
        ),
        RelationshipSpec(
            left_dataset="SalesReasons", left_column="salesreasonkey",
            right_dataset="SalesReason", right_column="salesreasonkey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales"),
    ]
    return tables, rels, measures


def _unknown_table_auto_detected_as_bridge_fixture():
    """A table classified as 'unknown' by the LLM that should be auto-detected as bridge."""
    tables = [
        _fact_table("InternetSales", [
            _pk_col("salesordernumber"),
            _reg_col("salesamount"),
        ]),
        _dim_table("SalesReason", [
            _pk_col("salesreasonkey"),
            _reg_col("reasontype"),
        ]),
        _unknown_table("SalesReasons", [
            _fk_col("salesreasonkey", "SalesReason.salesreasonkey"),
            _fk_col("salesordernumber", "InternetSales.salesordernumber"),
            _pk_col("salesorderlinenumber"),
        ]),
    ]
    rels = [
        RelationshipSpec(
            left_dataset="InternetSales", left_column="salesordernumber",
            right_dataset="SalesReasons", right_column="salesordernumber",
        ),
        RelationshipSpec(
            left_dataset="SalesReasons", left_column="salesreasonkey",
            right_dataset="SalesReason", right_column="salesreasonkey",
        ),
    ]
    measures = [
        MeasureSpec(name="SalesAmount", expression="[SalesAmount]",
                    source_dataset="InternetSales"),
    ]
    return tables, rels, measures


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestClassicBridge:
    def test_sales_reasons_classified_as_bridge(self):
        tables, rels, measures = _classic_bridge_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "SalesReasons" in result.bridge_names
        assert len(result.bridge_names) == 1

    def test_sales_reasons_decision_record(self):
        tables, rels, measures = _classic_bridge_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        decisions = {d.table_name: d for d in result.decisions}
        d = decisions["SalesReasons"]
        assert d.is_bridge is True
        assert d.has_incoming_from_fact is True
        assert d.has_outgoing_to_dim is True
        assert d.has_outgoing_to_fact is False
        assert d.has_measures is False
        assert d.non_pk_columns <= 1


class TestNonBridges:
    def test_exchange_rates_not_bridge(self):
        tables, rels, measures = _exchange_rates_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "ExchangeRates" not in result.bridge_names
        assert "ExchangeRates" in result.reclassified

    def test_many_outgoing_not_bridge(self):
        tables, rels, measures = _many_outgoing_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "InternetCustomers" not in result.bridge_names
        assert "InternetCustomers" in result.reclassified

    def test_no_outgoing_to_dim_not_bridge(self):
        tables, rels, measures = _no_outgoing_to_dim_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "Organization" not in result.bridge_names
        assert "Organization" in result.reclassified

    def test_bridge_with_measures_not_bridge(self):
        tables, rels, measures = _bridge_with_measures_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "SalesReasons" not in result.bridge_names
        assert "SalesReasons" in result.reclassified

    def test_bridge_outgoing_to_fact_not_bridge(self):
        tables, rels, measures = _bridge_outgoing_to_fact_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "SalesReasons" not in result.bridge_names
        assert "SalesReasons" in result.reclassified


class TestAutoDetection:
    def test_unknown_table_auto_detected_as_bridge(self):
        tables, rels, measures = _unknown_table_auto_detected_as_bridge_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "SalesReasons" in result.bridge_names
        assert len(result.bridge_names) == 1

    def test_fact_prefixed_junction_table_kept_as_bridge(self):
        """factinternetsalesreason in AdventureWorks is a junction table
        (all-PK, incoming from fact, outgoing to dim). When the LLM classifies
        it as bridge, that classification is kept despite the "fact" prefix."""
        tables = [
            _fact_table("factinternetsales", [_pk_col("salesordernumber"), _pk_col("salesorderlinenumber"), _reg_col("orderqty")]),
            TableSpec(
                name="factinternetsalesreason",
                table_type="bridge",
                columns=[
                    _fk_col("salesordernumber", "factinternetsales.salesordernumber"),
                    _fk_col("salesorderlinenumber", "factinternetsales.salesorderlinenumber"),
                    _fk_col("salesreasonkey", "dimsalesreason.salesreasonkey"),
                ],
            ),
            _dim_table("dimsalesreason", [_pk_col("salesreasonkey"), _reg_col("salesreasonname")]),
        ]
        rels = [
            # after _normalize_bridge_relationships the edge is: fact → factinternetsalesreason
            RelationshipSpec(
                left_dataset="factinternetsales", left_column="salesordernumber",
                right_dataset="factinternetsalesreason", right_column="salesordernumber",
                relationship_type="many_to_many",
            ),
            RelationshipSpec(
                left_dataset="factinternetsalesreason", left_column="salesreasonkey",
                right_dataset="dimsalesreason", right_column="salesreasonkey",
                relationship_type="many_to_one",
            ),
        ]
        result = detect_bridges(tables=tables, relationships=rels, measures=[])
        assert "factinternetsalesreason" in result.bridge_names
        assert "factinternetsalesreason" not in result.fact_names
        assert "factinternetsalesreason" not in result.reclassified


class TestResultStructure:
    def test_summary_string_contains_bridge_names(self):
        tables, rels, measures = _classic_bridge_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        s = result.summary()
        assert "SalesReasons" in s
        assert "Bridge datasets" in s

    def test_to_json_round_trip(self):
        tables, rels, measures = _classic_bridge_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        j = result.to_json()
        import json
        d = json.loads(j)
        assert "SalesReasons" in d["bridge_names"]

    def test_fact_and_dim_sets_populated(self):
        tables, rels, measures = _classic_bridge_fixture()
        result = detect_bridges(
            tables=tables, relationships=rels, measures=measures,
        )
        assert "InternetSales" in result.fact_names
        assert "salesreason" in result.dim_names
