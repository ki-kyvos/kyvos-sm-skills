# AdventureWorks Testing Flows and Claude Cowork Usage

## Purpose

This guide provides three end-to-end AdventureWorks examples for the `discover` command and explains how to use the packaged Kyvos skills in Claude Cowork without cloning or exposing this repository.

The examples use the AdventureWorks schema `awdw2019multidimensionalee`. Substitute your own schema, input files, and approved model designs where appropriate.

## Prerequisites

Install the published packages in the environment that will run the skills. Source code is not required.

```bash
python3 -m venv /opt/kyvos-skills/.venv
source /opt/kyvos-skills/.venv/bin/activate
pip install "kyvos-sdk-python[env]>=0.6.0" "kyvos-sm-skills[sdk,anthropic]>=1.0.0"
```

For Azure OpenAI rather than Anthropic, install `openai`, set `LLM_PROVIDER=azure_openai`, and configure `AZURE_OPENAI_API_KEY` (or `LLM_API_KEY`), `AZURE_OPENAI_ENDPOINT` (or `AZURE_ENDPOINT`), `AZURE_DEPLOYMENT_NAME`, and optionally `AZURE_API_VERSION`. The `discover` flow selects the provider using `LLM_PROVIDER`; Anthropic is the default.

Create a protected environment file, for example `/opt/kyvos-config/adventureworks.env`. It must contain the Kyvos server credentials and warehouse connection settings required by `kyvos-sdk-python`. See [Deployment & Getting Started Guide](deployment-guide.md#4-configuration) for the complete environment-variable reference.

For Anthropic-backed flows, make `ANTHROPIC_API_KEY` available only to the runtime environment. Do not place API keys, Kyvos passwords, or warehouse passwords in intent files, design JSON, or Claude chat messages.

Prepare source-independent input assets in a controlled directory:

```text
/opt/kyvos-input/
  AdventureWorks.xmla
  adventureworks-intent.txt
```

`AdventureWorks.xmla` is the exported AdventureWorks Analysis Services model. Store it in your approved artifact repository. The package does not require repository sample files to execute any flow.

## Safety Model

- `--dry-run` parses and compiles XMLA in Flow 1, or inspects the warehouse and builds the model specification in Flows 2 and 3. It does not create Kyvos entities.
- `--cleanup-dry-run` lists entities that a deployment cleanup would remove. Cleanup is enabled by default for a non-dry-run deployment.
- `--auto-approve` bypasses the XMLA cleanup confirmation in Flow 1 and the semantic-model approval gate in Flows 2 and 3. Use it only for an approved input or an automated test environment.
- `--sm-folder-suffix` isolates dataset, DRD, and semantic-model folders for each flow. Use a distinct suffix for every concurrent or comparison run.

Run the dry-run command before the corresponding deployment command. For live testing, review cleanup output and use a dedicated test schema and test folders.

## Flow 1: XMLA File Deployment

Use this flow to deploy an exported AdventureWorks XMLA model. It does not inspect the warehouse or call an LLM; it parses the XMLA model, compiles Kyvos artifacts, and deploys them.

### Dry run

```bash
kyvos-skills deploy \
  --xmla-path /opt/kyvos-input/AdventureWorks.xmla \
  --env-file /opt/kyvos-config/adventureworks.env \
  --payload-format json \
  --sm-folder-suffix A \
  --cleanup-dry-run \
  --dry-run
```

### Deploy

```bash
kyvos-skills deploy \
  --xmla-path /opt/kyvos-input/AdventureWorks.xmla \
  --env-file /opt/kyvos-config/adventureworks.env \
  --payload-format json \
  --sm-folder-suffix A \
  --auto-approve
```

### What it tests

1. XMLA parsing and semantic-model import.
2. Dataset, DRD, and semantic-model compilation.
3. Kyvos deployment and validation.
4. Folder isolation for the XMLA deployment.

## Flow 2: Generate Intent from the Warehouse

Use this flow to test automatic intent generation. The flow first inspects AdventureWorks, asks the configured LLM to produce an analytics intent, saves that intent, then uses it to generate and deploy a semantic model.

### Dry run

```bash
kyvos-skills discover \
  --env-file /opt/kyvos-config/adventureworks.env \
  --generate-intent \
  --intent-output /opt/kyvos-output/adventureworks-generated-intent.txt \
  --domain adventure_works \
  --schema awdw2019multidimensionalee \
  --payload-format json \
  --sm-folder-suffix B \
  --no-web-research \
  --cleanup-dry-run \
  --dry-run
```

### Deploy

```bash
kyvos-skills discover \
  --env-file /opt/kyvos-config/adventureworks.env \
  --generate-intent \
  --intent-output /opt/kyvos-output/adventureworks-generated-intent.txt \
  --domain adventure_works \
  --schema awdw2019multidimensionalee \
  --payload-format json \
  --sm-folder-suffix B \
  --auto-approve
```

Remove `--no-web-research` only when your data-governance policy permits the LLM research step to use externally available information. It does not change warehouse access; the warehouse is always inspected locally through the configured connection.

### What it tests

1. Schema-aware intent generation.
2. LLM semantic-model design from the generated intent.
3. Schema validation, build, deployment, and Kyvos validation.
4. Persisting the generated intent as a reusable, reviewable artifact.

## Flow 3: Direct User Intent

Use this flow when an analyst or Claude Cowork provides a business request directly. It does not need a pre-approved design or a generated intent file. The following command reads the request from a user-managed text file so long prompts do not need to be placed on the command line.

Example `adventureworks-intent.txt` content:

```text
Build an AdventureWorks multifact sales and financial semantic model. Include
Internet Sales, Reseller Sales, Financial Reporting, conformed dimensions,
product and sales-territory hierarchies, sales targets or quotas where valid,
and Kyvos MDX measures for revenue, cost, margin, and variance analysis.
```

### Dry run

```bash
kyvos-skills discover \
  --env-file /opt/kyvos-config/adventureworks.env \
  --user-intent "$(cat /opt/kyvos-input/adventureworks-intent.txt)" \
  --domain adventure_works \
  --schema awdw2019multidimensionalee \
  --payload-format json \
  --sm-folder-suffix U \
  --no-web-research \
  --cleanup-dry-run \
  --dry-run
```

### Deploy

```bash
kyvos-skills discover \
  --env-file /opt/kyvos-config/adventureworks.env \
  --user-intent "$(cat /opt/kyvos-input/adventureworks-intent.txt)" \
  --domain adventure_works \
  --schema awdw2019multidimensionalee \
  --payload-format json \
  --sm-folder-suffix U \
  --auto-approve
```

### What it tests

1. Natural-language-to-semantic-model design.
2. LLM output recovery and validation against the inspected warehouse schema.
3. Relationship connectivity rules, including valid fact-to-bridge-to-dimension paths.
4. Live Kyvos semantic-model validation.

## Flow 1 `deploy` Parameter Reference

| Parameter | Default | Description |
|---|---:|---|
| `--xmla-path PATH` | required | Path to the exported AdventureWorks XMLA file. |
| `--env-file PATH` | `.env` | Environment file containing Kyvos and warehouse configuration. |
| `--payload-format {json,xml}` | configuration value | Overrides the configured Kyvos payload format for this run. |
| `--dry-run` | `false` | Parses and compiles the XMLA model without Kyvos create, delete, or validation calls. |
| `--cleanup-dry-run` | `false` | Lists matching entities that a live deployment would remove without deleting them. |
| `--auto-approve` | `false` | Skips the interactive cleanup confirmation gate. Use only for an approved test or automation run. |
| `--sm-folder-suffix TEXT` | empty | Suffix used to isolate all dataset, DRD, and semantic-model folders. Use `A` for this example. |

## Flow 2 and Flow 3 `discover` Parameter Reference

| Parameter | Default | Description |
|---|---:|---|
| `--env-file PATH` | `.env` | Environment file containing Kyvos and warehouse configuration. |
| `--sm-design PATH` | none | Approved semantic-model design JSON. Selects the deterministic, pre-approved design mode. Mutually exclusive in practice with an intent-producing mode. |
| `--user-intent TEXT` | none | Natural-language analytics request. Selects LLM semantic-model design mode. |
| `--generate-intent` | `false` | Inspects the schema and creates an intent with the configured LLM. This generated intent replaces `--user-intent`. |
| `--intent-output PATH` | `intent_<domain-or-auto>.txt` | Where `--generate-intent` saves the generated intent artifact. |
| `--domain NAME` | none | Domain hint, for example `adventure_works`. It guides intent and model design. |
| `--no-web-research` | `false` | Disables optional web research for LLM model design. Use for restricted environments. |
| `--schema NAME` | warehouse-specific | Warehouse schema to inspect. Use `awdw2019multidimensionalee` for these examples. |
| `--max-tables INTEGER` | `500` | Maximum number of tables allowed during inspection. The command fails if the limit is exceeded. |
| `--payload-format {json,xml}` | configuration value | Overrides the configured Kyvos payload format for this run. JSON is recommended for the current compiler path. |
| `--dry-run` | `false` | Inspects and builds the model specification without Kyvos create, delete, or validation calls. |
| `--cleanup-dry-run` | `false` | Lists matching existing entities without deleting them. Use before a live deployment. |
| `--cleanup` | `false` | Explicitly states that cleanup is enabled. Cleanup is already the default for non-dry-run discovery deployments. |
| `--sm-folder-suffix TEXT` | empty | Suffix used to isolate all dataset, DRD, and semantic-model folders. Use `A`, `B`, and `U` for the three examples. |
| `--auto-approve` | `false` | Skips the interactive model approval prompt. Required for unattended automation; do not use before reviewing an LLM-generated design. |

The command requires one design source: `--sm-design`, `--user-intent`, or `--generate-intent`.

## Use as Claude Cowork Skills Without Source Access

### 1. Install the runtime package

Install the published wheel from PyPI or your approved private package index in the environment Claude Cowork can use. Do not clone the repository.

```bash
pip install "kyvos-sdk-python[env]>=0.6.0" "kyvos-sm-skills[sdk,anthropic]>=1.0.0"
```

Verify that the installed package includes the bundled skill definitions:

```bash
kyvos-skills list
```

### 2. Export the bundled skills

Exporting copies the skill Markdown and shared references from the installed package; it does not require the source tree.

```bash
mkdir -p /opt/claude-cowork/kyvos-skills
kyvos-skills export-skill --all -o /opt/claude-cowork/kyvos-skills
```

The exported directory contains the orchestration skills, including `discover-sm-from-warehouse.md`, and `_shared/` reference material. Keep the shared directory next to the exported skill files because the skills reference it.

### 3. Add skills to Claude Cowork

Create two Cowork skills from the exported files:

1. **Kyvos XMLA Deployment** — use `deploy-from-xmla.md` and its referenced `_shared/` files.
2. **Kyvos Semantic Model Discovery** — use `discover-sm-from-warehouse.md`, `inspect-warehouse-schema.md`, and `_shared/sm-design-principles.md`.

If Cowork imports folders or archives, import `/opt/claude-cowork/kyvos-skills` unchanged. If it accepts pasted instructions only, paste each exported skill Markdown file and the content of its referenced `_shared/` files into the corresponding skill configuration. The exact import control and accepted file layout depend on the Cowork version and organization policy.

Configure both skills with these operating instructions:

- Use the installed `kyvos-skills` executable; do not reconstruct the Python implementation from the instructions.
- Ask for the flow number, source input location, environment-file location, schema where applicable, folder suffix, and requested operation (`dry run` or `deploy`).
- Never request, print, paste, or store Kyvos, warehouse, Anthropic, or Azure OpenAI secrets in the conversation.
- Run a dry run first. Summarize the artifacts or model specification produced and stop for approval.
- Before a live deployment, run `--cleanup-dry-run`, show the matching entity names, folders, and count, then obtain explicit confirmation.
- Use `--auto-approve` only after the user explicitly confirms the displayed deployment command and cleanup impact.
- Stop immediately on a failed parser, compiler, or Kyvos validation result. Do not retry a destructive deployment unless the user requests it.

### 4. Configure the Cowork runtime

A prompt-only Claude Cowork skill can collect requirements and generate a design, but it cannot inspect the warehouse or deploy to Kyvos. For operational use, the Cowork runtime must have the following controlled capabilities:

1. **Package runtime** — Python 3.11+ with the installed `kyvos-sdk-python` and `kyvos-sm-skills` packages, with `kyvos-skills` available on `PATH`.
2. **Read-only inputs** — access to `/opt/kyvos-input/AdventureWorks.xmla` and any approved intent or design artifact.
3. **Protected configuration** — read permission for `/opt/kyvos-config/adventureworks.env` without exposing file contents in chat or logs.
4. **Approved output path** — write permission for `/opt/kyvos-output/` to save generated intent artifacts and run logs.
5. **Network access** — connectivity to the Kyvos server and warehouse; Flows 2 and 3 additionally need access to the configured LLM provider.
6. **Terminal permission controls** — permission to run read-only dry-run commands, with a separate human approval requirement for live commands that can create or remove Kyvos entities.

Use a service account with the minimum warehouse and Kyvos permissions required for the test environment. Do not use production credentials for exploratory Cowork sessions.

### 5. Cowork operating procedure

For every flow, Cowork should follow this sequence:

1. Confirm the requested flow, input path, environment file, schema, suffix, and whether web research is allowed.
2. Verify the input file exists and run `kyvos-skills list` if the runtime has not already been validated.
3. Run the flow's dry-run prompt below.
4. Present the dry-run output, including model counts and any errors or warnings. If `--cleanup-dry-run` reports existing entities, present the names and count.
5. For Flows 2 and 3, explain that semantic-model design is LLM-generated and can vary between runs. Reuse the saved generated intent in Flow 2.
6. Stop and request explicit deployment approval. The approval must state that the user accepts creation and cleanup of the reported entities.
7. Run the matching deployment prompt only after that approval.
8. Report created entity identifiers and Kyvos validation status. Treat any validation error as blocking.

### 6. Cowork prompts

#### Flow 1: XMLA file deployment

**Dry run prompt**

> Use the **Kyvos XMLA Deployment** skill. Verify `/opt/kyvos-input/AdventureWorks.xmla` exists, then dry-run `kyvos-skills deploy` with environment file `/opt/kyvos-config/adventureworks.env`, JSON payload format, and folder suffix `A`. Include `--cleanup-dry-run`. Report parsed tables, relationships, measures, cleanup candidates, and all warnings. Do not deploy or use `--auto-approve`.

**Deployment prompt**

> I approve the Flow 1 XMLA deployment after reviewing the dry run and cleanup candidates. Use the **Kyvos XMLA Deployment** skill to deploy `/opt/kyvos-input/AdventureWorks.xmla` with environment file `/opt/kyvos-config/adventureworks.env`, JSON payload format, and folder suffix `A`. Use `--auto-approve`. Report created dataset, DRD, and semantic-model identifiers and the final Kyvos validation result.

#### Flow 2: Generate intent from the warehouse

**Dry run prompt**

> Use the **Kyvos Semantic Model Discovery** skill to generate a restricted AdventureWorks intent from schema `awdw2019multidimensionalee`. Use environment file `/opt/kyvos-config/adventureworks.env`, `--generate-intent`, `--no-web-research`, JSON payload format, folder suffix `B`, and save the intent to `/opt/kyvos-output/adventureworks-generated-intent.txt`. Include `--cleanup-dry-run` and `--dry-run`. Report the generated-intent path, model counts, cleanup candidates, warnings, and errors. Do not deploy.

**Deployment prompt**

> I approve the Flow 2 deployment after reviewing the generated intent, dry-run output, and cleanup candidates. Use the **Kyvos Semantic Model Discovery** skill to run `kyvos-skills discover` with environment file `/opt/kyvos-config/adventureworks.env`, schema `awdw2019multidimensionalee`, the reviewed intent loaded from `/opt/kyvos-output/adventureworks-generated-intent.txt` as `--user-intent`, JSON payload format, folder suffix `B`, `--no-web-research`, and `--auto-approve`. Do not generate a new intent. Report created entity identifiers and the final Kyvos validation result.

#### Flow 3: Direct user intent

**Dry run prompt**

> Use the **Kyvos Semantic Model Discovery** skill to dry-run a model for `awdw2019multidimensionalee` using this request: “Build an AdventureWorks multifact sales and financial semantic model. Include sales, financial reporting, product and territory analysis, valid quota analysis, and Kyvos MDX measures.” Use environment file `/opt/kyvos-config/adventureworks.env`, domain `adventure_works`, JSON payload format, `--no-web-research`, folder suffix `U`, `--cleanup-dry-run`, and `--dry-run`. Report the selected tables, relationships, measures, hierarchies, cleanup candidates, warnings, and errors. Do not deploy.

**Deployment prompt**

> I approve the Flow 3 deployment after reviewing the dry run and cleanup candidates. Use the **Kyvos Semantic Model Discovery** skill to deploy the approved AdventureWorks multifact sales and financial request against schema `awdw2019multidimensionalee`. Use environment file `/opt/kyvos-config/adventureworks.env`, domain `adventure_works`, JSON payload format, folder suffix `U`, and `--auto-approve`. Do not use web research. Report created entity identifiers and the final Kyvos validation result.

## Expected Results

A successful dry run reports the discovered table count and the built specification's table, relationship, measure, and hierarchy counts. A successful deployment reports created dataset, DRD, and semantic-model identifiers, then a `VALID` semantic-model validation result.

Partition-detail messages can be warnings from the Kyvos environment. Treat any semantic-model validation error as blocking and investigate it before promoting a test model.
