# Claude Skill Usage Guide

## Overview

The `skills/` directory contains markdown skill files that serve as prompt-based definitions for Claude (or any LLM) to generate and deploy Kyvos-compatible semantic model components.

## Skill Files

| File | Purpose | Input | Output |
|------|---------|-------|--------|
| `deploy-from-xmla.md` | End-to-end XMLA → Kyvos deployment | XMLA file path + config | Created entity IDs/names |
| `deploy-from-pbit.md` | End-to-end .pbit → Kyvos deployment | PBIT file path + config | Created entity IDs/names |
| `discover-sm-from-warehouse.md` | Warehouse inspection → recommended SMs | Warehouse connection + config | Deployed SM summaries |
| `generate-sm-from-intent.md` | Natural language intent → generated data + SM | Intent + warehouse config | Created entity IDs/names |
| `generate-semantic-model.md` | SM payload generation | Schema + relationships + measures | SM JSON/XML payload |
| `generate-dataset.md` | Dataset payload generation | TableSpec | Dataset JSON/XML |
| `generate-drd.md` | DRD payload generation | Relationships + dataset IDs | DRD JSON/XML |
| `generate-connection.md` | Connection payload generation | DB connection params | Connection JSON/XML |
| `convert-dax-to-mdx.md` | DAX to MDX conversion | DAX measures | MDX measures |
| `design-star-schema.md` | Schema design from domain | Domain description | Star/snowflake/multifact schema JSON |
| `design-measures.md` | Measure design from schema | Schema + domain | Measures JSON |
| `inspect-warehouse-schema.md` | Warehouse schema introspection | DB connection params | Schema summary + pattern detection |

## How to Use

### 1. Direct Prompt Usage

Copy the `## System Prompt` section from a skill file and paste it as the system prompt in your Claude conversation. Then provide the input JSON as the user message.

### 2. Programmatic Usage with Claude API

```python
import anthropic
import json

# Load skill
with open("skills/generate-semantic-model.md") as f:
    skill_content = f.read()

# Extract system prompt (between ## System Prompt and ## Input Schema)
# Or use the entire file as context

client = anthropic.Anthropic()

message = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    system=skill_content,
    messages=[
        {"role": "user", "content": json.dumps(input_data)}
    ]
)
```

### 3. Multi-Skill Workflow

For a complete semantic model from scratch:

1. **Design schema:** Use `design-star-schema.md` with a domain description
2. **Design measures:** Use `design-measures.md` with the schema from step 1
3. **Generate connection:** Use `generate-connection.md` with DB params
4. **Generate datasets:** Use `generate-dataset.md` for each table
5. **Generate DRD:** Use `generate-drd.md` with relationships from step 1
6. **Generate semantic model:** Use `generate-semantic-model.md` with all prior outputs
7. **(Optional) Convert DAX:** Use `convert-dax-to-mdx.md` if migrating from Power BI
8. **Deploy:** Use `deploy-from-xmla.md` or `deploy-from-pbit.md` for end-to-end provisioning

### 4. Using with the Python Compilers

The skill files include `## Backend` sections showing how to use the SDK's pure compilers directly. This is useful for:

- Deterministic payload generation (no LLM needed)
- Validating LLM-generated specs
- Batch processing multiple models

```python
# Use the skill's input schema to structure your data,
# then pass it to the SDK compiler or contract adapter
from kyvos_sdk.compiler import compile_semantic_model

artifact = compile_semantic_model(...)
payload = artifact.payload
```

## Skill File Structure

Each skill file follows this structure:

1. **System Prompt** — Instructions for the LLM
2. **Input Schema** — JSON schema for the input
3. **Output Schema** — JSON schema for the expected output
4. **Example** — Worked example with input and output
5. **Backend** — Python generator reference for programmatic use

## Best Practices

- **Validate LLM output:** Always validate LLM-generated specs against the Pydantic models before passing to generators
- **Use skills as templates:** Customize the system prompts for your specific domain
- **Chain skills:** Use the output of one skill as input to the next in a pipeline
- **Combine with generators:** Use Python generators for deterministic parts and LLM for creative parts (measure design, schema design)
