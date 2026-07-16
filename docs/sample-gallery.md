# Sample Gallery

The `samples/models/` directory contains generated Kyvos payloads for 5 industry verticals. Each vertical includes:

- `connection.json` / `connection.xml` — Connection payloads
- `dataset_*.json` / `dataset_*.xml` — Dataset payloads per table
- `drd.json` / `drd.xml` — DRD payloads
- `semantic_model.json` / `semantic_model.xml` — Semantic model payloads

## Verticals

### Retail Banking

- **Schema:** `retail_banking`
- **Tables:** `dim_customer`, `dim_date`, `fact_transactions`
- **Measures:** Total Transaction Amount, Total Fees, Transaction Count, Net Revenue (calculated)
- **Hierarchies:** Calendar (year→quarter→month), Customer Geography (region→segment)

### Healthcare

- **Schema:** `healthcare`
- **Tables:** `dim_patient`, `dim_date`, `fact_admissions`
- **Measures:** Total Admissions, Avg Length of Stay, Total Cost, Cost per Admission (calculated)
- **Hierarchies:** Calendar, Patient Demographics (age_group→gender)

### Retail E-commerce

- **Schema:** `retail_ecom`
- **Tables:** `dim_product`, `dim_date`, `fact_sales`
- **Measures:** Total Sales, Total Quantity, Total Discount, Net Sales, Avg Order Value, Discount Rate
- **Hierarchies:** Calendar, Product Hierarchy (category→subcategory→brand)

### Telecom

- **Schema:** `telecom`
- **Tables:** `dim_region`, `dim_date`, `fact_usage`
- **Measures:** Total Call Minutes, Total Data Usage, Total SMS, Avg Data per SMS
- **Hierarchies:** Calendar, Geography (country→region→city)

### Adventure Works

- **Schema:** `adventure_works`
- **Tables:** `dim_product`, `dim_date`, `fact_internet_sales`
- **Measures:** Total Sales, Total Order Quantity, Avg Unit Price, Avg Discount, Revenue per Unit
- **Hierarchies:** Calendar, Product Category (category→subcategory→color)

## Regenerating Samples

```bash
python scripts/generate_samples.py
```

## Reference Files

The `samples/kyvos-entities/` directory contains reference Kyvos XML/JSON files used as templates:

- `connection.xml` — Reference connection XML
- `dataset.xml` / `dataset.json` — Reference dataset payloads
- `drd.xml` / `drd.json` — Reference DRD payloads
- `semanticmodel.json` — Reference semantic model JSON

The `samples/xmla/` directory contains a sample XMLA file for testing the XMLA parsing workflow.
