"""kyvos-sm-skills: Claude skills + generators for Kyvos semantic model creation.

.. deprecated:: v0.2.0
    Local model re-exports (``ColumnSpec``, ``TableSpec``, etc.) are deprecated.
    Use ``kyvos_sdk.contracts.domain`` types instead.
    Install with: ``pip install kyvos-sm-skills[sdk]`` and use
    :func:`compile_connection_artifact`, :func:`compile_dataset_artifact`,
    :func:`compile_drd_artifact`, :func:`compile_smodel_artifact` for
    contract-typed ``CompiledArtifact`` output with content hashes and diagnostics.
"""

from __future__ import annotations

import warnings as _warnings

from kyvos_sm_skills.models import (
    ColumnSpec,
    DatasetSpec,
    HierarchySpec,
    MeasureSpec,
    RelationshipSpec,
    SemanticModelSpec,
    TableSpec,
)

_warnings.warn(
    "kyvos_sm_skills model re-exports are deprecated since v0.2.0. "
    "Use kyvos_sdk.contracts.domain types instead. "
    "Install kyvos-sm-skills[sdk] and use compile_*_artifact() for contract-typed output. "
    "Legacy models will be removed in v1.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

from kyvos_sm_skills.contract_adapter import (
    build_drd_graph,
    compile_connection_artifact,
    compile_dataset_artifact,
    compile_drd_artifact,
    compile_smodel_artifact,
)
from kyvos_sm_skills.generators import (
    DatasetJsonGenerator,
    DatasetXmlGenerator,
    DrdJsonGenerator,
    DrdXmlGenerator,
    SModelJsonGenerator,
    SModelXmlGenerator,
    SimpleRel,
    generate_connection_json,
    generate_connection_xml,
)

__version__ = "0.2.0"

__all__ = [
    # Legacy models (deprecated)
    "ColumnSpec",
    "TableSpec",
    "DatasetSpec",
    "RelationshipSpec",
    "MeasureSpec",
    "HierarchySpec",
    "SemanticModelSpec",
    # Legacy generators (backward compatible)
    "generate_connection_xml",
    "generate_connection_json",
    "DatasetXmlGenerator",
    "DatasetJsonGenerator",
    "DrdXmlGenerator",
    "DrdJsonGenerator",
    "SModelXmlGenerator",
    "SModelJsonGenerator",
    "SimpleRel",
    # SDK compiler-backed adapters (new)
    "compile_connection_artifact",
    "compile_dataset_artifact",
    "compile_drd_artifact",
    "compile_smodel_artifact",
    "build_drd_graph",
]
