# Phase 0 — Decisions & Groundwork

> **Status:** Complete
> **Exit criteria:** Decisions documented; no open questions blocking Phase 1 code.

---

## 1. Dependency Direction: JDBC / Driver Overrides

**Decision:** Generators and compilers accept explicit `jdbc_url_override` / `driver_override` parameters. The SDK registry (Phase 1) resolves warehouse-type → JDBC template + driver, then passes the resolved values down. No generator or compiler imports the registry.

**Implemented in:**

| Layer | File | Change |
|-------|------|--------|
| Generators (sm-skills) | `generators/connection_xml.py` | Added `jdbc_url_override`, `driver_override` params |
| Generators (sm-skills) | `generators/connection_json.py` | Added `jdbc_url_override`, `driver_override` params |
| Contract adapter (sm-skills) | `contract_adapter.py` | `compile_connection_artifact()` passes overrides to `compile_connection()` |
| Compiler (SDK) | `compiler.py` | `compile_connection()` + `_compile_connection_xml()` + `_compile_connection_json()` accept overrides |
| Client (SDK) | `client.py` | `create_connection_xml()`, `update_connection_xml()`, `create_connection_json()`, `create_or_update_connection_xml()`, `create_or_update_connection_json()` accept overrides |
| Protocol (SDK) | `protocol.py` | Abstract signatures updated |
| Provisioning (SDK) | `provisioning.py` | `ProvisioningClient.create_connection()` accepts overrides |

**Dependency rule:** `kyvos-sm-skills` → `kyvos-sdk-python` (optional via `[sdk]` extra). No `kyvos_sdk` imports in `kyvos-sm-skills` core (only in `contract_adapter.py` try/except blocks). No `kyvos_sm_skills` imports in `kyvos-sdk-python` core (only in `client.py` for legacy generator fallback).

---

## 2. Secrets Approach: `*_PASSWORD_CMD` Indirection

**Decision:** All secrets support command indirection via `*_PASSWORD_CMD` environment variables. When set, the command is executed via `subprocess.run()` and stdout is used as the secret value. This wins over plaintext `*_PASSWORD` vars.

**Supported indirection vars:**
- `KYVOS_PASSWORD_CMD` — resolves `KYVOS_PASSWORD`
- `WAREHOUSE_PASSWORD_CMD` — resolves `WAREHOUSE_PASSWORD`

**Implemented in:**

| File | Change |
|------|--------|
| `kyvos_sdk/config.py` | `_resolve_secret()` helper, `from_env()` uses it for both passwords |
| `kyvos_sdk/config.py` | `from_env_file()` classmethod added (requires `python-dotenv`, installed via `[env]` extra) |
| `pyproject.toml` | Added `env = ["python-dotenv>=1.0.0"]` optional dependency |
| `.env.example` | Created with all config vars, security guidance, and `*_PASSWORD_CMD` examples |

**`.env` handling guidance:**
- `chmod 600 .env` — restrict file permissions to owner only
- `.env` is in `.gitignore` — never committed
- Prefer `*_PASSWORD_CMD` for enterprise deployments (Vault, `security`, `gcloud secrets`)
- `from_env_file()` raises `ImportError` if `python-dotenv` is missing — never silently skips

---

## 3. `OperationResult` Contract: Entity ID Standardization

**Decision:** The created/updated entity ID is carried in `entity_refs[0].id` (type: `EntityRef`). This is already the pattern used by all `ProvisioningClient` methods.

**Convenience properties added:**

| Property | Returns | Description |
|----------|---------|-------------|
| `primary_entity_id` | `str \| None` | `entity_refs[0].id` or `None` |
| `primary_entity_name` | `str \| None` | `entity_refs[0].name` or `None` |
| `succeeded` | `bool` | `status == OperationStatus.SUCCEEDED` |

**File:** `kyvos_sdk/contracts/results.py`

**Contract:** All provisioning methods MUST populate `entity_refs` with at least one `EntityRef` on success. Failed/unsupported operations MAY have an empty list (properties return `None`).

---

## 4. Version Matrix

**Current versions (as of Phase 0):**

| Package | Version | Role |
|---------|---------|------|
| `kyvos-sdk-python` | `0.6.0` | Core SDK: contracts, compilers, transport, config |
| `kyvos-sm-skills` | `0.2.0` | Claude skills + legacy generators + contract adapters |
| `kyvos-xmla-parser` | `0.2.0` | PBIT/XMLA parsing → `DomainDemoSpec` |
| `kyvos-data-gen` | `0.2.0` | Synthetic data generation |

**Minimum-version pins (in optional dependencies):**

| Package | Extra | Pins |
|---------|-------|------|
| `kyvos-sm-skills` | `[sdk]` | `kyvos-sdk-python>=0.6.0` |
| `kyvos-sm-skills` | `[parser]` | `kyvos-xmla-parser>=0.2.0` |
| `kyvos-sm-skills` | `[datagen]` | `kyvos-data-gen>=0.2.0` |
| `kyvos-sm-skills` | `[all]` | All three above |
| `kyvos-xmla-parser` | `[contracts]` | `kyvos-sdk-python>=0.6.0` |
| `kyvos-data-gen` | `[contracts]` | `kyvos-sdk-python>=0.6.0` |
| `kyvos-sdk-python` | `[env]` | `python-dotenv>=1.0.0` |

**Rule:** When any package bumps its minor version, all downstream pins must be updated. The `[all]` extra in `kyvos-sm-skills` is the single-source workflow installer.
