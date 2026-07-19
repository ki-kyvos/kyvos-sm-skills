"""Adapter to delegate payload generation to SDK compilers.

This module provides functions that adapt local sm-skills models to SDK
contract types and invoke the SDK compilers, returning ``CompiledArtifact``
with deterministic content hashes, diagnostics, and capability requirements.

Existing generators (``generate_connection_xml``, ``DatasetXmlGenerator``,
etc.) remain available for backward compatibility. New code should prefer
the SDK compiler-backed functions in this module.

Install with: ``pip install kyvos-sm-skills[sdk]``
"""

from __future__ import annotations

import hashlib
from typing import Any

from kyvos_sm_skills.generators.drd_xml import SimpleRel


def compile_connection_artifact(
    *,
    name: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    db_type: str = "POSTGRES",
    db_version: str = "11",
    folder_id: str = "",
    folder_name: str = "",
    fmt: str = "xml",
    jdbc_url_override: str = "",
    driver_override: str = "",
) -> Any:
    """Compile a connection into a ``CompiledArtifact`` via SDK compiler.

    Args:
        All args mirror ``kyvos_sm_skills.generators.connection_xml.generate_connection_xml``.
        fmt: "xml" or "json".
        jdbc_url_override: If set, use this JDBC URL instead of the default.
        driver_override: If set, use this driver class instead of the default.

    Returns:
        ``kyvos_sdk.contracts.artifacts.CompiledArtifact`` with payload,
        content_hash, and diagnostics.

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.compiler import compile_connection
        from kyvos_sdk.contracts.artifacts import ArtifactFormat
    except ImportError as exc:
        raise ImportError(
            "kyvos-sdk-python is required for compile_connection_artifact(). "
            "Install it with: pip install kyvos-sm-skills[sdk]"
        ) from exc

    artifact_fmt = ArtifactFormat.JSON if fmt.lower() == "json" else ArtifactFormat.XML
    return compile_connection(
        name=name, host=host, port=port, database=database,
        username=username, password=password,
        db_type=db_type, db_version=db_version,
        folder_id=folder_id, folder_name=folder_name,
        fmt=artifact_fmt,
        jdbc_url_override=jdbc_url_override,
        driver_override=driver_override,
    )


def compile_dataset_artifact(
    table: Any,
    *,
    connection_name: str,
    folder_id: str = "",
    folder_name: str = "Demo Automation",
    fmt: str = "xml",
) -> Any:
    """Compile a dataset into a ``CompiledArtifact`` via SDK compiler.

    Args:
        table: A local ``kyvos_sm_skills.models.TableSpec`` (duck-typed).
        connection_name: Kyvos connection name.
        folder_id: Optional folder ID.
        folder_name: Folder name for dataset category.
        fmt: "xml" or "json".

    Returns:
        ``kyvos_sdk.contracts.artifacts.CompiledArtifact``.

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.contracts.adapters import adapt_table
        from kyvos_sdk.compiler import compile_dataset
        from kyvos_sdk.contracts.artifacts import ArtifactFormat
    except ImportError as exc:
        raise ImportError(
            "kyvos-sdk-python is required for compile_dataset_artifact(). "
            "Install it with: pip install kyvos-sm-skills[sdk]"
        ) from exc

    contract_table = adapt_table(table)
    artifact_fmt = ArtifactFormat.JSON if fmt.lower() == "json" else ArtifactFormat.XML
    return compile_dataset(
        contract_table,
        connection_name=connection_name,
        folder_id=folder_id,
        folder_name=folder_name,
        fmt=artifact_fmt,
    )


def build_drd_graph(
    *,
    drd_name: str,
    drd_id: str,
    dataset_name_to_id: dict[str, str],
    relationships: list[SimpleRel],
    dataset_aliases: dict[str, str] | None = None,
    fact_dataset_names: set[str] | None = None,
) -> Any:
    """Build a ``DrdGraph`` from SimpleRel relationships and dataset ID mapping.

    This constructs a preview DrdGraph suitable for passing to SDK compilers.

    Args:
        drd_name: Name of the DRD.
        drd_id: ID for the DRD entity ref.
        dataset_name_to_id: Mapping from Kyvos dataset name → dataset ID.
        relationships: List of SimpleRel relationships.
        dataset_aliases: Mapping from semantic display name → Kyvos dataset name.
        fact_dataset_names: Optional set of fact dataset names.

    Returns:
        ``kyvos_sdk.contracts.identity.DrdGraph`` (preview).

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.contracts.common import ContractMetadata
        from kyvos_sdk.contracts.identity import (
            DrdGraph,
            DrdNode,
            DrdRelation,
            EntityRef,
            EntityType,
        )
    except ImportError as exc:
        raise ImportError(
            "kyvos-sdk-python is required for build_drd_graph(). "
            "Install it with: pip install kyvos-sm-skills[sdk]"
        ) from exc

    aliases = dataset_aliases or {}
    aliases_ci: dict[str, str] = {k.lower(): v for k, v in aliases.items()}
    id_map_ci: dict[str, str] = {k.lower(): v for k, v in dataset_name_to_id.items()}
    fact_set = fact_dataset_names or set()

    # Ensure drd_id is non-empty for EntityRef validation
    if not drd_id or not drd_id.strip():
        drd_id = "drd_" + hashlib.sha256(drd_name.encode()).hexdigest()[:12]

    # Collect used datasets
    used_names: set[str] = set()
    for rel in relationships:
        used_names.add(rel.left_dataset)
        used_names.add(rel.right_dataset)

    # Resolve to Kyvos names and IDs
    nodes: list[DrdNode] = []
    name_to_node_id: dict[str, str] = {}

    for idx, semantic_name in enumerate(sorted(used_names), start=1):
        kyvos_name = (
            aliases.get(semantic_name)
            or aliases_ci.get(semantic_name.lower())
            or semantic_name
        )
        ds_id = dataset_name_to_id.get(kyvos_name) or id_map_ci.get(kyvos_name.lower())
        if not ds_id:
            continue
        node_id = f"{ds_id}_{idx}"
        name_to_node_id[kyvos_name] = node_id

        node_type = "fact" if kyvos_name in fact_set else ""
        nodes.append(DrdNode(
            node_id=node_id,
            dataset_ref=EntityRef(
                entity_type=EntityType.DATASET,
                id=ds_id,
                name=kyvos_name,
            ),
            alias=kyvos_name,
            node_type=node_type,
        ))

    # Build relations
    relations: list[DrdRelation] = []
    for rel_idx, rel in enumerate(relationships, start=1):
        left_kyvos = aliases.get(rel.left_dataset) or aliases_ci.get(rel.left_dataset.lower()) or rel.left_dataset
        right_kyvos = aliases.get(rel.right_dataset) or aliases_ci.get(rel.right_dataset.lower()) or rel.right_dataset

        left_node_id = name_to_node_id.get(left_kyvos)
        right_node_id = name_to_node_id.get(right_kyvos)
        if not left_node_id or not right_node_id:
            continue

        rel_type = _normalize_rel_type(rel.relationship_type)
        relations.append(DrdRelation(
            relation_id=f"rel_{rel_idx}",
            source_node_id=left_node_id,
            target_node_id=right_node_id,
            source_column=rel.left_column,
            target_column=rel.right_column,
            relation_type=rel_type,
        ))

    return DrdGraph(
        metadata=ContractMetadata(
            contract_version="1.0",
            producer="kyvos-sm-skills/0.2.0",
        ),
        drd_ref=EntityRef(
            entity_type=EntityType.DRD,
            id=drd_id,
            name=drd_name,
        ),
        nodes=nodes,
        relations=relations,
        is_preview=True,
    )


def compile_drd_artifact(
    *,
    drd_name: str,
    drd_id: str,
    folder_id: str,
    folder_name: str,
    dataset_name_to_id: dict[str, str],
    relationships: list[SimpleRel],
    dataset_aliases: dict[str, str] | None = None,
    fact_dataset_names: set[str] | None = None,
    fmt: str = "xml",
) -> Any:
    """Compile a DRD into a ``CompiledArtifact`` via SDK compiler.

    Builds a DrdGraph from the relationships and delegates to
    ``kyvos_sdk.compiler.compile_drd``.

    Args:
        drd_name: Name of the DRD.
        drd_id: ID for the DRD.
        folder_id: DRD folder ID.
        folder_name: DRD folder name.
        dataset_name_to_id: Mapping from Kyvos dataset name → dataset ID.
        relationships: List of SimpleRel relationships.
        dataset_aliases: Optional semantic→Kyvos name mapping.
        fact_dataset_names: Optional set of fact dataset names.
        fmt: "xml" or "json".

    Returns:
        ``kyvos_sdk.contracts.artifacts.CompiledArtifact``.

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.compiler import compile_drd
        from kyvos_sdk.contracts.artifacts import ArtifactFormat
    except ImportError as exc:
        raise ImportError(
            "kyvos-sdk-python is required for compile_drd_artifact(). "
            "Install it with: pip install kyvos-sm-skills[sdk]"
        ) from exc

    graph = build_drd_graph(
        drd_name=drd_name,
        drd_id=drd_id,
        dataset_name_to_id=dataset_name_to_id,
        relationships=relationships,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
    )
    artifact_fmt = ArtifactFormat.JSON if fmt.lower() == "json" else ArtifactFormat.XML
    return compile_drd(
        graph,
        drd_name=drd_name,
        folder_id=folder_id,
        folder_name=folder_name,
        dataset_name_to_id=dataset_name_to_id,
        fmt=artifact_fmt,
    )


def compile_smodel_artifact(
    smodel: Any,
    *,
    drd_name: str,
    drd_id: str,
    folder_id: str,
    folder_name: str,
    connection_name: str,
    dataset_name_to_id: dict[str, str],
    relationships: list[SimpleRel],
    dataset_aliases: dict[str, str] | None = None,
    fact_dataset_names: set[str] | None = None,
    dataset_columns: dict[str, list[dict]] | None = None,
    fmt: str = "xml",
) -> Any:
    """Compile a semantic model into a ``CompiledArtifact`` via SDK compiler.

    Adapts the local SemanticModelSpec to a contract SemanticModelSpec,
    builds a DrdGraph, and delegates to ``kyvos_sdk.compiler.compile_semantic_model``.

    Args:
        smodel: A local ``kyvos_sm_skills.models.SemanticModelSpec``.
        drd_name: Name of the DRD.
        drd_id: ID of the DRD.
        folder_id: Semantic model folder ID.
        folder_name: Semantic model folder name.
        connection_name: Kyvos connection name.
        dataset_name_to_id: Mapping from Kyvos dataset name → dataset ID.
        relationships: List of SimpleRel relationships for DRD graph.
        dataset_aliases: Optional semantic→Kyvos name mapping.
        fact_dataset_names: Optional set of fact dataset names.
        dataset_columns: Optional dataset columns metadata.
        fmt: "xml" or "json".

    Returns:
        ``kyvos_sdk.contracts.artifacts.CompiledArtifact``.

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.contracts.adapters import (
            adapt_semantic_model,
        )
        from kyvos_sdk.compiler import compile_semantic_model
        from kyvos_sdk.contracts.artifacts import ArtifactFormat
    except ImportError as exc:
        raise ImportError(
            "kyvos-sdk-python is required for compile_smodel_artifact(). "
            "Install it with: pip install kyvos-sm-skills[sdk]"
        ) from exc

    contract_smodel = adapt_semantic_model(smodel)
    graph = build_drd_graph(
        drd_name=drd_name,
        drd_id=drd_id,
        dataset_name_to_id=dataset_name_to_id,
        relationships=relationships,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
    )
    artifact_fmt = ArtifactFormat.JSON if fmt.lower() == "json" else ArtifactFormat.XML
    return compile_semantic_model(
        contract_smodel,
        graph=graph,
        folder_id=folder_id,
        folder_name=folder_name,
        connection_name=connection_name,
        drd_id=drd_id,
        drd_name=drd_name,
        dataset_name_to_id=dataset_name_to_id,
        dataset_columns=dataset_columns,
        fmt=artifact_fmt,
    )


def _normalize_rel_type(rel_type: str | None) -> str:
    value = (rel_type or "").strip().lower()
    if value in {"many_to_many", "manytomany"}:
        return "MANY_TO_MANY"
    if value in {"one_to_one", "onetoone"}:
        return "ONE_TO_ONE"
    return "ONE_TO_MANY"
