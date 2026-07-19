"""Skill-snippet CI harness — extract Python code from skill markdown files and verify
that imports, function signatures, and API calls match the real SDK.

This catches doc/code drift: if a skill snippet references a function that was renamed,
a parameter that was removed, or an import path that changed, these tests fail.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _extract_python_blocks(md_path: Path) -> list[str]:
    """Extract all ```python ... ``` code blocks from a markdown file."""
    text = md_path.read_text()
    pattern = r"```python\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL)


def _extract_imports(code: str) -> list[tuple[str, str | None, str | None]]:
    """Extract import statements from a Python code block.

    Returns list of (module, import_name, alias) tuples.
    For `from X import Y` → (X, Y, None)
    For `from X import Y as Z` → (X, Y, Z)
    For `import X` → (X, None, None)
    For `import X as Y` → (X, None, Y)
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.append((node.module or "", alias.name, alias.asname))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, None, alias.asname))
    return imports


def _get_skill_files() -> list[Path]:
    """Get all .md skill files (excluding _shared/)."""
    return sorted(
        f for f in SKILLS_DIR.glob("*.md")
        if f.is_file() and not f.name.startswith("_")
    )


# Skills that contain executable Python snippets (deployment/orchestration skills).
# Design/reference skills (design-*, convert-*) may not have Python blocks.
_DEPLOYMENT_SKILLS = {
    "deploy-from-xmla.md",
    "deploy-from-pbit.md",
    "inspect-warehouse-schema.md",
    "discover-sm-from-warehouse.md",
    "generate-sm-from-intent.md",
}


def _get_deployment_skill_files() -> list[Path]:
    """Get only the deployment skill files that must contain Python snippets."""
    return sorted(
        f for f in SKILLS_DIR.glob("*.md")
        if f.is_file() and f.name in _DEPLOYMENT_SKILLS
    )


# ── Tests: all skill files have parseable Python blocks ─────────────────


class TestSkillSnippetExtraction:
    def test_skill_files_exist(self):
        files = _get_skill_files()
        assert len(files) >= 7, f"Expected at least 7 skill files, found {len(files)}"

    @pytest.mark.parametrize("skill_file", _get_deployment_skill_files())
    def test_deployment_skill_has_python_blocks(self, skill_file):
        blocks = _extract_python_blocks(skill_file)
        assert len(blocks) > 0, f"{skill_file.name} has no Python code blocks"

    @pytest.mark.parametrize("skill_file", _get_skill_files())
    def test_python_blocks_are_parseable(self, skill_file):
        blocks = _extract_python_blocks(skill_file)
        for i, block in enumerate(blocks):
            try:
                ast.parse(block)
            except SyntaxError as exc:
                pytest.fail(
                    f"{skill_file.name} block {i} has syntax error: {exc}"
                )


# ── Tests: imports in skill snippets resolve to real modules ────────────


class TestSkillSnippetImports:
    """Verify that all import statements in skill snippets reference
    modules and names that actually exist in the installed packages."""

    @pytest.mark.parametrize("skill_file", _get_deployment_skill_files())
    def test_imports_resolve(self, skill_file):
        blocks = _extract_python_blocks(skill_file)
        failures = []

        for block in blocks:
            imports = _extract_imports(block)
            for module_path, import_name, _alias in imports:
                # Skip stdlib and common third-party that may not be installed
                if module_path in ("json", "os", "sys", "pathlib", "re", "hashlib"):
                    continue

                # Try to import the module
                try:
                    mod = importlib.import_module(module_path)
                except ImportError:
                    failures.append(
                        f"  Cannot import module: {module_path}"
                    )
                    continue

                # If it's a `from X import Y`, verify Y exists
                if import_name:
                    # Handle dotted imports like `from X.Y import Z`
                    parts = module_path.split(".")
                    obj = mod
                    for part in parts[1:]:
                        try:
                            obj = getattr(obj, part)
                        except AttributeError:
                            break

                    if not hasattr(obj, import_name) and not hasattr(mod, import_name):
                        # Check if it's a submodule
                        try:
                            importlib.import_module(f"{module_path}.{import_name}")
                        except ImportError:
                            failures.append(
                                f"  {module_path}.{import_name} not found"
                            )

        if failures:
            pytest.fail(
                f"{skill_file.name} has unresolvable imports:\n"
                + "\n".join(failures)
            )


# ── Tests: deploy-from-xmla specific signature checks ───────────────────


class TestDeployFromXmlaSignatures:
    """Verify that the deploy-from-xmla skill uses real SDK signatures."""

    @pytest.fixture
    def skill_code(self):
        skill_file = SKILLS_DIR / "deploy-from-xmla.md"
        if not skill_file.exists():
            pytest.skip("deploy-from-xmla.md not found")
        blocks = _extract_python_blocks(skill_file)
        return "\n\n".join(blocks)

    def test_uses_kyvos_config_from_env_file(self, skill_code):
        assert "KyvosConfig.from_env_file" in skill_code

    def test_uses_parse_xmla(self, skill_code):
        assert "parse_xmla" in skill_code

    def test_uses_provisioning_client(self, skill_code):
        assert "ProvisioningClient" in skill_code
        assert "FolderType" in skill_code

    def test_uses_create_folder(self, skill_code):
        assert "prov.create_folder" in skill_code
        assert "FolderType.RDATASET" in skill_code

    def test_uses_create_connection(self, skill_code):
        assert "prov.create_connection" in skill_code
        assert "jdbc_url_override" in skill_code
        assert "driver_override" in skill_code

    def test_uses_warehouse_registry(self, skill_code):
        assert "build_jdbc_url" in skill_code
        assert "get_warehouse_profile" in skill_code

    def test_uses_contract_adapter(self, skill_code):
        assert "compile_dataset_artifact" in skill_code
        assert "compile_drd_artifact" in skill_code
        assert "compile_smodel_artifact" in skill_code

    def test_uses_operation_result_properties(self, skill_code):
        assert ".succeeded" in skill_code
        assert ".primary_entity_id" in skill_code
        assert ".diagnostics" in skill_code

    def test_uses_refresh_dataset_columns(self, skill_code):
        assert "refresh_dataset_columns" in skill_code

    def test_does_not_use_removed_apis(self, skill_code):
        assert "deploy_from_xmla" not in skill_code
        assert "xmla_workflow" not in skill_code
        assert "cli.py" not in skill_code

    def test_does_not_use_entity_id_property(self, skill_code):
        assert ".entity_id" not in skill_code

    def test_does_not_use_success_property(self, skill_code):
        assert ".success" not in skill_code.replace(".succeeded", "")

    def test_does_not_use_pg_prefix(self, skill_code):
        assert "pg_host" not in skill_code
        assert "pg_port" not in skill_code
        assert "pg_database" not in skill_code
        assert "pg_username" not in skill_code
        assert "pg_password" not in skill_code
        assert "pg_connection_name" not in skill_code


# ── Tests: deploy-from-pbit specific signature checks ───────────────────


class TestDeployFromPbitSignatures:
    """Verify that the deploy-from-pbit skill uses real SDK signatures."""

    @pytest.fixture
    def skill_code(self):
        skill_file = SKILLS_DIR / "deploy-from-pbit.md"
        if not skill_file.exists():
            pytest.skip("deploy-from-pbit.md not found")
        blocks = _extract_python_blocks(skill_file)
        return "\n\n".join(blocks)

    def test_uses_enrich_spec_from_pbit(self, skill_code):
        assert "enrich_spec_from_pbit" in skill_code

    def test_uses_skip_conversion(self, skill_code):
        assert "skip_conversion" in skill_code

    def test_reads_binary_mode(self, skill_code):
        assert '"rb"' in skill_code or "'rb'" in skill_code

    def test_uses_kyvos_config_from_env_file(self, skill_code):
        assert "KyvosConfig.from_env_file" in skill_code

    def test_uses_provisioning_client(self, skill_code):
        assert "ProvisioningClient" in skill_code
        assert "FolderType" in skill_code

    def test_uses_warehouse_registry(self, skill_code):
        assert "build_jdbc_url" in skill_code
        assert "get_warehouse_profile" in skill_code

    def test_uses_contract_adapter(self, skill_code):
        assert "compile_dataset_artifact" in skill_code
        assert "compile_drd_artifact" in skill_code
        assert "compile_smodel_artifact" in skill_code

    def test_uses_operation_result_properties(self, skill_code):
        assert ".succeeded" in skill_code
        assert ".primary_entity_id" in skill_code
        assert ".diagnostics" in skill_code

    def test_does_not_use_removed_apis(self, skill_code):
        assert "deploy_from_xmla" not in skill_code
        assert "xmla_workflow" not in skill_code

    def test_does_not_use_entity_id_property(self, skill_code):
        assert ".entity_id" not in skill_code

    def test_does_not_use_pg_prefix(self, skill_code):
        assert "pg_host" not in skill_code
        assert "pg_port" not in skill_code
        assert "pg_database" not in skill_code
        assert "pg_username" not in skill_code
        assert "pg_password" not in skill_code
        assert "pg_connection_name" not in skill_code

    def test_references_calculated_columns(self, skill_code):
        assert "pbit_calculated_columns" in skill_code


# ── Tests: inspect-warehouse-schema specific signature checks ───────────


class TestInspectWarehouseSchemaSignatures:
    """Verify that the inspect-warehouse-schema skill uses real SDK signatures."""

    @pytest.fixture
    def skill_code(self):
        skill_file = SKILLS_DIR / "inspect-warehouse-schema.md"
        if not skill_file.exists():
            pytest.skip("inspect-warehouse-schema.md not found")
        blocks = _extract_python_blocks(skill_file)
        return "\n\n".join(blocks)

    def test_uses_kyvos_config_from_env_file(self, skill_code):
        assert "KyvosConfig.from_env_file" in skill_code

    def test_uses_build_sqlalchemy_url(self, skill_code):
        assert "build_sqlalchemy_url" in skill_code

    def test_uses_sqlalchemy_create_engine(self, skill_code):
        assert "create_engine" in skill_code
        assert "inspect" in skill_code

    def test_uses_max_tables_cap(self, skill_code):
        assert "max_tables" in skill_code

    def test_uses_detected_patterns(self, skill_code):
        assert "detected_patterns" in skill_code
        assert "potential_star_schemas" in skill_code
        assert "potential_snowflake_schemas" in skill_code
        assert "potential_multifact_schemas" in skill_code
        assert "single_table_candidates" in skill_code

    def test_uses_schema_filter(self, skill_code):
        assert "schema_filter" in skill_code

    def test_uses_warehouse_type_defaults(self, skill_code):
        assert "POSTGRES" in skill_code
        assert "SNOWFLAKE" in skill_code
        assert "MSSQL" in skill_code

    def test_does_not_use_credentials_in_inputs(self, skill_code):
        assert "password" not in skill_code.lower() or "config.warehouse_password" in skill_code

    def test_does_not_use_pg_prefix(self, skill_code):
        assert "pg_host" not in skill_code
        assert "pg_port" not in skill_code


# ── Tests: discover-sm-from-warehouse specific signature checks ──────────


class TestDiscoverSmFromWarehouseSignatures:
    """Verify that the discover-sm-from-warehouse skill uses real SDK signatures."""

    @pytest.fixture
    def skill_code(self):
        skill_file = SKILLS_DIR / "discover-sm-from-warehouse.md"
        if not skill_file.exists():
            pytest.skip("discover-sm-from-warehouse.md not found")
        blocks = _extract_python_blocks(skill_file)
        return "\n\n".join(blocks)

    @pytest.fixture
    def skill_file(self):
        f = SKILLS_DIR / "discover-sm-from-warehouse.md"
        if not f.exists():
            pytest.skip("discover-sm-from-warehouse.md not found")
        return f

    def test_uses_kyvos_config_from_env_file(self, skill_code):
        assert "KyvosConfig.from_env_file" in skill_code

    def test_uses_provisioning_client(self, skill_code):
        assert "ProvisioningClient" in skill_code
        assert "FolderType" in skill_code

    def test_uses_warehouse_registry(self, skill_code):
        assert "build_jdbc_url" in skill_code
        assert "get_warehouse_profile" in skill_code

    def test_uses_contract_adapter(self, skill_code):
        assert "compile_drd_artifact" in skill_code
        assert "compile_smodel_artifact" in skill_code

    def test_uses_operation_result_properties(self, skill_code):
        assert ".succeeded" in skill_code
        assert ".primary_entity_id" in skill_code
        assert ".diagnostics" in skill_code

    def test_uses_build_spec_from_recommendation(self, skill_code):
        assert "build_spec_from_recommendation" in skill_code

    def test_uses_existing_schema_context(self, skill_code):
        assert "existing_schema_context" in skill_code

    def test_uses_allow_web_research(self, skill_file):
        """Check allow_web_research in the full skill file (it's in JSON schema, not Python)."""
        text = skill_file.read_text()
        assert "allow_web_research" in text

    def test_uses_approval_gates(self, skill_file):
        """Check approval gates in the full skill file (they're in the workflow section)."""
        text = skill_file.read_text()
        assert "approval gate" in text.lower() or "Gate" in text

    def test_does_not_use_entity_id_property(self, skill_code):
        assert ".entity_id" not in skill_code

    def test_does_not_use_pg_prefix(self, skill_code):
        assert "pg_host" not in skill_code
        assert "pg_port" not in skill_code

    def test_references_sm_design_principles(self, skill_file):
        """Check sm-design-principles reference in the full skill file (it's in the header)."""
        text = skill_file.read_text()
        assert "sm-design-principles" in text


# ── Tests: generate-sm-from-intent specific signature checks ────────────


class TestGenerateSmFromIntentSignatures:
    """Verify that the generate-sm-from-intent skill uses real SDK signatures."""

    @pytest.fixture
    def skill_code(self):
        skill_file = SKILLS_DIR / "generate-sm-from-intent.md"
        if not skill_file.exists():
            pytest.skip("generate-sm-from-intent.md not found")
        blocks = _extract_python_blocks(skill_file)
        return "\n\n".join(blocks)

    @pytest.fixture
    def skill_file(self):
        f = SKILLS_DIR / "generate-sm-from-intent.md"
        if not f.exists():
            pytest.skip("generate-sm-from-intent.md not found")
        return f

    def test_uses_kyvos_config_from_env_file(self, skill_code):
        assert "KyvosConfig.from_env_file" in skill_code

    def test_uses_generate_data(self, skill_code):
        assert "generate_data" in skill_code
        assert "DataGenerationResult" in skill_code

    def test_uses_build_sqlalchemy_url(self, skill_code):
        assert "build_sqlalchemy_url" in skill_code

    def test_uses_provisioning_client(self, skill_code):
        assert "ProvisioningClient" in skill_code
        assert "FolderType" in skill_code

    def test_uses_warehouse_registry(self, skill_code):
        assert "build_jdbc_url" in skill_code
        assert "get_warehouse_profile" in skill_code

    def test_uses_contract_adapter(self, skill_code):
        assert "compile_drd_artifact" in skill_code
        assert "compile_smodel_artifact" in skill_code

    def test_uses_operation_result_properties(self, skill_code):
        assert ".succeeded" in skill_code
        assert ".primary_entity_id" in skill_code
        assert ".diagnostics" in skill_code

    def test_uses_build_spec_from_recommendation(self, skill_code):
        assert "build_spec_from_recommendation" in skill_code

    def test_uses_scale_guardrail(self, skill_code):
        assert "default_scale" in skill_code
        assert "effective_scale" in skill_code

    def test_uses_to_sql_for_bulk_load(self, skill_code):
        assert "to_sql" in skill_code
        assert "create_engine" in skill_code

    def test_uses_allow_web_research(self, skill_file):
        text = skill_file.read_text()
        assert "allow_web_research" in text

    def test_uses_approval_gates(self, skill_file):
        text = skill_file.read_text()
        assert "approval gate" in text.lower() or "Gate" in text

    def test_does_not_use_entity_id_property(self, skill_code):
        assert ".entity_id" not in skill_code

    def test_does_not_use_pg_prefix(self, skill_code):
        assert "pg_host" not in skill_code
        assert "pg_port" not in skill_code

    def test_references_sm_design_principles(self, skill_file):
        text = skill_file.read_text()
        assert "sm-design-principles" in text

    def test_references_design_skills(self, skill_file):
        text = skill_file.read_text()
        assert "design-star-schema" in text
        assert "design-measures" in text


# ── Tests: no credentials in skill input schemas ────────────────────────


class TestNoCredentialsInInputs:
    """Verify that no skill input schema contains credential fields.
    Secrets must come from .env or *_PASSWORD_CMD indirection."""

    @pytest.mark.parametrize("skill_file", _get_deployment_skill_files())
    def test_no_password_in_input_schema(self, skill_file):
        text = skill_file.read_text()
        # Find the Input Schema section
        input_section = re.search(
            r"## Input Schema\s*\n(.*?)(?=\n## |\Z)",
            text, re.DOTALL,
        )
        if not input_section:
            return

        # Extract only the JSON block from the input schema (not prose)
        input_text = input_section.group(1)
        json_blocks = re.findall(r"```json\n(.*?)```", input_text, re.DOTALL)
        if not json_blocks:
            return

        schema_text = "\n".join(json_blocks)
        # Check for credential field names in the JSON schema keys
        # Matches "password", "secret", "api_key", "token" as JSON keys
        forbidden_patterns = [
            r'"password"\s*:',
            r'"secret"\s*:',
            r'"api_key"\s*:',
            r'"token"\s*:',
            r'"credential"\s*:',
        ]
        for pattern in forbidden_patterns:
            match = re.search(pattern, schema_text, re.IGNORECASE)
            assert match is None, (
                f"{skill_file.name} Input Schema JSON contains credential field "
                f"'{match.group() if match else pattern}' — "
                f"credentials must come from .env, not skill inputs"
            )
