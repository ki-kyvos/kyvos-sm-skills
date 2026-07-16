"""Generate Kyvos-compatible XML for connection creation.

Based on the Kyvos REST API specification for creating connections via XML payload.
"""

from __future__ import annotations

import time
from xml.etree import ElementTree as ET


def generate_connection_xml(
    name: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    db_type: str = "POSTGRES",
    db_version: str = "11",
) -> str:
    """Generate Kyvos connection XML payload.

    Args:
        name: Connection name
        host: Database host
        port: Database port
        database: Database name
        username: Database username
        password: Database password
        db_type: Database type (default: POSTGRES)
        db_version: Database version (default: 11)

    Returns:
        XML string for Kyvos connection creation API
    """
    # Build JDBC URL
    jdbc_url = f"jdbc:postgresql://{host}:{port}/{database}"
    
    # Create CONNECTION element (root element, no RESPONSE wrapper)
    connection = ET.Element("CONNECTION")
    connection.set("NAME", name)
    connection.set("ACCESSRIGHTS", "1")
    
    # Create configuration element
    config = ET.SubElement(connection, "configuration")
    
    # Define properties
    properties = [
        ("kyvos.connection.isEncryptedConnection", "true", "false"),
        ("kyvos.connection.provider", db_type, "false"),
        ("kyvos.connection.properties.version", db_version, "false"),
        ("kyvos.connection.rawdata.enabled", "true", "false"),
        ("kyvos.connection.sqlEngineType", db_type, "false"),
        ("kyvos.connection.rawdata.supported.engines", db_type, "false"),
        ("kyvos.connection.url", jdbc_url, "false"),
        ("kyvos.connection.lastUpdateTimestamp", str(int(time.time() * 1000)), "false"),
        ("kyvos.build.spark.read.method", "JAVA_JDBC", "false"),
        ("kyvos.connection.user", username, "false"),
        ("kyvos.connection.isRead", "true", "false"),
        ("kyvos.connection.password", password, "false"),
        ("kyvos.connection.driver", "org.postgresql.Driver", "false"),
        ("kyvos.connection.defaultsqlengine", "false", "false"),
        ("kyvos.connection.name", name, "false"),
    ]
    
    # Add each property
    for prop_name, prop_value, encrypted in properties:
        prop = ET.SubElement(config, "property")
        
        name_elem = ET.SubElement(prop, "name")
        name_elem.text = prop_name
        
        value_elem = ET.SubElement(prop, "value")
        value_elem.text = prop_value
        
        encrypted_elem = ET.SubElement(prop, "encrypted")
        encrypted_elem.text = encrypted
    
    # Convert to string with proper formatting
    ET.indent(connection, space="    ")
    xml_string = ET.tostring(connection, encoding="unicode", method="xml")
    
    return xml_string
