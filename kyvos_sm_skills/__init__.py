"""kyvos-sm-skills: Claude skills + generators for Kyvos semantic model creation.

Internal model definitions live in ``kyvos_sm_skills.models`` — use
``kyvos_sdk.contracts.domain`` for contract-typed models.
Use :func:`compile_connection_artifact`, :func:`compile_dataset_artifact`,
:func:`compile_drd_artifact`, :func:`compile_smodel_artifact` for
contract-typed ``CompiledArtifact`` output with content hashes and diagnostics.
"""

from __future__ import annotations

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

__version__ = "1.0.0"

__all__ = [
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
    # SDK compiler-backed adapters
    "compile_connection_artifact",
    "compile_dataset_artifact",
    "compile_drd_artifact",
    "compile_smodel_artifact",
    "build_drd_graph",
]
