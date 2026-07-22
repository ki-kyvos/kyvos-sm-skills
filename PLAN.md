# Enhancement Plan: LLM-Driven Intent, Dynamic Knowledge Base, Parent-Child Hierarchies

> **GitHub Issue:** https://github.com/ki-kyvos/kyvos-sm-skills/issues/1

## Status Legend
- [x] Done — implemented and tested
- [ ] Pending — not yet started
- [~] In Progress — actively being worked on
- [!] Blocked — needs user input or external dependency

---

## Phase 1: MDX Reference Framework (DONE)

### 1.1 Create `kyvos_sm_skills/mdx_reference.py`
- [x] MDX function catalog (40+ functions from Kyvos MDX Functions Guide)
- [x] Expression templates (YTD, QTD, MTD, prior year, YoY, profit margin, safe divide, running total, rank)
- [x] DAX-to-MDX converter (`convert_dax_to_mdx()`) — handles TOTALYTD, TOTALQTD, TOTALMTD, CALCULATE+SAMEPERIODLASTYEAR, DATEADD
- [x] MDX expression validator (`validate_mdx_expression()`) — flags unsupported DAX functions
- [x] Prompt summary generator (`get_mdx_prompt_summary()`) — concise prompt text for LLM
- [x] Template builder (`build_expression()`) — programmatic expression generation

**Validation:**
- [x] 31 unit tests in `tests/test_mdx_reference.py` — all passing
- [x] Tests cover: catalog completeness, DAX conversion rules, validator, templates, prompt summary
- [ ] **Manual verification needed**: Confirm MDX expressions in deployed SM on Kyvos server match official guide syntax

### 1.2 Integrate MDX reference into LLM prompt (`llm_designer.py`)
- [x] Replaced DAX instructions (TOTALMTD, TOTALQTD, TOTALYTD, CALCULATE, SAMEPERIODLASTYEAR) with `get_mdx_prompt_summary()`
- [x] Time intelligence examples now use MDX: `SUM(YTD(...), [Measures].[...])`, `ParallelPeriod(...)`, `IIF(...)`
- [x] Import added: `from kyvos_sm_skills.mdx_reference import get_mdx_prompt_summary`

**Validation:**
- [x] LLM prompt no longer contains any DAX function names
- [ ] **Manual verification needed**: Run AdventureWorks flow and inspect LLM output to confirm it produces MDX (not DAX) expressions

### 1.3 Integrate MDX converter into spec builder (`spec_builder.py`)
- [x] Import added: `from kyvos_sm_skills.mdx_reference import convert_dax_to_mdx, validate_mdx_expression`
- [x] In `_build_measures()`: calculated measure expressions are run through `convert_dax_to_mdx()` automatically
- [x] Warnings printed for any unsupported DAX functions found after conversion
- [x] Removed duplicate `is_calculated` assignment

**Validation:**
- [x] All 317 tests pass
- [ ] **Manual verification needed**: Inspect deployed SM measures on Kyvos server — confirm no DAX functions remain

---

## Phase 2: Hierarchy Fixes (DONE)

### 2.1 Improve LLM prompt for hierarchy generation (`llm_designer.py`)
- [x] Hierarchies must specify `source_dataset` (dimension table)
- [x] Levels must be ACTUAL COLUMN NAMES from the warehouse schema (not made-up names)
- [x] Levels ordered broadest → most granular
- [x] Parent-child hierarchies: `is_parent_child=true`, `parent_column`, `child_column`
- [x] Instruction to verify each level exists on the source_dataset table before listing
- [x] Instruction to omit hierarchies if natural columns don't exist

**Validation:**
- [ ] **Manual verification needed**: Run AdventureWorks flow and inspect LLM output hierarchies — confirm levels match actual warehouse columns

### 2.2 Add hierarchy validation in spec builder (`spec_builder.py`)
- [x] `source_dataset` is now **required** (was optional before — would silently produce hierarchies with no table association)
- [x] Each level validated against actual columns on the `source_dataset` table
- [x] Invalid levels are skipped (with warning log)
- [x] Hierarchies with zero valid levels are dropped entirely (with warning log)
- [x] Parent-child columns validated against table schema; cleared if not found
- [x] Level names normalized to actual column casing from warehouse

**Validation:**
- [x] All 317 tests pass
- [ ] **Manual verification needed**: Inspect deployed SM hierarchies on Kyvos server — confirm all levels are real columns

---

## Phase 3: Cleanup Safety (FIX APPLIED — DRY-RUN VALIDATED)

### 3.1 Fix Applied
- [x] Removed 3-character abbreviation generation
- [x] Added `_MIN_PREFIX_LEN = 8` constant
- [x] Defense-in-depth filter for `extra_prefixes`

### 3.2 Dry-Run Validation Results (COMPLETED)
- [x] **Step A**: Discover flow dry-run — warehouse connection verified
- [x] **Step B**: Standalone cleanup dry-run with `awdw2019multidimensionalee` — 24 entities found, ALL AdventureWorks-related
- [x] **Step C**: Folder collision check — 504 folders scanned, only 3 matched (all ours), `"internet"` prefix matched 0 folders
- [x] **Conclusion**: Cleanup is safe to proceed

### 3.3 Cleanup Hardening (DONE — GitHub Issue #1)
- [x] Protected folders mechanism (`KYVOS_PROTECTED_FOLDERS` env var)
- [x] Cleanup confirmation gate (interactive 'yes' prompt, `auto_approve` for CI/CD)
- [x] Cleanup audit log (`cleanup_YYYYMMDD_HHMMSS.log`)
- [x] Prefix collision warning (abort on generic name collision)

---

## Phase 4: Parent-Child Hierarchy Enhancement (DONE — GitHub Issue #1)

### 4.1 SDK Compiler Updates (`kyvos_sdk/compiler.py`)
- [x] JSON compiler: emit `hasParentChildRelation: true` when `is_parent_child=True`
- [x] JSON compiler: emit `parentField`, `rootMemberType`, `nonLeafDataMember` settings
- [x] JSON compiler: emit `pcLevelNamingTemplate` with level names
- [x] JSON compiler: emit single level (child key) for parent-child hierarchies
- [x] JSON compiler: emit `hasAlternatePath: true` when applicable
- [x] XML compiler: add `HAS_PARENT_CHILD` attribute and child elements

### 4.2 Model Updates
- [x] `kyvos_sm_skills/models.py`: Add `root_member_type`, `non_leaf_data_member_visible`, `non_leaf_data_member_caption`, `display_column`
- [x] `kyvos_sdk/contracts/domain.py`: Add same fields; relax `_at_least_one_level` validator for parent-child
- [x] `kyvos_sdk/contracts/adapters.py`: Map new fields in `adapt_hierarchy()`

### 4.3 LLM Prompt Updates
- [x] Add parent-child hierarchy documentation from Kyvos docs
- [x] Include examples: Employee, Organization, Account
- [x] Instruct LLM to provide `root_member_type`, `display_column`, level naming

### 4.4 Tests
- [x] Unit tests for parent-child hierarchy builder (16 tests in `test_parent_child_hierarchy.py`)
- [x] Unit tests for SDK JSON/XML compiler parent-child output (4 tests in `test_compiler.py`)
- [x] All 877 tests passing (333 kyvos-sm-skills + 544 kyvos-sdk-python)

---

## Phase 5: Dynamic Knowledge Base (DONE — GitHub Issue #1)

### 5.1 Create `kyvos_sm_skills/knowledge_base.py`
- [x] Curated Kyvos documentation URLs with metadata:
  - MDX Functions Guide
  - Parent-child hierarchies
  - Custom rollups
  - Alternate hierarchies
- [x] `get_knowledge_base_summary() -> str` — concise summary for LLM prompt
- [x] `get_knowledge_base_urls() -> list[dict]` — structured list
- [x] `get_references_by_category(category)` — filter by category
- [x] `get_mdx_reference()` / `get_parent_child_reference()` — convenience accessors

### 5.2 Update LLM Prompt
- [x] Replace static MDX summary with knowledge base summary
- [x] Include parent-child hierarchy design guidelines
- [x] Instruct LLM to use references as knowledge base for research
- [x] All documentation URLs and key concepts included in prompt

### 5.3 Tests
- [x] 23 unit tests in `tests/test_knowledge_base.py`
- [x] Verify LLM prompt includes all documentation references

---

## Phase 6: Automatic Intent Generation (DONE — GitHub Issue #1)

### 6.1 Create `kyvos_sm_skills/intent_generator.py`
- [x] `generate_intent(schema_summary, domain, enterprise_context=None) -> str`
- [x] Uses LLM to generate intent by:
  1. Analyzing discovered warehouse schema (all tables, columns, relationships)
  2. Researching the domain
  3. Applying enterprise AI/BI best practices
  4. Including Kyvos-specific requirements (MDX, parent-child, custom rollups)
  5. Producing structured intent document
- [x] `generate_intent_from_file()` — saves generated intent to file

### 6.2 Update CLI
- [x] Add `--generate-intent` flag to `discover` command
- [x] Add `--intent-output` flag to specify save location
- [x] Pipeline: inspect schema → generate intent → save to file → use in design

### 6.3 Tests
- [x] 19 unit tests in `tests/test_intent_generator.py` (mock LLM)
- [x] Verify intent includes: schema analysis, domain research, hierarchy/KPI/MDX requirements
- [x] Verify parent-child hierarchy instructions in user message
- [x] Verify both Anthropic and Azure OpenAI providers supported

---

## Phase 7: End-to-End AdventureWorks Validation (PENDING — requires live Kyvos server)

### 7.1 Pipeline Run with All Enhancements
- [ ] Run `discover --generate-intent --cleanup-dry-run --dry-run`
- [ ] Verify generated intent covers all fact tables, hierarchies, KPIs
- [ ] Verify LLM uses knowledge base for MDX and parent-child design
- [ ] Verify parent-child hierarchies (Employee, Organization, Account) are correctly built

### 7.2 MDX Expression Verification
- [ ] Inspect deployed measures on Kyvos server
- [ ] Confirm calculated measures use MDX syntax (SUM, YTD, ParallelPeriod, IIF, DIVIDE)
- [ ] Confirm NO DAX functions present

### 7.3 Hierarchy Verification
- [ ] Inspect deployed hierarchies on Kyvos server
- [ ] Confirm each level is an actual column on the source_dataset table
- [ ] Confirm parent-child hierarchies have `hasParentChildRelation: true` on server
- [ ] Confirm parent/child columns are correct

### 7.4 Cleanup Safety Verification
- [ ] Dry-run: only AdventureWorks entities matched
- [ ] Live run: no unrelated entities deleted
- [ ] Post-run: all unrelated models intact on server
- [ ] Audit log written and verified

---

## Summary of All Files Changed

| File | Status | Changes |
|------|--------|---------|
| `kyvos_sm_skills/mdx_reference.py` | NEW | MDX catalog, templates, DAX converter, validator, prompt summary |
| `kyvos_sm_skills/knowledge_base.py` | NEW | Curated Kyvos documentation references (5 docs, knowledge base summary) |
| `kyvos_sm_skills/intent_generator.py` | NEW | LLM-powered automatic intent generation with Anthropic/Azure OpenAI support |
| `kyvos_sm_skills/llm_designer.py` | MODIFIED | MDX summary; parent-child docs; knowledge base integration in prompt |
| `kyvos_sm_skills/spec_builder.py` | MODIFIED | DAX-to-MDX conversion; hierarchy validation; parent-child empty levels; data type validation; new field extraction |
| `kyvos_sm_skills/models.py` | MODIFIED | Added parent-child fields to HierarchySpec (root_member_type, display_column, etc.) |
| `kyvos_sm_skills/skill_runner.py` | MODIFIED | Cleanup prefix safety; protected folders; confirmation gate; audit log; prefix collision warning |
| `kyvos_sm_skills/cli.py` | MODIFIED | Added --generate-intent and --intent-output flags |
| `kyvos_sdk/contracts/domain.py` | MODIFIED | Added parent-child fields; relaxed level validator for parent-child |
| `kyvos_sdk/contracts/adapters.py` | MODIFIED | Map new parent-child fields in adapt_hierarchy() |
| `kyvos_sdk/compiler.py` | MODIFIED | Emit parent-child fields in JSON and XML compilers |
| `kyvos_sdk/models.py` | MODIFIED | Added parent-child fields to legacy HierarchySpec |
| `tests/test_mdx_reference.py` | NEW | 31 tests for MDX reference module |
| `tests/test_cleanup_safety.py` | NEW | 11 tests for cleanup prefix safety |
| `tests/test_cleanup_hardening.py` | NEW | 15 tests for protected folders, prefix collision, audit log |
| `tests/test_parent_child_hierarchy.py` | NEW | 16 tests for parent-child hierarchy spec builder + LLM prompt |
| `tests/test_knowledge_base.py` | NEW | 23 tests for knowledge base module |
| `tests/test_intent_generator.py` | NEW | 19 tests for intent generation (mocked LLM) |
| `tests/test_compiler.py` (SDK) | MODIFIED | 4 new tests for parent-child JSON/XML compiler output |
| `tests/test_spec_builder.py` | MODIFIED | Updated test for empty levels (skip vs raise) |
| `intent-adventureworks.txt` | NEW | Finalized intent for AdventureWorks validation |

## Test Counts
- **kyvos-sm-skills**: 390 tests (all passing)
  - 275 original + 31 MDX + 11 cleanup safety + 16 parent-child + 23 knowledge base + 19 intent generator + 15 cleanup hardening
- **kyvos-sdk-python**: 544 tests (all passing)
  - 540 original + 4 parent-child compiler tests
- **Total**: 934 tests across both repos
