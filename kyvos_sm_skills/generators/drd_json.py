"""Generate Kyvos-compatible Simplified JSON for DRD creation (Kyvos 2026.5+).

Emits a JSON dict suitable for ``POST /rest/v2/dataset-relationships/`` with
``Content-Type: application/x-www-form-urlencoded`` and the JSON payload
sent as a form-encoded ``json`` parameter.
"""

from __future__ import annotations

from typing import Any

from kyvos_sm_skills.generators.drd_xml import SimpleRel
import logging

logger = logging.getLogger(__name__)


class DrdJsonGenerator:
    """Generate Kyvos DRD Simplified JSON definitions from dataset relationships."""

    def __init__(
        self,
        drd_folder_id: str,
        drd_folder_name: str,
        dataset_folder_id: str = "",
        dataset_folder_name: str = "",
    ) -> None:
        self.drd_folder_id = drd_folder_id
        self.drd_folder_name = drd_folder_name
        self.dataset_folder_id = dataset_folder_id
        self.dataset_folder_name = dataset_folder_name

    def generate(
        self,
        *,
        drd_name: str,
        dataset_name_to_id: dict[str, str],
        relationships: list[SimpleRel],
        dataset_aliases: dict[str, str],
        fact_dataset_names: set[str] | None = None,
    ) -> dict[str, Any]:
        """Generate Simplified JSON payload for DRD creation.

        Args:
            drd_name: Name of the DRD.
            dataset_name_to_id: Mapping from Kyvos dataset name → dataset ID.
            relationships: List of semantic relationships.
            dataset_aliases: Mapping from semantic display name → Kyvos dataset name.
            fact_dataset_names: Optional set of fact dataset Kyvos names.

        Returns:
            Dict matching the Kyvos 2026.5+ Simplified DRD JSON shape.
        """
        if not relationships:
            raise ValueError("relationships is empty; cannot generate DRD JSON")

        fact_set = fact_dataset_names or set()

        # Collect all datasets participating in relationships
        all_ds_names: set[str] = set()
        for rel in relationships:
            left_kyvos = dataset_aliases.get(rel.left_dataset, rel.left_dataset)
            right_kyvos = dataset_aliases.get(rel.right_dataset, rel.right_dataset)
            all_ds_names.add(left_kyvos)
            all_ds_names.add(right_kyvos)

        # Build datasets array
        datasets_json: list[dict[str, Any]] = []
        for ds_name in sorted(all_ds_names):
            ds_id = dataset_name_to_id.get(ds_name, "")
            datasets_json.append({
                "datasetName": ds_name,
                "datasetId": ds_id,
                "datasetFolderName": self.dataset_folder_name,
                "datasetFolderId": self.dataset_folder_id,
                "alias": ds_name,
                "isFact": ds_name in fact_set,
            })

        # Build relations array
        relations_json: list[dict[str, Any]] = []
        for rel in relationships:
            left_kyvos = dataset_aliases.get(rel.left_dataset, rel.left_dataset)
            right_kyvos = dataset_aliases.get(rel.right_dataset, rel.right_dataset)

            rel_type = rel.relationship_type.upper().replace("-", "_")
            if rel_type not in ("ONE_TO_MANY", "MANY_TO_ONE", "ONE_TO_ONE", "MANY_TO_MANY"):
                rel_type = "ONE_TO_MANY"

            relations_json.append({
                "firstDataset": left_kyvos,
                "secondDataset": right_kyvos,
                "joinType": rel_type,
                "joinKeys": [
                    {
                        "node1Key": {
                            "id": "",
                            "type": "",
                            "content": rel.left_column,
                        },
                        "node1SecondaryKey": {
                            "id": "",
                            "type": "",
                        },
                        "operator": "EQUAL_TO",
                        "node2SecondaryKey": {
                            "id": "",
                            "type": "",
                        },
                        "node2Key": {
                            "id": "",
                            "type": "",
                            "content": rel.right_column,
                        },
                    }
                ],
            })

        return {
            "relationshipName": drd_name,
            "relationshipId": "",
            "relationshipFolderName": self.drd_folder_name,
            "relationshipFolderId": self.drd_folder_id,
            "details": {
                "datasets": datasets_json,
                "relations": relations_json,
            },
        }
