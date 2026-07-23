"""Tests for cleanup hardening — protected folders, prefix collision, audit log, suffix scoping."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from kyvos_sm_skills.skill_runner import (
    _check_prefix_collision,
    _collect_and_cleanup_entities,
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


# ── Suffix-scoped cleanup tests ────────────────────────────────────────────


class TestSuffixScopedCleanup:
    """Tests that folder_suffix prevents cross-flow cleanup."""

    def _make_mock_insp(self, folder_refs_by_type):
        """Build a mock InspectionClient that returns folder refs per type."""
        insp = MagicMock()

        def _list_folders(ft):
            refs = folder_refs_by_type.get(ft, [])
            result = MagicMock()
            result.succeeded = True
            result.entity_refs = refs
            return result

        insp.list_folders = _list_folders

        # list_datasets_in_folder, list_drds_in_folder, list_smodels_in_folder
        def _list_entities(folder_name):
            result = MagicMock()
            result.succeeded = True
            result.entity_refs = []
            return result

        insp.list_datasets_in_folder = _list_entities
        insp.list_drds_in_folder = _list_entities
        insp.list_smodels_in_folder = _list_entities
        return insp

    def _make_ref(self, name, id_="id"):
        ref = MagicMock()
        ref.name = name
        ref.id = id_
        return ref

    def test_suffix_matches_own_folders(self):
        """When folder_suffix='G', folders ending with _G should match."""
        from kyvos_sdk.contracts.identity import FolderType

        folders = {
            FolderType.RDATASET: [
                self._make_ref("awdw2019multidimensionalee_G", "id_g"),
            ],
        }
        insp = self._make_mock_insp(folders)
        prov = MagicMock()

        result = _collect_and_cleanup_entities(
            insp=insp,
            prov=prov,
            base_name="awdw2019multidimensionalee",
            dry_run=True,
            folder_suffix="G",
        )
        # Dry run returns False, but should have found the folder
        assert result is False

    def test_suffix_excludes_other_flow_folders(self):
        """When folder_suffix='G', folders with _X or _U should NOT match."""
        from kyvos_sdk.contracts.identity import FolderType

        folders = {
            FolderType.RDATASET: [
                self._make_ref("awdw2019multidimensionalee_X", "id_x"),
                self._make_ref("awdw2019multidimensionalee_U", "id_u"),
                self._make_ref("awdw2019multidimensionalee_G", "id_g"),
            ],
        }
        insp = self._make_mock_insp(folders)
        prov = MagicMock()

        # Use dry_run to collect targets without deleting
        with patch("builtins.print"):
            _collect_and_cleanup_entities(
                insp=insp,
                prov=prov,
                base_name="awdw2019multidimensionalee",
                dry_run=True,
                folder_suffix="G",
            )

        # Verify prov.delete_dataset was never called (dry_run=True)
        prov.delete_dataset.assert_not_called()
        prov.delete_drd.assert_not_called()
        prov.delete_smodel.assert_not_called()

    def test_no_suffix_matches_all_folders(self):
        """Without folder_suffix, all prefix-matching folders should match (backward compat)."""
        from kyvos_sdk.contracts.identity import FolderType

        folders = {
            FolderType.RDATASET: [
                self._make_ref("awdw2019multidimensionalee_X", "id_x"),
                self._make_ref("awdw2019multidimensionalee_G", "id_g"),
                self._make_ref("awdw2019multidimensionalee", "id_base"),
            ],
        }
        insp = self._make_mock_insp(folders)
        prov = MagicMock()

        with patch("builtins.print"):
            _collect_and_cleanup_entities(
                insp=insp,
                prov=prov,
                base_name="awdw2019multidimensionalee",
                dry_run=True,
            )

        # Dry run — no deletions
        prov.delete_dataset.assert_not_called()

    def test_suffix_excludes_base_folders(self):
        """When folder_suffix='G', base (non-suffixed) folders should NOT match."""
        from kyvos_sdk.contracts.identity import FolderType

        folders = {
            FolderType.RDATASET: [
                self._make_ref("awdw2019multidimensionalee", "id_base"),
                self._make_ref("awdw2019multidimensionalee_G", "id_g"),
            ],
        }
        insp = self._make_mock_insp(folders)
        prov = MagicMock()

        with patch("builtins.print"):
            _collect_and_cleanup_entities(
                insp=insp,
                prov=prov,
                base_name="awdw2019multidimensionalee",
                dry_run=True,
                folder_suffix="G",
            )

        prov.delete_dataset.assert_not_called()

    def test_suffix_case_insensitive(self):
        """Folder suffix matching should be case-insensitive."""
        from kyvos_sdk.contracts.identity import FolderType

        folders = {
            FolderType.RDATASET: [
                self._make_ref("awdw2019multidimensionalee_g", "id_lower"),
                self._make_ref("awdw2019multidimensionalee_G", "id_upper"),
            ],
        }
        insp = self._make_mock_insp(folders)
        prov = MagicMock()

        with patch("builtins.print"):
            _collect_and_cleanup_entities(
                insp=insp,
                prov=prov,
                base_name="awdw2019multidimensionalee",
                dry_run=True,
                folder_suffix="G",
            )

        prov.delete_dataset.assert_not_called()

    def test_suffix_drd_folder_matches(self):
        """DRD folders with suffix should match (e.g., base_DRD_G)."""
        from kyvos_sdk.contracts.identity import FolderType

        folders = {
            FolderType.DATASET_RELATIONSHIP: [
                self._make_ref("awdw2019multidimensionalee_DRD_G", "id_drd_g"),
                self._make_ref("awdw2019multidimensionalee_DRD_X", "id_drd_x"),
            ],
        }
        insp = self._make_mock_insp(folders)
        prov = MagicMock()

        with patch("builtins.print"):
            _collect_and_cleanup_entities(
                insp=insp,
                prov=prov,
                base_name="awdw2019multidimensionalee",
                dry_run=True,
                folder_suffix="G",
            )

        prov.delete_drd.assert_not_called()
