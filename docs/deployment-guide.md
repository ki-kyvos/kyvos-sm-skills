# Kyvos Semantic Model Skills — Deployment & Getting Started Guide

This guide covers installing, configuring, and running the Kyvos semantic model skills on any machine using standard Python packaging — no source code checkout required.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [Verifying the Installation](#5-verifying-the-installation)
6. [Deploying an XMLA Model (CLI)](#6-deploying-an-xmla-model-cli)
7. [Deploying an XMLA Model (Claude Code)](#7-deploying-an-xmla-model-claude-code)
8. [Deploying an XMLA Model (Python Script)](#8-deploying-an-xmla-model-python-script)
9. [Available Skills](#9-available-skills)
10. [Enterprise Deployment Patterns](#10-enterprise-deployment-patterns)
11. [Troubleshooting](#11-troubleshooting)
12. [Upgrade Procedure](#12-upgrade-procedure)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     User Interface Layer                      │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ kyvos-skills │  │ Claude Code  │  │ Python Script    │  │
│  │ CLI          │  │ (IDE/CLI)    │  │ (skill_runner)   │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                   │             │
└─────────┼─────────────────┼───────────────────┼────────────┘
          │                 │                   │
          v                 v                   v
┌─────────────────────────────────────────────────────────────┐
│                    kyvos-sm-skills Package                   │
│                                                              │
│  ├── skill_runner.py    Programmatic pipeline executor       │
│  ├── contract_adapter.py  SDK compiler-backed adapters       │
│  ├── skills/*.md        Claude skill definitions             │
│  └── cli.py             kyvos-skills CLI entry point         │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          v
┌─────────────────────────────────────────────────────────────┐
│                    kyvos-sdk-python Package                  │
│                                                              │
│  ├── compiler.py        Pure compilers (XML/JSON payloads)   │
│  ├── provisioning.py    ProvisioningClient (entity CRUD)     │
│  ├── client.py          KyvosService (HTTP transport)        │
│  ├── config.py          KyvosConfig (env/file config)        │
│  ├── contracts/         Typed domain contracts               │
│  └── warehouse_registry  JDBC/driver profiles                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          v
┌─────────────────────────────────────────────────────────────┐
│                    kyvos-xmla-parser Package                 │
│                                                              │
│  └── xmla_parser.py     XMLA/PBIT file parser                │
└─────────────────────────────────────────────────────────────┘
```

**Three pip packages, no source code needed:**

| Package | Purpose | PyPI Name |
|---------|---------|-----------|
| Kyvos SDK | Compilers, provisioning client, transport, config | `kyvos-sdk-python` |
| Kyvos SM Skills | Skill definitions, contract adapters, CLI, runner | `kyvos-sm-skills` |
| Kyvos XMLA Parser | XMLA/PBIT file parsing | `kyvos-xmla-parser` |

---

## 2. System Requirements

### Operating System

- Linux (Ubuntu 22.04+, RHEL 9+) or macOS 12+
- Windows is supported via WSL2

### Python

- Python 3.11 or higher
- `pip` 24.0+
- `venv` module (standard library)

### Network Access

- Outbound HTTPS to the Kyvos server (e.g., `https://your-kyvos-instance:8443`)
- Outbound JDBC access to the source warehouse (PostgreSQL, Snowflake, etc.)

### Optional: Claude Code

- Node.js 18+ and npm — only required if using Claude Code for skill execution
- Install: `npm install -g @anthropic-ai/claude-code`

---

## 3. Installation

### 3.1 Create a Virtual Environment

```bash
mkdir -p /opt/kyvos-skills
cd /opt/kyvos-skills
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2 Install Packages

**Standard installation (JSON payload format, PostgreSQL warehouse):**

```bash
pip install "kyvos-sdk-python[env]" "kyvos-sm-skills[sdk]" kyvos-xmla-parser
```

**Full installation (all optional dependencies):**

```bash
pip install "kyvos-sdk-python[env]" "kyvos-sm-skills[all]" "kyvos-xmla-parser[contracts]"
```

**Pinned versions for reproducibility:**

```bash
pip install \
  "kyvos-sdk-python[env]>=0.6.0" \
  "kyvos-sm-skills[sdk]>=0.2.0" \
  "kyvos-xmla-parser>=0.2.0"
```

### 3.3 Private Package Index (Enterprise)

If packages are hosted on a private registry:

```bash
pip install \
  --index-url https://your-nexus-repo/repository/pypi-all/simple \
  --trusted-host your-nexus-repo \
  "kyvos-sdk-python[env]" "kyvos-sm-skills[sdk]" kyvos-xmla-parser
```

Or with a `requirements.txt`:

```text
# requirements.txt
kyvos-sdk-python[env]>=0.6.0
kyvos-sm-skills[sdk]>=0.2.0
kyvos-xmla-parser>=0.2.0
```

```bash
pip install -r requirements.txt
```

### 3.4 Air-Gapped Installation

On a machine with internet access:

```bash
pip download \
  "kyvos-sdk-python[env]" "kyvos-sm-skills[sdk]" kyvos-xmla-parser \
  -d ./kyvos-packages
```

Transfer the `kyvos-packages/` directory to the air-gapped machine, then:

```bash
pip install --no-index --find-links ./kyvos-packages \
  kyvos-sdk-python kyvos-sm-skills kyvos-xmla-parser
```

---

## 4. Configuration

### 4.1 Environment File

Create a `.env` file at a known location (e.g., `/opt/kyvos-skills/.env`):

```env
# ── Kyvos Server ──────────────────────────────────────────────
KYVOS_BASE_URL=https://your-kyvos-instance:8443/kyvos
KYVOS_USERNAME=admin
KYVOS_PASSWORD=your-kyvos-password
# OR use token-based auth:
# KYVOS_AUTH_TOKEN=your-session-token

# ── Source Warehouse ──────────────────────────────────────────
WAREHOUSE_TYPE=POSTGRES
WAREHOUSE_HOST=10.80.134.108
WAREHOUSE_PORT=45421
WAREHOUSE_DATABASE=demo
WAREHOUSE_USERNAME=postgres
WAREHOUSE_PASSWORD=your-db-password
WAREHOUSE_CONNECTION_NAME=pgconnection

# ── Workflow Settings ─────────────────────────────────────────
KYVOS_PAYLOAD_FORMAT=json
KYVOS_SKIP_HIDDEN_TABLES=true
```

### 4.2 Environment Variable Reference

#### Kyvos Server

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KYVOS_BASE_URL` | Yes | — | Kyvos REST API base URL (e.g., `https://host:8443/kyvos`) |
| `KYVOS_USERNAME` | Yes* | — | Kyvos username (for password auth) |
| `KYVOS_PASSWORD` | Yes* | — | Kyvos password (or set `KYVOS_PASSWORD_CMD` for command indirection) |
| `KYVOS_AUTH_TOKEN` | No | — | Pre-authenticated session token (alternative to username/password) |
| `KYVOS_TIMEOUT_SECONDS` | No | `60` | HTTP request timeout |
| `KYVOS_MAX_RETRIES` | No | `5` | Max retry attempts for transient failures |
| `KYVOS_RETRY_BACKOFF_FACTOR` | No | `2.0` | Exponential backoff multiplier |

*Either username+password or auth_token is required.

#### Source Warehouse

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WAREHOUSE_TYPE` | Yes | `POSTGRES` | One of: `POSTGRES`, `SNOWFLAKE`, `BIGQUERY`, `ORACLE`, `MSSQL`, `REDSHIFT` |
| `WAREHOUSE_HOST` | Yes | — | Warehouse hostname or IP |
| `WAREHOUSE_PORT` | No | Type-specific default | Warehouse port |
| `WAREHOUSE_DATABASE` | Yes | — | Database/schema name |
| `WAREHOUSE_USERNAME` | Yes | — | Warehouse username |
| `WAREHOUSE_PASSWORD` | Yes | — | Warehouse password (or `WAREHOUSE_PASSWORD_CMD`) |
| `WAREHOUSE_CONNECTION_NAME` | No | `warehouse_connection` | Name for the Kyvos connection entity |
| `WAREHOUSE_JDBC_URL` | No | Auto-generated | Override JDBC URL (bypasses registry) |
| `WAREHOUSE_DRIVER` | No | Auto-generated | Override JDBC driver class |
| `WAREHOUSE_DB_VERSION` | No | Type-specific default | Database version string |
| `WAREHOUSE_SCHEMA` | No | — | Schema for inspection (e.g., `public`, `dbo`) |

#### Workflow

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KYVOS_PAYLOAD_FORMAT` | No | `json` | Payload format: `json` or `xml` |
| `KYVOS_SKIP_HIDDEN_TABLES` | No | `true` | Skip hidden tables in XMLA parsing |
| `KYVOS_USE_PLAN_APPLY` | No | `false` | Enable plan/apply lifecycle |
| `KYVOS_FOLDER_NAME` | No | `Demo Automation` | Default folder name prefix |

### 4.3 Secure Credential Handling

**Option A: Command indirection (recommended for production)**

```env
KYVOS_PASSWORD_CMD=cat /run/secrets/kyvos_password
WAREHOUSE_PASSWORD_CMD=cat /run/secrets/warehouse_password
```

**Option B: Environment variables only (no .env file)**

```bash
export KYVOS_BASE_URL=https://your-kyvos-instance:8443/kyvos
export KYVOS_USERNAME=admin
export KYVOS_PASSWORD=$K8S_SECRET_KYVOS_PASSWORD
# ...
```

**Option C: Docker secrets / Kubernetes**

Mount secrets as files and use command indirection:

```yaml
# docker-compose.yml
services:
  kyvos-deployer:
    image: your-registry/kyvos-deployer:latest
    secrets:
      - kyvos_password
      - warehouse_password
    environment:
      KYVOS_PASSWORD_CMD: cat /run/secrets/kyvos_password
      WAREHOUSE_PASSWORD_CMD: cat /run/secrets/warehouse_password
```

---

## 5. Verifying the Installation

### 5.1 Check Package Imports

```bash
python -c "
from kyvos_sdk.provisioning import ProvisioningClient
from kyvos_sdk.client import KyvosService
from kyvos_sdk.config import KyvosConfig
from kyvos_sm_skills.contract_adapter import compile_dataset_artifact
from kyvos_sm_skills.skill_runner import run_deploy_from_xmla
from kyvos_xmla_parser.xmla_parser import parse_xmla
print('All imports OK')
"
```

### 5.2 Check CLI

```bash
kyvos-skills list
```

Expected output:

```
Available skills:
  convert-dax-to-mdx                       (2,946 bytes)
  deploy-from-pbit                         (13,219 bytes)
  deploy-from-xmla                         (24,048 bytes)
  design-measures                          (4,171 bytes)
  design-star-schema                       (13,974 bytes)
  discover-sm-from-warehouse               (19,269 bytes)
  generate-connection                      (2,282 bytes)
  generate-dataset                         (3,149 bytes)
  generate-drd                             (3,302 bytes)
  generate-semantic-model                  (4,615 bytes)
  generate-sm-from-intent                  (22,031 bytes)
  inspect-warehouse-schema                 (13,875 bytes)

Shared resources (1 files):
  _shared/sm-design-principles.md
```

### 5.3 Check Config Loading

```bash
python -c "
from kyvos_sdk.config import KyvosConfig
config = KyvosConfig.from_env_file('/opt/kyvos-skills/.env')
print(f'Kyvos URL: {config.base_url}')
print(f'Warehouse: {config.warehouse_type} @ {config.warehouse_host}:{config.warehouse_port}')
print(f'Payload format: {config.payload_format}')
"
```

### 5.4 Dry Run (Parse Only)

```bash
kyvos-skills deploy \
  --xmla-path /path/to/AdventureWorks.xmla \
  --env-file /opt/kyvos-skills/.env \
  --dry-run
```

This parses the XMLA file and prints the spec summary without making any API calls.

---

## 6. Deploying an XMLA Model (CLI)

The `kyvos-skills deploy` command runs the full 9-step deployment pipeline without Claude Code.

### 6.1 Basic Deploy

```bash
kyvos-skills deploy \
  --xmla-path /data/models/AdventureWorks.xmla \
  --env-file /opt/kyvos-skills/.env
```

### 6.2 With XML Format

```bash
kyvos-skills deploy \
  --xmla-path /data/models/AdventureWorks.xmla \
  --env-file /opt/kyvos-skills/.env \
  --payload-format xml
```

### 6.3 Dry Run (Parse + Compile Only)

```bash
kyvos-skills deploy \
  --xmla-path /data/models/AdventureWorks.xmla \
  --env-file /opt/kyvos-skills/.env \
  --dry-run
```

### 6.4 What Happens During Deployment

| Step | Action | API Calls |
|------|--------|-----------|
| 1 | Load config from `.env` | None |
| 2 | Parse XMLA file, derive entity names with timestamp | None |
| 3 | Initialize `KyvosService` and `ProvisioningClient` | Login |
| 4 | Create 3 folders (dataset, DRD, semantic model) | 3x POST |
| 5 | Create or update database connection | 1x PUT/POST |
| 6 | Create datasets, refresh columns, validate each | N x (POST + 2x GET) |
| 7 | Validate relationships, build DRD, create + validate DRD | 1x POST + 1x GET |
| 8 | Compile semantic model, create + validate SM | 1x POST + 1x GET |
| 9 | Report results | None |

### 6.5 Expected Output

```
✅ Deployment Successful
   XMLA model    : AdventureWorks
   Timestamp     : 072026_1014
   Tables parsed : 30
   Datasets      : 30
   Relationships : 48
   Measures      : 86
   Connection    : pgconnection
   DRD           : AdventureWorks_072026_1014 DRD (id=1589082727205820)
   Semantic Model: AdventureWorks_072026_1014
```

---

## 7. Deploying an XMLA Model (Claude Code)

Use Claude Code (CLI or Windsurf IDE) to execute the skill interactively. Claude reads the skill definition and runs the same pipeline, with the ability to adapt to errors and make decisions.

### 7.1 Export Skill Files

```bash
mkdir -p ~/kyvos-workspace && cd ~/kyvos-workspace
kyvos-skills export-skill deploy-from-xmla
```

This creates:

```
~/kyvos-workspace/
├── deploy-from-xmla.md
└── _shared/
    └── sm-design-principles.md
```

### 7.2 Add Your Files

```bash
cp /data/models/AdventureWorks.xmla ~/kyvos-workspace/
cp /opt/kyvos-skills/.env ~/kyvos-workspace/.env
```

### 7.3 Run with Claude Code CLI

```bash
cd ~/kyvos-workspace
claude
```

In the Claude Code session, type:

> Read the skill file at `deploy-from-xmla.md` in this directory. Then deploy the Adventure Works XMLA model at `AdventureWorks.xmla` to Kyvos. My `.env` is at `.env` in the current directory.

### 7.4 Run with Windsurf IDE

1. Open the `~/kyvos-workspace` folder in Windsurf
2. Open the Cascade chat panel
3. Type the same prompt as above

### 7.5 Non-Interactive Mode (Automation)

```bash
claude --print \
  "Read the skill file at deploy-from-xmla.md. Then deploy the Adventure Works XMLA model at AdventureWorks.xmla to Kyvos. My .env is at .env in the current directory."
```

---

## 8. Deploying an XMLA Model (Python Script)

For integration into existing pipelines, CI/CD, or custom orchestration:

```python
from kyvos_sm_skills.skill_runner import run_deploy_from_xmla

# Full deployment
exit_code = run_deploy_from_xmla(
    xmla_file_path="/data/models/AdventureWorks.xmla",
    env_file="/opt/kyvos-skills/.env",
)

# Dry run (parse only)
exit_code = run_deploy_from_xmla(
    xmla_file_path="/data/models/AdventureWorks.xmla",
    env_file="/opt/kyvos-skills/.env",
    dry_run=True,
)

# With XML format
exit_code = run_deploy_from_xmla(
    xmla_file_path="/data/models/AdventureWorks.xmla",
    env_file="/opt/kyvos-skills/.env",
    payload_format="xml",
)

if exit_code != 0:
    raise RuntimeError("Deployment failed")
```

### CI/CD Example (GitHub Actions)

```yaml
name: Deploy Semantic Model

on:
  workflow_dispatch:
    inputs:
      xmla-file:
        description: 'Path to XMLA file in repo'
        required: true
        default: 'models/AdventureWorks.xmla'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Kyvos packages
        run: |
          pip install "kyvos-sdk-python[env]" "kyvos-sm-skills[sdk]" kyvos-xmla-parser

      - name: Deploy semantic model
        env:
          KYVOS_BASE_URL: ${{ secrets.KYVOS_BASE_URL }}
          KYVOS_USERNAME: ${{ secrets.KYVOS_USERNAME }}
          KYVOS_PASSWORD: ${{ secrets.KYVOS_PASSWORD }}
          WAREHOUSE_TYPE: POSTGRES
          WAREHOUSE_HOST: ${{ secrets.WAREHOUSE_HOST }}
          WAREHOUSE_PORT: ${{ secrets.WAREHOUSE_PORT }}
          WAREHOUSE_DATABASE: ${{ secrets.WAREHOUSE_DATABASE }}
          WAREHOUSE_USERNAME: ${{ secrets.WAREHOUSE_USERNAME }}
          WAREHOUSE_PASSWORD: ${{ secrets.WAREHOUSE_PASSWORD }}
          WAREHOUSE_CONNECTION_NAME: pgconnection
          KYVOS_PAYLOAD_FORMAT: json
        run: |
          kyvos-skills deploy \
            --xmla-path ${{ inputs.xmla-file }} \
            --env-file /dev/null
```

---

## 9. Available Skills

### End-to-End Deployment Skills

| Skill | Input | Description |
|-------|-------|-------------|
| `deploy-from-xmla` | XMLA file + config | Full pipeline: parse → folders → connection → datasets → DRD → semantic model |
| `deploy-from-pbit` | PBIT file + config | Full pipeline from Power BI Template files |
| `discover-sm-from-warehouse` | Warehouse connection | Inspect warehouse schema → recommend + deploy semantic models |
| `generate-sm-from-intent` | Natural language intent | Generate sample data + deploy semantic model from a description |

### Component Generation Skills

| Skill | Input | Output |
|-------|-------|--------|
| `generate-connection` | DB connection params | Connection JSON/XML payload |
| `generate-dataset` | Table specification | Dataset JSON/XML payload |
| `generate-drd` | Relationships + dataset IDs | DRD JSON/XML payload |
| `generate-semantic-model` | Schema + measures + hierarchies | Semantic model JSON/XML payload |

### Design Skills

| Skill | Input | Output |
|-------|-------|--------|
| `design-star-schema` | Domain description | Star/snowflake schema specification |
| `design-measures` | Schema + domain | Measures with aggregation types |
| `inspect-warehouse-schema` | DB connection params | Schema summary + pattern detection |
| `convert-dax-to-mdx` | DAX expressions | MDX expressions |

### Exporting Skills for Claude Code

```bash
# Export a single skill
kyvos-skills export-skill deploy-from-xmla

# Export all skills to a directory
kyvos-skills export-skill --all -o ~/my-skills/

# Export to a specific location
kyvos-skills export-skill generate-semantic-model -o /shared/skills/
```

---

## 10. Enterprise Deployment Patterns

### 10.1 Docker Container

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "kyvos-sdk-python[env]>=0.6.0" \
    "kyvos-sm-skills[sdk]>=0.2.0" \
    "kyvos-xmla-parser>=0.2.0"

COPY . .

ENTRYPOINT ["kyvos-skills"]
CMD ["deploy", "--xmla-path", "/data/model.xmla", "--env-file", "/config/.env"]
```

Build and run:

```bash
docker build -t kyvos-deployer .

docker run --rm \
  -v /path/to/models:/data \
  -v /path/to/config:/config \
  kyvos-deployer \
  deploy --xmla-path /data/AdventureWorks.xmla --env-file /config/.env
```

### 10.2 Docker Compose

```yaml
version: "3.8"

services:
  kyvos-deployer:
    build: .
    volumes:
      - ./models:/data
      - ./config:/config
    environment:
      KYVOS_BASE_URL: https://kyvos.internal:8443/kyvos
      KYVOS_USERNAME: admin
      KYVOS_PASSWORD_CMD: cat /run/secrets/kyvos_password
      WAREHOUSE_TYPE: POSTGRES
      WAREHOUSE_HOST: warehouse.internal
      WAREHOUSE_PORT: "5432"
      WAREHOUSE_DATABASE: analytics
      WAREHOUSE_USERNAME: readonly
      WAREHOUSE_PASSWORD_CMD: cat /run/secrets/warehouse_password
      WAREHOUSE_CONNECTION_NAME: pgconnection
      KYVOS_PAYLOAD_FORMAT: json
    secrets:
      - kyvos_password
      - warehouse_password
    command: deploy --xmla-path /data/AdventureWorks.xmla --env-file /dev/null

secrets:
  kyvos_password:
    file: ./secrets/kyvos_password.txt
  warehouse_password:
    file: ./secrets/warehouse_password.txt
```

### 10.3 Kubernetes Job

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: kyvos-sm-deploy
spec:
  template:
    spec:
      containers:
      - name: deployer
        image: your-registry/kyvos-deployer:latest
        command: ["kyvos-skills", "deploy"]
        args:
          - --xmla-path
          - /data/AdventureWorks.xmla
          - --env-file
          - /dev/null
        envFrom:
        - secretRef:
            name: kyvos-secrets
        volumeMounts:
        - name: model-data
          mountPath: /data
      volumes:
      - name: model-data
        configMap:
          name: xmla-models
      restartPolicy: OnFailure
  backoffLimit: 2
```

### 10.4 CI/CD with Pre-Built Image

```bash
# Pull the pre-built image
docker pull your-registry/kyvos-deployer:0.2.0

# Run deployment
docker run --rm \
  --env-file /opt/kyvos-skills/.env \
  -v /data/models:/data \
  your-registry/kyvos-deployer:0.2.0 \
  deploy --xmla-path /data/AdventureWorks.xmla --env-file /dev/null
```

---

## 11. Troubleshooting

### Installation Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| `ModuleNotFoundError: No module named 'kyvos_sdk'` | Virtual environment not activated | Run `source .venv/bin/activate` |
| `ModuleNotFoundError: No module named 'dotenv'` | Missing `[env]` extra | `pip install "kyvos-sdk-python[env]"` |
| `command not found: kyvos-skills` | Package not installed or venv not active | Reinstall and activate venv |
| `ImportError: kyvos-sdk-python not installed` | Missing `[sdk]` extra | `pip install "kyvos-sm-skills[sdk]"` |

### Configuration Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| `ValueError: Unknown WAREHOUSE_TYPE` | Unsupported warehouse type | Use one of: POSTGRES, SNOWFLAKE, BIGQUERY, ORACLE, MSSQL, REDSHIFT |
| `KyvosServiceError: Authentication failed` | Wrong credentials | Check `KYVOS_BASE_URL`, `KYVOS_USERNAME`, `KYVOS_PASSWORD` |
| `Connection refused` | Network/firewall | Verify outbound access to Kyvos server and warehouse |

### Deployment Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| `Dataset creation failed: Parameter missing: datasetName` | Outdated SDK version | Upgrade: `pip install --upgrade kyvos-sdk-python` |
| `DRD validation failed` | Datasets not fully processed | Retry logic handles this (3 attempts, 5s delay). If still failing, check dataset validation status in Kyvos UI |
| `NO_MEASURES_PLACED` | Measure source_dataset names don't match dataset names after alias remapping | Check XMLA file for measure table references; ensure fact tables are detected |
| `EntityRef id must be non-empty` | Server didn't return entity ID | Fallback lookup by name is built-in; if still failing, check Kyvos server logs |
| `Folder creation failed: already exists` | Folder name collision | Each run uses a timestamp suffix; if collision occurs, wait a minute and retry |

### Getting Help

```bash
# Check installed versions
pip show kyvos-sdk-python kyvos-sm-skills kyvos-xmla-parser

# Verify config without deploying
python -c "from kyvos_sdk.config import KyvosConfig; c = KyvosConfig.from_env_file('.env'); print(c)"

# Dry run to verify XMLA parsing
kyvos-skills deploy --xmla-path model.xmla --env-file .env --dry-run
```

---

## 12. Upgrade Procedure

### In-Place Upgrade

```bash
source /opt/kyvos-skills/.venv/bin/activate
pip install --upgrade \
  "kyvos-sdk-python[env]" \
  "kyvos-sm-skills[sdk]" \
  kyvos-xmla-parser

# Verify
kyvos-skills list
python -c "import kyvos_sdk; print(kyvos_sdk.__version__)"
```

### Pinned Upgrade (Reproducible)

```bash
# Update requirements.txt with new versions, then:
pip install -r requirements.txt --upgrade
```

### Rollback

```bash
pip install \
  "kyvos-sdk-python[env]==0.5.0" \
  "kyvos-sm-skills[sdk]==0.1.0" \
  "kyvos-xmla-parser==0.1.0"
```

### Version Compatibility Matrix

| kyvos-sm-skills | kyvos-sdk-python | kyvos-xmla-parser | Notes |
|-----------------|-------------------|---------------------|-------|
| 0.2.0 | 0.6.0 | 0.2.0 | Current — bundled skills, CLI, skill_runner |
| 0.1.0 | 0.4.0–0.5.0 | 0.1.0 | Legacy — no CLI, no bundled skills |
