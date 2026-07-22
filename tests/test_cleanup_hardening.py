"""Tests for cleanup hardening — protected folders, prefix collision, audit log."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from kyvos_sm_skills.skill_runner import (
    _check_prefix_collision,
    _derive_cleanup_prefixes,
    _get_protected_folders,
    _write_audit_log,
)


# ── Protected folders tests ────────────────────────────────────────────────


class TestProtectedFolders:
    def test_no_env_var_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            result = _get_protected_folders()
            assert result == set()

    def test_single_folder(self):
        with patch.dict("os.environ", {"KYVOS_PROTECTED_FOLDERS": "production"}):
            result = _get_protected_folders()
            assert result == {"production"}

    def test_multiple_folders(self):
        with patch.dict("os.environ", {"KYVOS_PROTECTED_FOLDERS": "shared,templates,system"}):
            result = _get_protected_folders()
            assert result == {"shared", "templates", "system"}

    def test_folders_are_lowercase(self):
        with patch.dict("os.environ", {"KYVOS_PROTECTED_FOLDERS": "Production,SHARED"}):
            result = _get_protected_folders()
            assert result == {"production", "shared"}

    def test_whitespace_stripped(self):
        with patch.dict("os.environ", {"KYVOS_PROTECTED_FOLDERS": "  production ,  templates  "}):
            result = _get_protected_folders()
            assert result == {"production", "templates"}

    def test_empty_entries_ignored(self):
        with patch.dict("os.environ", {"KYVOS_PROTECTED_FOLDERS": "production,,templates,"}):
            result = _get_protected_folders()
            assert result == {"production", "templates"}


# ── Prefix collision tests ─────────────────────────────────────────────────


class TestPrefixCollision:
    def test_no_collision_for_specific_prefix(self):
        prefixes = _derive_cleanup_prefixes("AdventureWorks_Discovered_SM")
        warnings = _check_prefix_collision(prefixes)
        assert warnings == []

    def test_collision_for_generic_name_dataset(self):
        warnings = _check_prefix_collision(("dataset",))
        assert len(warnings) == 1
        assert "dataset" in warnings[0]

    def test_collision_for_generic_name_smodel(self):
        warnings = _check_prefix_collision(("smodel",))
        assert len(warnings) == 1

    def test_collision_for_generic_name_test(self):
        warnings = _check_prefix_collision(("test",))
        assert len(warnings) == 1

    def test_no_collision_for_normal_names(self):
        prefixes = ("adventureworks", "adventureworks_discovered_sm")
        warnings = _check_prefix_collision(prefixes)
        assert warnings == []

    def test_multiple_collisions(self):
        warnings = _check_prefix_collision(("dataset", "smodel", "adventureworks"))
        assert len(warnings) == 2  # dataset and smodel are generic


# ── Audit log tests ────────────────────────────────────────────────────────


class TestAuditLog:
    def test_dry_run_log_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        targets = [
            ("DATASET", "AdventureWorks_DS", "id_1", "AdventureWorks"),
            ("FOLDER", "AdventureWorks", "id_2", "RDATASET"),
        ]
        log_path = _write_audit_log(
            targets, deleted=0, base_name="AdventureWorks",
            prefixes=("adventureworks",), dry_run=True
        )
        assert os.path.exists(log_path)
        content = open(log_path).read()
        assert "DRY RUN" in content
        assert "AdventureWorks" in content
        assert "adventureworks" in content
        assert "AdventureWorks_DS" in content
        assert "Entities found: 2" in content
        assert "Entities deleted: 0" in content

    def test_live_log_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        targets = [
            ("DATASET", "Old_DS", "id_1", "OldFolder"),
        ]
        log_path = _write_audit_log(
            targets, deleted=1, base_name="OldBase",
            prefixes=("oldbase",), dry_run=False
        )
        assert os.path.exists(log_path)
        content = open(log_path).read()
        assert "LIVE" in content
        assert "DELETED" in content
        assert "Old_DS" in content
        assert "Entities deleted: 1" in content

    def test_log_filename_has_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        log_path = _write_audit_log(
            [], deleted=0, base_name="Test",
            prefixes=("test",), dry_run=True
        )
        assert log_path.startswith("cleanup_")
        assert log_path.endswith(".log")
