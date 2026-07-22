"""Tests for cleanup prefix safety — ensures unrelated entities are never matched."""

from kyvos_sm_skills.skill_runner import _derive_cleanup_prefixes, _MIN_PREFIX_LEN


class TestDeriveCleanupPrefixes:
    def test_full_name_always_included(self):
        prefixes = _derive_cleanup_prefixes("AdventureWorks_Discovered_SM")
        assert "adventureworks_discovered_sm" in prefixes

    def test_first_part_included_if_long_enough(self):
        prefixes = _derive_cleanup_prefixes("AdventureWorks_Discovered_SM")
        assert "adventureworks" in prefixes

    def test_no_3char_abbreviation(self):
        """Critical safety test: 3-char abbreviations must NEVER be generated."""
        prefixes = _derive_cleanup_prefixes("AdventureWorks_Discovered_SM")
        assert "adw" not in prefixes
        assert "adv" not in prefixes

    def test_no_short_prefixes(self):
        """All generated prefixes must be >= _MIN_PREFIX_LEN characters."""
        prefixes = _derive_cleanup_prefixes("AdventureWorks_Discovered_SM")
        for p in prefixes:
            assert len(p) >= _MIN_PREFIX_LEN, f"Prefix '{p}' is only {len(p)} chars"

    def test_single_word_name(self):
        """Single word name should only produce one prefix (the full name)."""
        prefixes = _derive_cleanup_prefixes("awdw2019multidimensionalee")
        assert prefixes == ("awdw2019multidimensionalee",)

    def test_short_first_part_excluded(self):
        """If first part is < 8 chars, it should NOT be added as a prefix."""
        prefixes = _derive_cleanup_prefixes("Sales_Analytics_SM")
        assert "sales" not in prefixes  # "sales" is only 5 chars
        assert "sales_analytics_sm" in prefixes

    def test_internet_sales_not_abbreviated(self):
        """Critical: 'Internet Sales Performance' must not produce 'int' prefix."""
        prefixes = _derive_cleanup_prefixes("Internet Sales Performance 072026 1851")
        assert "int" not in prefixes
        # 'internet' is 8 chars exactly, so it IS included — that's acceptable
        # because it's specific enough to avoid matching unrelated folders
        assert "internet" in prefixes

    def test_internet_prefix_is_exactly_8(self):
        """Verify 'internet' (8 chars) is included but nothing shorter."""
        prefixes = _derive_cleanup_prefixes("Internet Sales Performance 072026 1851")
        for p in prefixes:
            assert len(p) >= 8

    def test_empty_string(self):
        prefixes = _derive_cleanup_prefixes("")
        assert prefixes == ()

    def test_underscores_only(self):
        prefixes = _derive_cleanup_prefixes("___")
        assert prefixes == ()

    def test_no_false_positive_on_unrelated_names(self):
        """Prefixes from 'AdventureWorks' should not match 'Banking_Reports'."""
        prefixes = _derive_cleanup_prefixes("AdventureWorks_Discovered_SM")
        for p in prefixes:
            assert not "banking_reports".startswith(p)
            assert not "healthcare_analytics".startswith(p)
            assert not "retail_dashboard".startswith(p)
