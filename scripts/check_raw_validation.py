"""Check raw validation response using KyvosService client directly."""
import json
import requests
from kyvos_sdk.config import KyvosConfig
from kyvos_sdk.client import KyvosService

config = KyvosConfig.from_env_file(".env.discover")
svc = KyvosService(config)
svc.ensure_authenticated()

sm_ids = {
    "Flow A": "843524F8-D5B3-F4B4-5E7F-B9F7526F42AF",
    "Flow B": "20DC8619-030B-0DB5-5B77-003E09CE7960",
}
folder_name = "awdw2019multidimensionalee_SModel"

for label, sm_id in sm_ids.items():
    print(f"\n{'='*60}")
    print(f"{label}: {sm_id}")
    print(f"{'='*60}")
    raw = svc.validate_semantic_model(smodel_id=sm_id, folder_name=folder_name)
    print(f"valid: {raw.get('valid')}")
    print(f"validation_status: {raw.get('validation_status')}")
    print(f"errors: {raw.get('errors')}")
    print(f"warnings: {raw.get('warnings')}")
    print(f"raw response (first 5000 chars):")
    raw_json = raw.get("raw", {})
    print(json.dumps(raw_json, indent=2)[:5000])
