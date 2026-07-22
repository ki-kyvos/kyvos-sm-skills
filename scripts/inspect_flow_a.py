"""Compile Flow A SM design and inspect the generated JSON for issues."""
import json

# Load Flow A SM design
with open("samples/output/flow-a-intent-file-sm-design.json") as f:
    design = json.load(f)

sm_rec = design["recommended_sms"][0]

# Hierarchies
print("Flow A Hierarchies:")
for h in sm_rec.get("hierarchies", []):
    print(f"  {h.get('name')}: dataset={h.get('source_dataset')}, pc={h.get('is_parent_child', False)}, levels={len(h.get('levels', []))}")
    for lvl in h.get("levels", []):
        print(f"    level: {lvl.get('name')}, key_column={lvl.get('key_column')}, parent_field={lvl.get('parent_field', 'N/A')}")

print("\nFlow A Measures:")
for m in sm_rec.get("measures", []):
    print(f"  {m.get('name')}: dataset={m.get('source_dataset')}, column={m.get('source_column')}, expr={m.get('expression', 'N/A')[:60]}")

print("\nFlow A Relationships:")
for rel in sm_rec.get("relationships", []):
    print(f"  {rel['from_table']}.{rel['from_column']} -> {rel['to_table']}.{rel['to_column']} ({rel.get('relationship_type', 'many_to_one')})")
