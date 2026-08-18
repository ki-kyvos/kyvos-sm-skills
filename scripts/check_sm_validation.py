"""Check validation status of SMs in the awdw2019multidimensionalee_SModel folder."""
from kyvos_sdk.config import KyvosConfig
from kyvos_sdk.client import KyvosService
from kyvos_sdk.inspection import InspectionClient

config = KyvosConfig.from_env_file(".env.discover")
svc = KyvosService(config)
svc.ensure_authenticated()
insp = InspectionClient(svc)

folder_name = "awdw2019multidimensionalee_SModel"
result = insp.list_smodels_in_folder(folder_name)
print(f"SM folder: {folder_name}")
print(f"Total SMs in folder: {len(result.entity_refs)}")
print()

# Our two newly deployed SMs
target_ids = {
    "8FC83836-8094-08B3-5068-F7E21BE5072C": "Flow A",
    "3EED8902-CEB4-CF62-9BC6-64BE2409571A": "Flow B",
}

if result.succeeded:
    for ref in result.entity_refs:
        label = target_ids.get(ref.id, "OLD")
        print(f"  [{label}] {ref.name} (id={ref.id})")
        try:
            val = insp.validate_semantic_model(ref.id, ref.name, folder_name)
            v = val.model_dump()
            valid = v.get("valid")
            diagnostics = v.get("diagnostics", [])
            print(f"    valid={valid} diagnostics={len(diagnostics)}")
            for d in diagnostics:
                print(f"    {d}")
        except Exception as e:
            print(f"    ERROR during validation: {e}")
        print()
else:
    print(f"  Failed to list SMs: {result.diagnostics}")
