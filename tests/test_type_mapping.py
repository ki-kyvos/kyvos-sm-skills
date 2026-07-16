"""Tests for type mapping utilities."""

import pytest

from kyvos_sm_skills.type_mapping import (
    SQL_TO_KYVOS_XML_MAP,
    _KYVOS_TYPE_ALIAS,
    field_format_value,
    map_sql_to_kyvos_type,
    resolve_sql_type,
)


class TestResolveSqlType:
    def test_simple_integer(self):
        assert resolve_sql_type("INTEGER") == "INTEGER"

    def test_varchar_with_length(self):
        assert resolve_sql_type("VARCHAR(255)") == "VARCHAR"

    def test_numeric_with_precision(self):
        assert resolve_sql_type("NUMERIC(15,2)") == "NUMERIC"

    def test_double_precision_alias(self):
        assert resolve_sql_type("DOUBLE PRECISION") == "DOUBLE"

    def test_timestamp_tz_alias(self):
        assert resolve_sql_type("TIMESTAMPTZ") == "TIMESTAMP"

    def test_uuid_alias(self):
        assert resolve_sql_type("UUID") == "VARCHAR"

    def test_jsonb_alias(self):
        assert resolve_sql_type("JSONB") == "TEXT"

    def test_lowercase(self):
        assert resolve_sql_type("integer") == "INTEGER"

    def test_unknown_type(self):
        assert resolve_sql_type("UNKNOWN_TYPE") == "UNKNOWN_TYPE"


class TestMapSqlToKyvosType:
    def test_integer_mapping(self):
        result = map_sql_to_kyvos_type("INTEGER")
        assert result["dataTypeName"] == "NUMBER"
        assert result["pigDataType"] == "-5"
        assert result["dataSubTypeName"] == "long"

    def test_varchar_mapping(self):
        result = map_sql_to_kyvos_type("VARCHAR(100)")
        assert result["dataTypeName"] == "CHAR"
        assert result["pigDataType"] == "1"

    def test_date_mapping(self):
        result = map_sql_to_kyvos_type("DATE")
        assert result["dataTypeName"] == "DATE"
        assert result["isTimestamp"] is False

    def test_timestamp_mapping(self):
        result = map_sql_to_kyvos_type("TIMESTAMP")
        assert result["dataTypeName"] == "DATE"
        assert result["isTimestamp"] is True
        assert result["originalDataTypeName"] == "93"

    def test_boolean_mapping(self):
        result = map_sql_to_kyvos_type("BOOLEAN")
        assert result["dataTypeName"] == "BOOLEAN"

    def test_unknown_type_defaults_to_char(self):
        result = map_sql_to_kyvos_type("UNKNOWN")
        assert result["dataTypeName"] == "CHAR"


class TestFieldFormatValue:
    def test_date_format(self):
        type_info = map_sql_to_kyvos_type("DATE")
        assert field_format_value(type_info) == "yyyy-mm-dd"

    def test_timestamp_format(self):
        type_info = map_sql_to_kyvos_type("TIMESTAMP")
        assert field_format_value(type_info) == "yyyy-mm-dd"

    def test_number_format(self):
        type_info = map_sql_to_kyvos_type("INTEGER")
        assert field_format_value(type_info) == "0"

    def test_char_format(self):
        type_info = map_sql_to_kyvos_type("VARCHAR(100)")
        assert field_format_value(type_info) == "0"


class TestSqlToKyvosMap:
    def test_map_has_common_types(self):
        expected = {"INTEGER", "BIGINT", "VARCHAR", "DATE", "TIMESTAMP", "BOOLEAN", "NUMERIC", "FLOAT"}
        assert expected.issubset(SQL_TO_KYVOS_XML_MAP.keys())

    def test_alias_map_has_common_aliases(self):
        expected = {"DOUBLE PRECISION", "CHARACTER VARYING", "TIMESTAMPTZ", "UUID", "JSONB"}
        assert expected.issubset(_KYVOS_TYPE_ALIAS.keys())
