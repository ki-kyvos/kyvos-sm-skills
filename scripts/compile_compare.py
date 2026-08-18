"""Compile both Flow A and Flow B SM designs and compare the JSON structures."""
import json
from kyvos_sm_skills.contract_adapter import compile_smodel_artifact
from kyvos_sm_skills.spec_builder import build_spec_from_recommendation

# We need warehouse tables - let's load from the schema inspection output
# or build a minimal version from the SM design
import os

def load_warehouse_tables():
    """Load warehouse tables from schema inspection output if available."""
    # Try to find the schema file
    for path in [
        "samples/output/flow-a-warehouse-schema.json",
        "samples/output/warehouse-schema.json",
    ]:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    return None


def build_minimal_wh_tables(sm_rec):
    """Build minimal warehouse table dicts from SM design for spec building."""
    tables = []
    for t in sm_rec.get("tables", []):
        if isinstance(t, str):
            tables.append({
                "name": t,
                "schema": "public",
                "columns": [],
                "estimated_table_type": "",
                "outgoing_fk_count": 0,
                "incoming_fk_count": 0,
            })
        elif isinstance(t, dict):
            tables.append(t)
    return tables


for label, path in [
    ("Flow A", "samples/output/flow-a-intent-file-sm-design.json"),
    ("Flow B", "samples/output/flow-b-generate-intent-sm-design.json"),
]:
    with open(path) as f:
        design = json.load(f)

    sm_rec = design["recommended_sms"][0]
    wh_tables = build_minimal_wh_tables(sm_rec)

    try:
        spec = build_spec_from_recommendation(sm_rec, wh_tables)
        artifact = compile_smodel_artifact(
            spec.semantic_model,
            connection_name="pgconnection",
            folder_id="folder_smodel",
            folder_name="awdw2019multidimensionalee_SModel",
            dataset_name_to_id={},
            relationships=spec.semantic_model.relationships,
            dataset_aliases={},
            fact_dataset_names=set(),
            fmt="json",
        )

        # Write the compiled JSON
        out_path = f"samples/output/{label.lower().replace(' ', '-')}-compiled-sm.json"
        with open(out_path, "w") as f:
            if isinstance(artifact.payload, str):
                f.write(artifact.payload)
            else:
                json.dump(artifact.payload, f, indent=2)
        print(f"{label}: compiled JSON written to {out_path}")
    except Exception as e:
        print(f"{label}: compilation error: {e}")
        import traceback
        traceback.print_exc()
