"""Kyvos Dataset XML Generator – creates Kyvos dataset XML definitions from table schemas.

Generates dataset XML compatible with Kyvos 2026.1 REST API for automated
dataset registration. Uses the sample/dataset.xml as a reference structure
but generates XML programmatically — the template file is never modified.
"""

from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Any
from xml.dom import minidom
from xml.etree import ElementTree as ET

from kyvos_sm_skills.models import ColumnSpec, TableSpec
from kyvos_sm_skills.type_mapping import (
    SQL_TO_KYVOS_XML_MAP,
    _KYVOS_TYPE_ALIAS,
    field_format_value,
    map_sql_to_kyvos_type,
    resolve_sql_type,
)
import logging

logger = logging.getLogger(__name__)

# ── Reference template path (read-only) ──────────────────────────────────────

_SAMPLE_XML_PATH = Path(__file__).resolve().parents[1] / "samples" / "kyvos-entities" / "dataset.xml"


class DatasetXmlGenerator:
    """Generate Kyvos dataset XML definitions from table schemas."""

    def __init__(
        self,
        connection_name: str = "PostgresConnection",
        connection_type: str = "POSTGRES",
        category_name: str = "Demo Automation",
        folder_id: str | None = None,
    ) -> None:
        self.connection_name = connection_name
        self.connection_type = connection_type
        self.category_name = category_name
        self.folder_id = folder_id

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_dataset_xml(
        self,
        table: TableSpec,
        output_dir: Path,
    ) -> Path:
        """Generate Kyvos dataset XML for a single table.

        Args:
            table: Table specification with schema and columns.
            output_dir: Directory to write the XML file.

        Returns:
            Path to the generated XML file.
        """
        if not table.columns:
            raise ValueError(f"TableSpec.columns is empty for {table.schema_name}.{table.name}")
            
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        dataset_id = self._generate_id()
        step_id = self._generate_id()

        category_id = self.folder_id or f"folder_{self._generate_id()}"
        
        dataset_name = self._format_table_name(table.name)
        role = self._resolve_role(table)
        # sql_query = f"SELECT * FROM {table.schema_name.upper()}.{table.name.upper()}"

        schema = (table.schema_name or "public").strip()
        name = table.name.strip()
        sql_query = f"SELECT * FROM {self._safe_sql_ident(schema)}.{self._safe_sql_ident(name)}"

        # Build XML tree
        root = self._build_iro_element(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            category_id=category_id,
            role=role,
        )

        # Add common section
        self._add_common_section(root, table.description or "")

        # Add specific section with transformation steps and columns
        self._add_specific_section(
            root,
            step_id=step_id,
            sql_query=sql_query,
            role=role,
            columns=table.columns,
        )

        # Serialize and validate
        xml_string = self._serialize_xml(root)
        self._validate_xml(xml_string)

        # Write to file
        output_file = output_dir / f"{table.name}_dataset.xml"
        output_file.write_text(xml_string, encoding="utf-8")

        logger.info(
            "kyvos_dataset_xml_generated",
            table=table.name,
            role=role,
            columns=len(table.columns),
            output_file=str(output_file),
        )

        return output_file

    def generate_all_datasets_xml(
        self,
        tables: list[TableSpec],
        output_dir: Path,
    ) -> list[Path]:
        """Generate Kyvos dataset XMLs for all tables.

        Args:
            tables: List of table specifications.
            output_dir: Directory to write XML files.

        Returns:
            List of paths to generated XML files.
        """
        output_files: list[Path] = []
        for table in tables:
            try:
                output_file = self.generate_dataset_xml(table, output_dir)
                output_files.append(output_file)
            except Exception as exc:
                logger.error(
                    "kyvos_dataset_xml_generation_failed",
                    table=table.name,
                    error=str(exc),
                )

        logger.info(
            "kyvos_datasets_xml_generated",
            total=len(output_files),
            output_dir=str(output_dir),
        )
        return output_files

    def generate_xml_payload(self, table: TableSpec) -> str:
        """Generate XML payload string for direct API submission (no file I/O).

        Args:
            table: Table specification.

        Returns:
            XML string suitable for Kyvos CreateDataset API.
        """
        if not table.columns:
            raise ValueError(f"TableSpec.columns is empty for {table.schema_name}.{table.name}")

        dataset_id = self._generate_id()
        step_id = self._generate_id()

        category_id = self.folder_id or f"folder_{self._generate_id()}"

        dataset_name = self._format_table_name(table.name)
        role = self._resolve_role(table)
        # sql_query = f"SELECT * FROM {table.schema_name.upper()}.{table.name.upper()}"

        schema = (table.schema_name or "public").strip()
        name = table.name.strip()
        sql_query = f"SELECT * FROM {self._safe_sql_ident(schema)}.{self._safe_sql_ident(name)}"

        root = self._build_iro_element(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            category_id=category_id,
            role=role,
        )
        self._add_common_section(root, table.description or "")
        self._add_specific_section(root, step_id, sql_query, role, table.columns)

        xml_string = self._serialize_xml(root)
        self._validate_xml(xml_string)

        logger.debug(
            "kyvos_dataset_xml_payload_generated",
            table=table.name,
            payload_length=len(xml_string),
        )
        return xml_string

    # ── XML Construction ──────────────────────────────────────────────────────

    def _build_iro_element(
        self,
        dataset_id: str,
        dataset_name: str,
        category_id: str,
        role: str,
    ) -> ET.Element:
        """Build the root IRO element with attributes."""
        from datetime import datetime, timezone

        now_str = datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M:%S UTC")

        root = ET.Element("IRO")
        root.set("ID", dataset_id)
        root.set("NAME", dataset_name)
        root.set("TYPE", "QUERY")
        root.set("SUBTYPE", "REGISTERED")
        root.set("CATEGORY_ID", category_id)
        root.set("FOLDER_NAME", self.category_name)
        root.set("FOLDER_ID", category_id)
        root.set("ACCESSRIGHTS", "1")
        root.set("OWNERAPPID", "Admin")
        root.set("OWNERAPPNAME", "Admin")
        root.set("REPOSITDATE", now_str)
        root.set("LINKED_ENTITY_ID", "")
        root.set("ISPUBLIC", "true")
        root.set("ENTITY_STATE", "")
        root.set("DESIGN_SOURCE", "DESIGNER")
        return root

    def _add_common_section(self, root: ET.Element, description: str) -> None:
        """Add the COMMON section with DESC, TAGS, and COMPATIBILITY_VERSION."""
        common = ET.SubElement(root, "COMMON")

        desc_el = ET.SubElement(common, "DESC")
        desc_el.text = description

        tags_el = ET.SubElement(common, "TAGS")
        tags_el.text = ""

        compat = ET.SubElement(common, "COMPATIBILITY_VERSION")
        compat.text = "1"

    def _add_specific_section(
        self,
        root: ET.Element,
        step_id: str,
        sql_query: str,
        role: str,
        columns: list[ColumnSpec],
    ) -> None:
        """Add the SPECIFIC > QO > TRANSFORMATION > STEPS section."""
        specific = ET.SubElement(root, "SPECIFIC")
        qo = ET.SubElement(specific, "QO")
        qo.set("CACHED", "TRUE")
        qo.set("CONN_NAME", self.connection_name)
        qo.set("ROLE", role)
        qo.set("CONN_TYPE", self.connection_type)

        transformation = ET.SubElement(qo, "TRANSFORMATION")

        # Layout
        layout = ET.SubElement(transformation, "LAYOUT")
        layout.set("HSCROLLVALUE", "0")
        layout.set("VSCROLLVALUE", "0")
        layout.set("STEPS_PANEL_WIDTH", "")
        layout.set("TRANSFORMATION_PANEL_HEIGHT", "")
        layout.set("SELECTED_STEP_ID", "")

        # Steps
        steps = ET.SubElement(transformation, "STEPS")
        step = ET.SubElement(steps, "STEP")
        step.set("ID", step_id)
        step.set("NAME", "Fetch")
        step.set("TYPE", "FETCH")
        step.set("LEFT", "0")
        step.set("TOP", "0")

        ET.SubElement(step, "INPUT_STEPS")
        ET.SubElement(step, "OUTPUT_STEPS")

        step_info = ET.SubElement(step, "STEP_INFO")
        fetch_info = ET.SubElement(step_info, "FETCH_INFO")

        fetch = ET.SubElement(fetch_info, "FETCH")
        fetch.set("SOURCE", "HCATALOG")
        fetch.set("SOURCE_SUB_TYPE", "")
        fetch.set("CONN_NAME", self.connection_name)
        fetch.set("CONN_TYPE", "PARENT")
        fetch.set("LOOKUP_ENABLED", "false")
        fetch.set("IS_SORTED", "FALSE")
        fetch.set("INPUT_TYPE", "SQL")

        # Partition column details
        # partition = ET.SubElement(fetch, "SPARK_JDBC_DATA_PARTITION_COLUMN")
        # for tag_name in [
          #  "COLUMN_NAME", "TABLE_NAME", "METADATA_MODE",
           # "NUMBER_OF_PARTITIONS", "MIN_VALUE", "MAX_VALUE", "NUMBER_OF_RECORDS",
        #]:
         # el = ET.SubElement(partition, tag_name)
          #  el.text = "DEFAULT" if tag_name == "METADATA_MODE" else ""

        full_table = re.sub(r'"([^"]+)"', r'\1', sql_query).split("FROM ", 1)[-1].strip()

        partition = ET.SubElement(fetch, "SPARK_JDBC_DATA_PARTITION_COLUMN")

        ET.SubElement(partition, "COLUMN_NAME").text = ""
        ET.SubElement(partition, "TABLE_NAME").text = full_table
        ET.SubElement(partition, "METADATA_MODE").text = "DEFAULT"
        ET.SubElement(partition, "NUMBER_OF_PARTITIONS").text = ""
        ET.SubElement(partition, "MIN_VALUE").text = ""
        ET.SubElement(partition, "MAX_VALUE").text = ""
        ET.SubElement(partition, "NUMBER_OF_RECORDS").text = ""

        # Incremental identifier
        incr = ET.SubElement(fetch, "INCREMENTAL_IDENTIFIER")
        incr.set("COLUMN_NAME", "")

        # SQL source
        sql_source = ET.SubElement(fetch, "SQL_SOURCE")
        sql_el = ET.SubElement(sql_source, "SQL")
        sql_el.text = sql_query
        filepath_el = ET.SubElement(sql_source, "FILE_PATH")
        filepath_el.text = ""

        # Columns
        columns_el = ET.SubElement(fetch_info, "COLUMNS")
        for col in columns:
            self._add_column_element(columns_el, col)

    def _add_column_element(self, parent: ET.Element, col: ColumnSpec) -> None:
        """Add a single COLUMN element to the COLUMNS parent."""
        base_sql_type = col.data_type.split("(")[0].strip().upper()
        # Resolve multi-word aliases (e.g. DOUBLE PRECISION → DOUBLE) before map lookup
        base_sql_type = _KYVOS_TYPE_ALIAS.get(base_sql_type, base_sql_type)
        is_timestamp = base_sql_type == "TIMESTAMP"

        data_type_name, pig_data_type, data_sub_type, field_format_data_type = (
            SQL_TO_KYVOS_XML_MAP.get(base_sql_type, ("CHAR", "1", "", "1"))
        )

        col_el = ET.SubElement(parent, "COLUMN")
        col_el.set("DATATYPENAME", data_type_name)
        col_el.set("HIDDEN", "false")
        col_el.set("PIGDATATYPE", pig_data_type)
        col_el.set("DATASUBTYPENAME", data_sub_type)
        col_el.set("ORIGINAL_DATATYPENAME", "93" if is_timestamp else pig_data_type)
        col_el.set("SORT_ORDER", "")
        col_el.set("ROLE", "")
        col_el.set("SORT_TYPE", "")
        col_el.set("LOCALE", "")
        col_el.set("PRECISION", "")
        col_el.set("SCALE", "")

        col_name = col.name.strip()

        for tag_name in ["NAME", "UPDATED_NAME", "ORIGINAL_NAME"]:
            el = ET.SubElement(col_el, tag_name)
            el.text = col_name

        ff = ET.SubElement(col_el, "FIELDFORMAT")
        ff.set("USEDEFAULT", "")
        ff.text = "yyyy-mm-dd" if is_timestamp else ("yyyy-mm-dd" if data_type_name == "DATE" else "0")

        ffdtype = ET.SubElement(col_el, "FIELDDATAFORMATTYPE")
        ffdtype.text = field_format_data_type

        for tag_name in [
            "FULLY_QUALIFIED_NAME", "ORIGINAL_QUALIFIED_NAME", "DATABASE_TIMEZONE",
        ]:
            el = ET.SubElement(col_el, tag_name)
            el.text = ""

    # ── Serialization & Validation ────────────────────────────────────────────

    def _serialize_xml(self, root: ET.Element) -> str:
        """Serialize ElementTree to pretty-printed XML string."""
        rough = ET.tostring(root, encoding="unicode", xml_declaration=False)
        dom = minidom.parseString(rough)
        return dom.toprettyxml(indent="    ", encoding=None)

    def _validate_xml(self, xml_string: str) -> None:
        """Validate XML structure by parsing it.

        Raises:
            ET.ParseError: If XML is malformed.
        """
        try:
            ET.fromstring(xml_string)
        except ET.ParseError as exc:
            logger.error("kyvos_dataset_xml_validation_failed", error=str(exc))
            raise

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_role(self, table: TableSpec) -> str:
        """Resolve Kyvos dataset role from table metadata and naming convention."""
        table_name = (table.name or "").strip().lower()
        table_type = (table.table_type or "").strip().lower()

        if table_name.startswith("fact_") or table_name.startswith("fct_") or table_type == "fact":
            return "FACT"

        if table_name.startswith("dim_") or table_type == "dimension":
            return "DIMENSION"

        return "FACT"

    @staticmethod
    def _safe_sql_ident(name: str) -> str:
        """Quote a SQL identifier that contains spaces or non-snake-case chars."""
        if re.match(r'^[a-z][a-z0-9_]*$', name):
            return name
        return '"' + name.replace('"', '""') + '"'

    def _format_table_name(self, table_name: str) -> str:
        """Format table name for Kyvos (PascalCase)."""
        parts = table_name.split("_")
        return "".join(word.capitalize() for word in parts)

    def _generate_id(self) -> str:
        """Generate a unique ID for Kyvos entities."""
        import random
        timestamp = int(time.time() * 1000)
        random_part = random.randint(100000, 999999)
        return f"{timestamp}{random_part}"
