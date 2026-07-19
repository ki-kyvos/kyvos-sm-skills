# Shared: Semantic Model Design Principles

> This file is referenced by `discover-sm-from-warehouse.md`, `generate-sm-from-intent.md`, and `deploy-from-multi-pbit.md`. It defines the canonical enterprise-quality mandate that all three skills must follow.

## Enterprise-Quality Mandate

All semantic model recommendations must reflect production-grade enterprise data modeling — not academic or toy examples. This means:

### Conformed Dimensions
- Proper conformed dimensions (e.g., `dim_date` with full calendar attributes, fiscal calendar support, type 2 SCD patterns where appropriate)
- Dimensions shared across fact tables must use consistent keys and attributes
- Natural keys preserved alongside surrogate keys for traceability

### Industry-Standard Measure Definitions
- Measures must map to real industry KPIs (e.g., revenue recognition, COGS, churn rate per industry conventions)
- Correct aggregation types: sum, distinct count, weighted average, etc. matching industry KPIs
- Base measures derived from actual numeric columns; calculated measures use industry-standard formulas

### Appropriate Granularity
- Correct fact table granularity (transaction-level vs. snapshot-level based on analytics needs)
- Domain-specific grain: healthcare → patient encounter; retail → basket-level vs. daily-store-level; banking → account snapshot vs. transaction
- Avoid over-aggregation that loses analytical flexibility

### Correct Dimensional Modeling Patterns
- Star schema for standard single-process analytics
- Snowflake for complex dimension hierarchies with normalized sub-dimensions
- Multifact for multiple business processes sharing conformed dimensions
- Single table for simple flat/denormalized data with no dimensional depth

### Named Hierarchies
- Hierarchies must reflect real business rollups (e.g., product → category → department → division)
- Multi-level hierarchies with explicit level definitions
- Date hierarchies: year → quarter → month → week → day (with fiscal calendar variant where applicable)

## Supported Schema Types

| Schema Type | When to Use | Example |
|-------------|-------------|--------|
| **Single table** | Simple domain, flat denormalized data, few attributes, no need for separate dimensions | A single `sales_summary` table with measures + attributes in one table |
| **Star schema** | Standard BI use case: one fact table surrounded by conformed dimensions | `fact_sales` + `dim_product` + `dim_customer` + `dim_date` |
| **Snowflake schema** | Dimensions have complex hierarchies or normalized sub-dimensions that reduce redundancy | `fact_sales` + `dim_product` → `dim_category` → `dim_department` |
| **Multifact schema** | Multiple business processes sharing conformed dimensions | `fact_sales` + `fact_inventory` + shared `dim_product` + `dim_date` |

## Design Principle: Right-Size Complexity to Domain

The LLM should avoid over-engineering. A retail sales dashboard doesn't need a multifact schema if a single star suffices. A healthcare analytics platform with patient claims, prescriptions, and lab results may warrant multiple SMs or a multifact schema. Apply domain research findings + built-in knowledge to balance simplicity vs. normalization.

**Enterprise patterns are preferred when the domain warrants them** — do not strip dimensions or measures just to simplify if the domain standard calls for them. Conversely, do not add unnecessary normalization when a simple star schema serves the use case.

## Domain Research Requirements

Before recommending any SM, the LLM must:
1. **Identify the domain** — infer from table/column names, measure names, or PBIT metadata if not explicitly provided
2. **Research the domain** via web search — look for industry-standard data models, schema patterns, and KPI definitions
3. **Map available tables to domain concepts** — which tables correspond to which industry-standard entities?
4. **Identify gaps** — are there standard dimensions or measures for this domain that are missing?
5. **Synthesize findings** into a domain research summary that informs the SM design

When `allow_web_research` is `false`, domain research uses built-in knowledge only — no table/column/measure names leave the local environment via web search.

## User Approval Gates

All three skills require user approval at key checkpoints:
1. **Gate 1** — Domain identification confirmation
2. **Gate 2** — Domain research findings review
3. **Gate 3** — Full SM design review (schema types, tables, measures, hierarchies, relationships)

The LLM iterates on user feedback until the user explicitly approves before proceeding to deployment.
