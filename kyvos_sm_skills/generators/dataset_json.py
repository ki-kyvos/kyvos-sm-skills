"""Generate Kyvos-compatible Simplified JSON for SQL-based dataset creation.

Uses the Kyvos 2026.5+ Simplified JSON format for ``POST /rest/v2/datasets``
with ``Content-Type: application/x-www-form-urlencoded`` and the JSON payload
sent as a form-encoded ``json`` parameter. The server auto-discovers columns
from the SQL query — no need to send column definitions.

Reference:
https://docs.support.kyvosinsights.com/wiki/spaces/KD20265/pages/1102839809
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from kyvos_sm_skills.models import TableSpec
import logging

logger = logging.getLogger(__name__)


class DatasetJsonGenerator:
    """Generate Kyvos dataset Simplified JSON definitions from table schemas."""

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

    def generate_json_payload(self, table: TableSpec) -> dict[str, Any]:
        """Generate Simplified JSON payload dict for SQL-based dataset creation.

        Args:
            table: Table specification with schema and columns.

        Returns:
            Dict matching the Kyvos 2026.5+ Simplified JSON shape.
        """
        if not table.columns:
            raise ValueError("columns is empty; cannot generate dataset JSON")

        dataset_id = self._generate_id()
        category_id = self.folder_id or f"folder_{self._generate_id()}"

        dataset_name = self._format_table_name(table.name)

        schema = (table.schema_name or "public").strip()
        name = table.name.strip()
        sql_query = f"SELECT * FROM {self._safe_sql_ident(schema)}.{self._safe_sql_ident(name)}"

        payload: dict[str, Any] = {
            "datasetName": dataset_name,
            "datasetId": dataset_id,
            "folderName": self.category_name,
            "folderId": category_id,
            "datasetDetails": {
                "connectionName": self.connection_name,
                "inputType": "SQL",
                "sqlDetails": {
                    "sql": sql_query,
                },
                "parameters": [],
                "partitionDetails": {
                    "metadataMode": "AUTO",
                    "columnName": "",
                    "tableName": "",
                    "tableRecordCount": "",
                    "numberOfPartitions": "",
                    "columnMaxValue": "",
                    "columnMinValue": "",
                },
            },
        }

        logger.debug(
            "kyvos_dataset_json_payload_generated",
            table=table.name,
            dataset_name=dataset_name,
        )
        return payload

    @staticmethod
    def _safe_sql_ident(name: str) -> str:
        if re.match(r'^[a-z][a-z0-9_]*$', name):
            return name
        return '"' + name.replace('"', '""') + '"'

    def _format_table_name(self, table_name: str) -> str:
        parts = table_name.split("_")
        return "".join(word.capitalize() for word in parts)

    def _generate_id(self) -> str:
        return uuid.uuid4().hex[:16]
