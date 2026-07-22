"""Retrieve SM JSON from Kyvos server using raw API."""
import json
import requests
from kyvos_sdk.config import KyvosConfig
from kyvos_sdk.client import KyvosService

config = KyvosConfig.from_env_file(".env.discover")
svc = KyvosService(config)
svc.ensure_authenticated()

sm_ids = {
    "flow-a": "843524F8-D5B3-F4B4-5E7F-B9F7526F42AF",
    "flow-b": "20DC8619-030B-0DB5-5B77-003E09CE7960",
}
folder_name = "awdw2019multidimensionalee_SModel"

for label, sm_id in sm_ids.items():
    print(f"\n{'='*60}")
    print(f"{label}: {sm_id}")
    print(f"{'='*60}")
    for endpoint in [
        f"/rest/v2/semantic-models/{sm_id}?by=id&folderName={folder_name}",
        f"/rest/v2/semantic-models/{sm_id}?by=id",
        f"/rest/smodels/{sm_id}",
    ]:
        url = f"{config.base_url.rstrip('/')}{endpoint}"
        resp = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "sessionid": svc._client.headers.get("sessionid", ""),
            },
            verify=False,
        )
        print(f"  GET {endpoint} -> {resp.status_code}")
        if resp.status_code == 200 and "json" in resp.headers.get("content-type", "").lower():
            data = resp.json()
            out_path = f"samples/output/{label}-deployed-sm.json"
            with open(out_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"  Saved to {out_path}")
            if isinstance(data, dict):
                print(f"  Top-level keys: {list(data.keys())[:15]}")
            break
        elif resp.status_code == 200:
            print(f"  Content-Type: {resp.headers.get('content-type', '?')}")
            print(f"  Response (first 200 chars): {resp.text[:200]}")
