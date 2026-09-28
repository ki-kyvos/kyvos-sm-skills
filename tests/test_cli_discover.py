"""Tests for the discover CLI subcommand."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kyvos_sm_skills.cli import main


def _run_cli(argv: list[str]) -> int:
    """Run CLI with a given argv list."""
    with patch("sys.argv", ["kyvos-skills"] + argv):
        return main()


class TestCliDiscover:
    def test_discover_help(self, capsys):
        """discover --help should show discover-specific options."""
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(["discover", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--sm-design" in captured.out
        assert "--user-intent" in captured.out
        assert "--domain" in captured.out
        assert "--auto-approve" in captured.out
        assert "--dry-run" in captured.out

    def test_discover_dry_run_with_sm_design(self, tmp_path, capsys):
        """discover --sm-design with --dry-run should call runner with dry_run=True."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        design_file = tmp_path / "design.json"
        design_file.write_text(json.dumps({
            "recommended_sms": [{
                "name": "TestSM",
                "schema_type": "star",
                "tables": ["fact_sales"],
                "relationships": [],
                "measures": [],
                "hierarchies": [],
            }]
        }))

        mock_result = 0
        with patch("kyvos_sm_skills.skill_runner.run_discover_sm_from_warehouse", return_value=mock_result) as mock_runner:
            rc = _run_cli([
                "discover",
                "--env-file", str(env_file),
                "--sm-design", str(design_file),
                "--dry-run",
            ])

        assert rc == 0
        mock_runner.assert_called_once()
        call_kwargs = mock_runner.call_args
        assert call_kwargs.kwargs.get("sm_design_path") == str(design_file)
        assert call_kwargs.kwargs.get("dry_run") is True

    def test_discover_with_user_intent(self, tmp_path, capsys):
        """discover --user-intent should pass user_intent to runner."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sm_skills.skill_runner.run_discover_sm_from_warehouse", return_value=0) as mock_runner:
            rc = _run_cli([
                "discover",
                "--env-file", str(env_file),
                "--user-intent", "I want sales analytics",
                "--domain", "adventure_works",
                "--auto-approve",
                "--dry-run",
            ])

        assert rc == 0
        call_kwargs = mock_runner.call_args
        assert call_kwargs.kwargs.get("user_intent") == "I want sales analytics"
        assert call_kwargs.kwargs.get("domain") == "adventure_works"
        assert call_kwargs.kwargs.get("auto_approve") is True

    def test_discover_no_web_research_flag(self, tmp_path):
        """--no-web-research should set allow_web_research=False."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sm_skills.skill_runner.run_discover_sm_from_warehouse", return_value=0) as mock_runner:
            _run_cli([
                "discover",
                "--env-file", str(env_file),
                "--user-intent", "test",
                "--no-web-research",
                "--dry-run",
            ])

        call_kwargs = mock_runner.call_args
        assert call_kwargs.kwargs.get("allow_web_research") is False

    def test_discover_schema_filter(self, tmp_path):
        """--schema should pass schema_filter to runner."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sm_skills.skill_runner.run_discover_sm_from_warehouse", return_value=0) as mock_runner:
            _run_cli([
                "discover",
                "--env-file", str(env_file),
                "--user-intent", "test",
                "--schema", "myschema",
                "--dry-run",
            ])

        call_kwargs = mock_runner.call_args
        assert call_kwargs.kwargs.get("schema_filter") == "myschema"

    def test_discover_max_tables(self, tmp_path):
        """--max-tables should pass max_tables to runner."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        with patch("kyvos_sm_skills.skill_runner.run_discover_sm_from_warehouse", return_value=0) as mock_runner:
            _run_cli([
                "discover",
                "--env-file", str(env_file),
                "--user-intent", "test",
                "--max-tables", "50",
                "--dry-run",
            ])

        call_kwargs = mock_runner.call_args
        assert call_kwargs.kwargs.get("max_tables") == 50


class TestCliDiscoverKyvos:
    """Tests for the discover-kyvos CLI subcommand."""

    def test_discover_kyvos_help(self, capsys):
        """discover-kyvos --help should show required Kyvos options."""
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(["discover-kyvos", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--connection-name" in captured.out
        assert "--database-name" in captured.out
        assert "--schema-name" in captured.out

    def test_discover_kyvos_dry_run_passes_schema_summary(self, tmp_path):
        """discover-kyvos should build a schema_summary and reuse the runner."""
        env_file = tmp_path / ".env"
        env_file.write_text("KYVOS_BASE_URL=http://test\nKYVOS_WAREHOUSE_TYPE=POSTGRES\n")

        fake_summary = {"warehouse_type": "KYVOS", "schema": "br_new", "table_count": 0, "tables": [], "relationships": [], "detected_patterns": {}}

        with patch("kyvos_sm_skills.kyvos_api_inspector.inspect_schema_from_kyvos", return_value=fake_summary) as mock_inspector:
            with patch("kyvos_sm_skills.skill_runner.run_discover_sm_from_warehouse", return_value=0) as mock_runner:
                rc = _run_cli([
                    "discover-kyvos",
                    "--env-file", str(env_file),
                    "--connection-name", "PSdatabricks",
                    "--database-name", "kyvs_support_ps",
                    "--schema-name", "br_new",
                    "--user-intent", "Build a retail semantic model",
                    "--domain", "retail",
                    "--dry-run",
                ])

        assert rc == 0
        mock_inspector.assert_called_once()
        inspector_kwargs = mock_inspector.call_args.kwargs
        assert inspector_kwargs["connection_name"] == "PSdatabricks"
        assert inspector_kwargs["database_name"] == "kyvs_support_ps"
        assert inspector_kwargs["schema_name"] == "br_new"

        mock_runner.assert_called_once()
        runner_kwargs = mock_runner.call_args.kwargs
        assert runner_kwargs["schema_summary"] is fake_summary
        assert runner_kwargs["user_intent"] == "Build a retail semantic model"
        assert runner_kwargs["domain"] == "retail"
        assert runner_kwargs["dry_run"] is True
