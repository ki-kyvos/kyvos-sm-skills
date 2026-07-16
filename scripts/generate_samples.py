#!/usr/bin/env python3
"""Generate sample Kyvos semantic model payloads for 5 industry verticals.

Usage:
    python scripts/generate_samples.py [--output-dir samples/models]

Produces JSON + XML outputs for connection, datasets, DRD, and semantic model
for each vertical in samples/models/<vertical>/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kyvos_sm_skills.generators.connection_json import generate_connection_json
from kyvos_sm_skills.generators.connection_xml import generate_connection_xml
from kyvos_sm_skills.generators.dataset_json import DatasetJsonGenerator
from kyvos_sm_skills.generators.dataset_xml import DatasetXmlGenerator
from kyvos_sm_skills.generators.drd_json import DrdJsonGenerator
from kyvos_sm_skills.generators.drd_xml import DrdXmlGenerator, SimpleRel
from kyvos_sm_skills.generators.smodel_json import SModelJsonGenerator
from kyvos_sm_skills.generators.smodel_xml import SModelXmlGenerator
from kyvos_sm_skills.models import ColumnSpec, HierarchySpec, MeasureSpec, TableSpec


def _retail_banking() -> dict:
    """Retail Banking sample model definition."""
    tables = [
        TableSpec(
            name="dim_customer",
            schema_name="retail_banking",
            table_type="dimension",
            columns=[
                ColumnSpec(name="customer_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="customer_id", data_type="VARCHAR(20)"),
                ColumnSpec(name="customer_name", data_type="VARCHAR(200)"),
                ColumnSpec(name="segment", data_type="VARCHAR(50)"),
                ColumnSpec(name="region", data_type="VARCHAR(100)"),
            ],
        ),
        TableSpec(
            name="dim_date",
            schema_name="retail_banking",
            table_type="dimension",
            columns=[
                ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="year", data_type="INTEGER"),
                ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
                ColumnSpec(name="month", data_type="VARCHAR(10)"),
                ColumnSpec(name="month_name", data_type="VARCHAR(20)"),
            ],
        ),
        TableSpec(
            name="fact_transactions",
            schema_name="retail_banking",
            table_type="fact",
            columns=[
                ColumnSpec(name="transaction_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="customer_fk", data_type="INTEGER", is_foreign_key=True, references="retail_banking.dim_customer.customer_pk"),
                ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True, references="retail_banking.dim_date.date_pk"),
                ColumnSpec(name="transaction_amount", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="fee_amount", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="transaction_count", data_type="INTEGER"),
            ],
        ),
    ]
    relationships = [
        SimpleRel("fact_transactions", "customer_fk", "dim_customer", "customer_pk"),
        SimpleRel("fact_transactions", "date_fk", "dim_date", "date_pk"),
    ]
    measures = [
        MeasureSpec(name="Total Transaction Amount", expression="", source_dataset="FactTransactions", source_column="transaction_amount", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Total Fees", expression="", source_dataset="FactTransactions", source_column="fee_amount", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Transaction Count", expression="", source_dataset="FactTransactions", source_column="transaction_count", aggregation_type="sum", format_string="#,##0"),
        MeasureSpec(name="Net Revenue", expression="[Measures].[Total Transaction Amount] + [Measures].[Total Fees]", is_calculated=True, source_dataset="FactTransactions", format_string="#,##0.00"),
    ]
    hierarchies = [
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Customer Geography", levels=["region", "segment"], source_dataset="DimCustomer"),
    ]
    return {
        "tables": tables,
        "relationships": relationships,
        "measures": measures,
        "hierarchies": hierarchies,
        "connection_name": "RetailBankingConnection",
        "schema_name": "retail_banking",
    }


def _healthcare() -> dict:
    """Healthcare sample model definition."""
    tables = [
        TableSpec(
            name="dim_patient",
            schema_name="healthcare",
            table_type="dimension",
            columns=[
                ColumnSpec(name="patient_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="patient_id", data_type="VARCHAR(20)"),
                ColumnSpec(name="patient_name", data_type="VARCHAR(200)"),
                ColumnSpec(name="gender", data_type="VARCHAR(10)"),
                ColumnSpec(name="age_group", data_type="VARCHAR(20)"),
            ],
        ),
        TableSpec(
            name="dim_date",
            schema_name="healthcare",
            table_type="dimension",
            columns=[
                ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="year", data_type="INTEGER"),
                ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
                ColumnSpec(name="month", data_type="VARCHAR(10)"),
            ],
        ),
        TableSpec(
            name="fact_admissions",
            schema_name="healthcare",
            table_type="fact",
            columns=[
                ColumnSpec(name="admission_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="patient_fk", data_type="INTEGER", is_foreign_key=True, references="healthcare.dim_patient.patient_pk"),
                ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True, references="healthcare.dim_date.date_pk"),
                ColumnSpec(name="admission_count", data_type="INTEGER"),
                ColumnSpec(name="length_of_stay", data_type="NUMERIC(8,2)"),
                ColumnSpec(name="total_cost", data_type="NUMERIC(15,2)"),
            ],
        ),
    ]
    relationships = [
        SimpleRel("fact_admissions", "patient_fk", "dim_patient", "patient_pk"),
        SimpleRel("fact_admissions", "date_fk", "dim_date", "date_pk"),
    ]
    measures = [
        MeasureSpec(name="Total Admissions", expression="", source_dataset="FactAdmissions", source_column="admission_count", aggregation_type="sum", format_string="#,##0"),
        MeasureSpec(name="Avg Length of Stay", expression="", source_dataset="FactAdmissions", source_column="length_of_stay", aggregation_type="average", format_string="#,##0.00"),
        MeasureSpec(name="Total Cost", expression="", source_dataset="FactAdmissions", source_column="total_cost", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Cost per Admission", expression="[Measures].[Total Cost] / [Measures].[Total Admissions]", is_calculated=True, source_dataset="FactAdmissions", format_string="#,##0.00"),
    ]
    hierarchies = [
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Patient Demographics", levels=["age_group", "gender"], source_dataset="DimPatient"),
    ]
    return {
        "tables": tables,
        "relationships": relationships,
        "measures": measures,
        "hierarchies": hierarchies,
        "connection_name": "HealthcareConnection",
        "schema_name": "healthcare",
    }


def _retail_ecommerce() -> dict:
    """Retail E-commerce sample model definition."""
    tables = [
        TableSpec(
            name="dim_product",
            schema_name="retail_ecom",
            table_type="dimension",
            columns=[
                ColumnSpec(name="product_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="product_id", data_type="VARCHAR(20)"),
                ColumnSpec(name="product_name", data_type="VARCHAR(200)"),
                ColumnSpec(name="category", data_type="VARCHAR(100)"),
                ColumnSpec(name="subcategory", data_type="VARCHAR(100)"),
                ColumnSpec(name="brand", data_type="VARCHAR(100)"),
            ],
        ),
        TableSpec(
            name="dim_date",
            schema_name="retail_ecom",
            table_type="dimension",
            columns=[
                ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="year", data_type="INTEGER"),
                ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
                ColumnSpec(name="month", data_type="VARCHAR(10)"),
                ColumnSpec(name="month_name", data_type="VARCHAR(20)"),
            ],
        ),
        TableSpec(
            name="fact_sales",
            schema_name="retail_ecom",
            table_type="fact",
            columns=[
                ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="product_fk", data_type="INTEGER", is_foreign_key=True, references="retail_ecom.dim_product.product_pk"),
                ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True, references="retail_ecom.dim_date.date_pk"),
                ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="quantity", data_type="INTEGER"),
                ColumnSpec(name="discount_amount", data_type="NUMERIC(15,2)"),
            ],
        ),
    ]
    relationships = [
        SimpleRel("fact_sales", "product_fk", "dim_product", "product_pk"),
        SimpleRel("fact_sales", "date_fk", "dim_date", "date_pk"),
    ]
    measures = [
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactSales", source_column="sales_amount", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Total Quantity", expression="", source_dataset="FactSales", source_column="quantity", aggregation_type="sum", format_string="#,##0"),
        MeasureSpec(name="Total Discount", expression="", source_dataset="FactSales", source_column="discount_amount", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Net Sales", expression="[Measures].[Total Sales] - [Measures].[Total Discount]", is_calculated=True, source_dataset="FactSales", format_string="#,##0.00"),
        MeasureSpec(name="Avg Order Value", expression="[Measures].[Total Sales] / [Measures].[Total Quantity]", is_calculated=True, source_dataset="FactSales", format_string="#,##0.00"),
        MeasureSpec(name="Discount Rate", expression="[Measures].[Total Discount] / [Measures].[Total Sales]", is_calculated=True, source_dataset="FactSales", format_string="0.00%"),
    ]
    hierarchies = [
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Product Hierarchy", levels=["category", "subcategory", "brand"], source_dataset="DimProduct"),
    ]
    return {
        "tables": tables,
        "relationships": relationships,
        "measures": measures,
        "hierarchies": hierarchies,
        "connection_name": "RetailEcomConnection",
        "schema_name": "retail_ecom",
    }


def _telecom() -> dict:
    """Telecom sample model definition."""
    tables = [
        TableSpec(
            name="dim_region",
            schema_name="telecom",
            table_type="dimension",
            columns=[
                ColumnSpec(name="region_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="region_name", data_type="VARCHAR(100)"),
                ColumnSpec(name="country", data_type="VARCHAR(50)"),
                ColumnSpec(name="city", data_type="VARCHAR(100)"),
            ],
        ),
        TableSpec(
            name="dim_date",
            schema_name="telecom",
            table_type="dimension",
            columns=[
                ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="year", data_type="INTEGER"),
                ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
                ColumnSpec(name="month", data_type="VARCHAR(10)"),
            ],
        ),
        TableSpec(
            name="fact_usage",
            schema_name="telecom",
            table_type="fact",
            columns=[
                ColumnSpec(name="usage_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="region_fk", data_type="INTEGER", is_foreign_key=True, references="telecom.dim_region.region_pk"),
                ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True, references="telecom.dim_date.date_pk"),
                ColumnSpec(name="call_minutes", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="data_usage_mb", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="sms_count", data_type="INTEGER"),
            ],
        ),
    ]
    relationships = [
        SimpleRel("fact_usage", "region_fk", "dim_region", "region_pk"),
        SimpleRel("fact_usage", "date_fk", "dim_date", "date_pk"),
    ]
    measures = [
        MeasureSpec(name="Total Call Minutes", expression="", source_dataset="FactUsage", source_column="call_minutes", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Total Data Usage", expression="", source_dataset="FactUsage", source_column="data_usage_mb", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Total SMS", expression="", source_dataset="FactUsage", source_column="sms_count", aggregation_type="sum", format_string="#,##0"),
        MeasureSpec(name="Avg Data per SMS", expression="[Measures].[Total Data Usage] / [Measures].[Total SMS]", is_calculated=True, source_dataset="FactUsage", format_string="#,##0.00"),
    ]
    hierarchies = [
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Geography", levels=["country", "region_name", "city"], source_dataset="DimRegion"),
    ]
    return {
        "tables": tables,
        "relationships": relationships,
        "measures": measures,
        "hierarchies": hierarchies,
        "connection_name": "TelecomConnection",
        "schema_name": "telecom",
    }


def _adventure_works() -> dict:
    """Adventure Works sample model definition."""
    tables = [
        TableSpec(
            name="dim_product",
            schema_name="adventure_works",
            table_type="dimension",
            columns=[
                ColumnSpec(name="product_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="product_name", data_type="VARCHAR(200)"),
                ColumnSpec(name="category", data_type="VARCHAR(100)"),
                ColumnSpec(name="subcategory", data_type="VARCHAR(100)"),
                ColumnSpec(name="color", data_type="VARCHAR(50)"),
                ColumnSpec(name="list_price", data_type="NUMERIC(12,2)"),
            ],
        ),
        TableSpec(
            name="dim_date",
            schema_name="adventure_works",
            table_type="dimension",
            columns=[
                ColumnSpec(name="date_pk", data_type="INTEGER", is_primary_key=True),
                ColumnSpec(name="year", data_type="INTEGER"),
                ColumnSpec(name="quarter", data_type="VARCHAR(10)"),
                ColumnSpec(name="month", data_type="VARCHAR(10)"),
                ColumnSpec(name="month_name", data_type="VARCHAR(20)"),
            ],
        ),
        TableSpec(
            name="fact_internet_sales",
            schema_name="adventure_works",
            table_type="fact",
            columns=[
                ColumnSpec(name="sales_pk", data_type="BIGINT", is_primary_key=True),
                ColumnSpec(name="product_fk", data_type="INTEGER", is_foreign_key=True, references="adventure_works.dim_product.product_pk"),
                ColumnSpec(name="date_fk", data_type="INTEGER", is_foreign_key=True, references="adventure_works.dim_date.date_pk"),
                ColumnSpec(name="sales_amount", data_type="NUMERIC(15,2)"),
                ColumnSpec(name="order_quantity", data_type="INTEGER"),
                ColumnSpec(name="unit_price", data_type="NUMERIC(12,2)"),
                ColumnSpec(name="discount_pct", data_type="NUMERIC(7,4)"),
            ],
        ),
    ]
    relationships = [
        SimpleRel("fact_internet_sales", "product_fk", "dim_product", "product_pk"),
        SimpleRel("fact_internet_sales", "date_fk", "dim_date", "date_pk"),
    ]
    measures = [
        MeasureSpec(name="Total Sales", expression="", source_dataset="FactInternetSales", source_column="sales_amount", aggregation_type="sum", format_string="#,##0.00"),
        MeasureSpec(name="Total Order Quantity", expression="", source_dataset="FactInternetSales", source_column="order_quantity", aggregation_type="sum", format_string="#,##0"),
        MeasureSpec(name="Avg Unit Price", expression="", source_dataset="FactInternetSales", source_column="unit_price", aggregation_type="average", format_string="#,##0.00"),
        MeasureSpec(name="Avg Discount", expression="", source_dataset="FactInternetSales", source_column="discount_pct", aggregation_type="average", format_string="0.00%"),
        MeasureSpec(name="Revenue per Unit", expression="[Measures].[Total Sales] / [Measures].[Total Order Quantity]", is_calculated=True, source_dataset="FactInternetSales", format_string="#,##0.00"),
    ]
    hierarchies = [
        HierarchySpec(name="Calendar", levels=["year", "quarter", "month"], source_dataset="DimDate"),
        HierarchySpec(name="Product Category", levels=["category", "subcategory", "color"], source_dataset="DimProduct"),
    ]
    return {
        "tables": tables,
        "relationships": relationships,
        "measures": measures,
        "hierarchies": hierarchies,
        "connection_name": "AdventureWorksConnection",
        "schema_name": "adventure_works",
    }


VERTICALS = {
    "retail-banking": _retail_banking,
    "healthcare": _healthcare,
    "retail-ecommerce": _retail_ecommerce,
    "telecom": _telecom,
    "adventure-works": _adventure_works,
}


def _format_dataset_name(table_name: str) -> str:
    parts = table_name.split("_")
    return "".join(word.capitalize() for word in parts)


def generate_vertical(name: str, spec: dict, output_dir: Path) -> None:
    """Generate all payloads for a single vertical."""
    vdir = output_dir / name
    vdir.mkdir(parents=True, exist_ok=True)

    conn_name = spec["connection_name"]
    schema = spec["schema_name"]
    tables = spec["tables"]
    relationships = spec["relationships"]
    measures = spec["measures"]
    hierarchies = spec["hierarchies"]

    # Connection
    conn_json = generate_connection_json(
        name=conn_name, host="localhost", port=5432,
        database=schema.replace("_", ""), username="demo_user", password="demo_pass",
    )
    conn_xml = generate_connection_xml(
        name=conn_name, host="localhost", port=5432,
        database=schema.replace("_", ""), username="demo_user", password="demo_pass",
    )
    (vdir / "connection.json").write_text(json.dumps(conn_json, indent=2))
    (vdir / "connection.xml").write_text(conn_xml)

    # Datasets
    ds_json_gen = DatasetJsonGenerator(connection_name=conn_name, category_name=name)
    ds_xml_gen = DatasetXmlGenerator(connection_name=conn_name)

    dataset_name_to_id: dict[str, str] = {}
    dataset_aliases: dict[str, str] = {}
    dataset_columns: dict[str, list[dict]] = {}
    fact_dataset_names: set[str] = set()
    connected_dim_names: set[str] = set()

    for table in tables:
        ds_name = _format_dataset_name(table.name)
        ds_id = f"ds_{abs(hash(ds_name)) % 1000000:06d}"
        dataset_name_to_id[ds_name] = ds_id
        dataset_aliases[table.name] = ds_name

        cols_for_sm: list[dict] = []
        for col in table.columns:
            from kyvos_sm_skills.type_mapping import resolve_sql_type, SQL_TO_KYVOS_XML_MAP
            canonical = resolve_sql_type(col.data_type)
            dt_name = SQL_TO_KYVOS_XML_MAP.get(canonical, ("CHAR", "1", "", "1"))[0]
            cols_for_sm.append({"name": col.name, "datatype": dt_name})

        dataset_columns[ds_name] = cols_for_sm

        if table.table_type == "fact":
            fact_dataset_names.add(ds_name)
        else:
            connected_dim_names.add(ds_name)

        # Dataset JSON
        ds_payload = ds_json_gen.generate_json_payload(table)
        (vdir / f"dataset_{ds_name}.json").write_text(json.dumps(ds_payload, indent=2))

        # Dataset XML
        try:
            ds_xml = ds_xml_gen.generate_xml(table)
            (vdir / f"dataset_{ds_name}.xml").write_text(ds_xml)
        except Exception:
            pass  # XML gen may need sample reference file

    # DRD
    drd_json_gen = DrdJsonGenerator(drd_folder_id="folder_drd", drd_folder_name="DRDs")
    drd_name = f"{name.title().replace('-', '')}DRD"
    drd_json = drd_json_gen.generate(
        drd_name=drd_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=relationships,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
    )
    (vdir / "drd.json").write_text(json.dumps(drd_json, indent=2))

    drd_xml_gen = DrdXmlGenerator(drd_folder_id="folder_drd", drd_folder_name="DRDs")
    drd_xml = drd_xml_gen.generate(
        drd_name=drd_name,
        dataset_name_to_id=dataset_name_to_id,
        relationships=relationships,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
    )
    (vdir / "drd.xml").write_text(drd_xml)

    # Semantic Model
    sm_json_gen = SModelJsonGenerator(
        folder_id="folder_sm",
        folder_name="Semantic Models",
        smodel_name=f"{name.title().replace('-', '')}Model",
        connection_name=conn_name,
        drd_id="drd_001",
        drd_name=drd_name,
        drd_xml=drd_xml,
        dataset_name_to_id=dataset_name_to_id,
        dataset_columns=dataset_columns,
        hierarchy_specs=hierarchies,
        semantic_measures=measures,
        fact_dataset_names=fact_dataset_names,
        connected_dim_names=connected_dim_names,
    )
    sm_json = sm_json_gen.generate()
    (vdir / "semantic_model.json").write_text(json.dumps(sm_json, indent=2))

    sm_xml_gen = SModelXmlGenerator(
        folder_id="folder_sm",
        folder_name="Semantic Models",
        smodel_name=f"{name.title().replace('-', '')}Model",
        connection_name=conn_name,
        drd_id="drd_001",
        drd_name=drd_name,
        drd_xml=drd_xml,
        dataset_name_to_id=dataset_name_to_id,
        dataset_columns=dataset_columns,
        hierarchy_specs=hierarchies,
        semantic_measures=measures,
    )
    sm_xml = sm_xml_gen.generate()
    (vdir / "semantic_model.xml").write_text(sm_xml)

    print(f"  Generated {name}: connection, {len(tables)} datasets, DRD, semantic model")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sample Kyvos SM payloads")
    parser.add_argument("--output-dir", default="samples/models", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.is_absolute():
        output_dir = Path(__file__).resolve().parent.parent / output_dir

    print(f"Generating sample models in {output_dir}/")
    for name, builder in VERTICALS.items():
        spec = builder()
        generate_vertical(name, spec, output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
