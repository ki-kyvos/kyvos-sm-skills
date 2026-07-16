"""Tests for connection generators (JSON + XML)."""

import xml.etree.ElementTree as ET

from kyvos_sm_skills.generators.connection_json import generate_connection_json
from kyvos_sm_skills.generators.connection_xml import generate_connection_xml


class TestConnectionJson:
    def test_basic_generation(self):
        payload = generate_connection_json(
            name="TestConn",
            host="localhost",
            port=5432,
            database="testdb",
            username="user",
            password="pass",
        )
        assert payload["name"] == "TestConn"
        assert payload["accessRights"] == "1"

    def test_properties_present(self):
        payload = generate_connection_json(
            name="Conn",
            host="h",
            port=5432,
            database="db",
            username="u",
            password="p",
        )
        props = payload["configuration"]["property"]
        assert len(props) == 15

    def test_jdbc_url(self):
        payload = generate_connection_json(
            name="C",
            host="myhost",
            port=5433,
            database="mydb",
            username="u",
            password="p",
        )
        url_prop = next(p for p in payload["configuration"]["property"] if p["name"] == "kyvos.connection.url")
        assert "jdbc:postgresql://myhost:5433/mydb" in url_prop["value"]

    def test_password_encrypted(self):
        payload = generate_connection_json(
            name="C", host="h", port=5432, database="d", username="u", password="secret",
        )
        pwd_prop = next(p for p in payload["configuration"]["property"] if p["name"] == "kyvos.connection.password")
        assert pwd_prop["encrypted"] == "true"
        assert pwd_prop["value"] == "secret"

    def test_custom_db_type(self):
        payload = generate_connection_json(
            name="C", host="h", port=5432, database="d", username="u", password="p",
            db_type="SNOWFLAKE",
        )
        provider_prop = next(
            p for p in payload["configuration"]["property"]
            if p["name"] == "kyvos.connection.provider"
        )
        assert provider_prop["value"] == "SNOWFLAKE"


class TestConnectionXml:
    def test_basic_generation(self):
        xml_str = generate_connection_xml(
            name="TestConn",
            host="localhost",
            port=5432,
            database="testdb",
            username="user",
            password="pass",
        )
        root = ET.fromstring(xml_str)
        assert root.tag == "CONNECTION"
        assert root.get("NAME") == "TestConn"

    def test_properties_count(self):
        xml_str = generate_connection_xml(
            name="C", host="h", port=5432, database="d", username="u", password="p",
        )
        root = ET.fromstring(xml_str)
        config = root.find("configuration")
        props = config.findall("property")
        assert len(props) == 15

    def test_jdbc_url_in_xml(self):
        xml_str = generate_connection_xml(
            name="C", host="myhost", port=5433, database="mydb", username="u", password="p",
        )
        root = ET.fromstring(xml_str)
        props = root.findall(".//property")
        url_prop = next(p for p in props if p.find("name").text == "kyvos.connection.url")
        assert "myhost:5433/mydb" in url_prop.find("value").text
