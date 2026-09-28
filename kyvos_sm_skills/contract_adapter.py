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
import logging
from collections import deque
from typing import Any

from kyvos_sm_skills.generators.drd_xml import SimpleRel

_log = logging.getLogger(__name__)


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
    db_type: str = "POSTGRES",
) -> Any:
    """Compile a dataset into a ``CompiledArtifact`` via SDK compiler.

    Args:
        table: A local ``kyvos_sm_skills.models.TableSpec`` (duck-typed).
        connection_name: Kyvos connection name.
        folder_id: Optional folder ID.
        folder_name: Folder name for dataset category.
        fmt: "xml" or "json".
        db_type: Database/connection type for the dataset (e.g. "POSTGRES", "DATABRICKSSQL").

    Returns:
        ``kyvos_sdk.contracts.artifacts.CompiledArtifact``.

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.compiler import compile_dataset
        from kyvos_sdk.contracts.adapters import adapt_table
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
        db_type=db_type,
    )


def _resolve_alias(name: str, aliases: dict[str, str], aliases_ci: dict[str, str]) -> str:
    return aliases.get(name) or aliases_ci.get(name.lower()) or name


def _prune_snowflake_parents(
    relationships: list[SimpleRel],
    *,
    aliases: dict[str, str],
    aliases_ci: dict[str, str],
    fact_set: set[str],
    bridge_set: set[str],
    dropped: list[str] | None = None,
) -> list[SimpleRel]:
    """Prune ambiguous snowflake dim->dim joins that Kyvos cannot compile.

    Two cases are handled:

    1. Child with many parents — a snowflake child dimension joined to more
       than one parent dimension. In a multi-fact Kyvos model a shared
       dimension must be reachable from every measure group through the same
       join path, so we keep only the parent with the most direct fact
       connections and discard the others.
    2. Shared outrigger — one parent dimension referenced by several child
       dimensions via dim->dim joins (e.g. DimCustomer/DimReseller/
       DimSalesTerritory all -> DimGeography). The outrigger then has
       ambiguous paths to the facts, so unless the parent itself is directly
       joined to a fact/bridge (a conformed dimension — left alone), we keep
       exactly one child join: the child with the most direct fact edges,
       tie-broken alphabetically (case-insensitive).

    When ``dropped`` is a list, a human-readable line is appended for each
    dropped join so callers can log them.
    """
    def resolve(name: str) -> str:
        return _resolve_alias(name, aliases, aliases_ci)

    def is_fact_or_bridge(name: str) -> bool:
        n = resolve(name)
        return n in fact_set or n in bridge_set

    # Count direct fact-to-parent edges for every candidate parent dimension.
    parent_fact_edges: dict[str, int] = {}
    for rel in relationships:
        left = resolve(rel.left_dataset)
        right = resolve(rel.right_dataset)
        left_fb = is_fact_or_bridge(left)
        right_fb = is_fact_or_bridge(right)
        if left_fb and not right_fb:
            parent_fact_edges[right] = parent_fact_edges.get(right, 0) + 1
        elif right_fb and not left_fb:
            parent_fact_edges[left] = parent_fact_edges.get(left, 0) + 1

    # Map each snowflake child dimension to the dim->dim relationships that
    # connect it to a candidate parent.
    child_to_parents: dict[str, list[tuple[int, str, SimpleRel]]] = {}
    for idx, rel in enumerate(relationships):
        left = resolve(rel.left_dataset)
        right = resolve(rel.right_dataset)
        if is_fact_or_bridge(left) or is_fact_or_bridge(right):
            continue
        rel_type = _normalize_rel_type(rel.relationship_type)
        if rel_type == "MANY_TO_ONE":
            child, parent = left, right
        elif rel_type == "ONE_TO_MANY":
            child, parent = right, left
        else:
            continue
        child_to_parents.setdefault(child, []).append((idx, parent, rel))

    # Drop redundant parent relationships. Skip children that are already
    # directly joined to facts; those are conformed dimensions, not snowflake
    # leaves, and we should not alter their graph connections.
    to_drop: set[int] = set()
    for child, candidates in child_to_parents.items():
        if len(candidates) <= 1:
            continue
        # Count direct fact edges to the child itself.
        child_fact_edges = sum(
            1
            for rel in relationships
            if (
                (is_fact_or_bridge(resolve(rel.left_dataset)) and resolve(rel.right_dataset) == child)
                or (is_fact_or_bridge(resolve(rel.right_dataset)) and resolve(rel.left_dataset) == child)
            )
        )
        if child_fact_edges:
            continue
        # Keep the parent that is directly joined to the most facts.
        # Ties are broken deterministically by parent name.
        ordered = sorted(
            candidates,
            key=lambda item: (-parent_fact_edges.get(item[1], 0), item[1].lower()),
        )
        for drop_idx, dropped_parent, _ in ordered[1:]:
            to_drop.add(drop_idx)
            if dropped is not None:
                dropped.append(
                    f"Dropped dim->dim join {child} -> {dropped_parent} "
                    f"(kept {child} -> {ordered[0][1]}; multiple parents)"
                )

    # ── Pass 2: shared outrigger — one parent dim with several child dims ──
    # Operate on the relationships that survived pass 1.
    surviving = [rel for idx, rel in enumerate(relationships) if idx not in to_drop]

    parent_to_children: dict[str, list[tuple[int, str, SimpleRel]]] = {}
    for idx, rel in enumerate(surviving):
        left = resolve(rel.left_dataset)
        right = resolve(rel.right_dataset)
        if is_fact_or_bridge(left) or is_fact_or_bridge(right):
            continue
        rel_type = _normalize_rel_type(rel.relationship_type)
        if rel_type == "MANY_TO_ONE":
            child, parent = left, right
        elif rel_type == "ONE_TO_MANY":
            child, parent = right, left
        else:
            continue
        parent_to_children.setdefault(parent, []).append((idx, child, rel))

    drop2: set[int] = set()
    for parent, candidates in parent_to_children.items():
        if len(candidates) <= 1:
            continue
        # A parent directly joined to a fact/bridge is a conformed
        # dimension — every child path is valid; leave it alone.
        if parent_fact_edges.get(parent, 0):
            continue
        # Keep the child directly joined to the most facts; ties broken
        # alphabetically (case-insensitive) for determinism.
        ordered = sorted(
            candidates,
            key=lambda item: (-parent_fact_edges.get(item[1], 0), item[1].lower()),
        )
        keep_child = ordered[0][1]
        for drop_idx, child, _rel in ordered[1:]:
            drop2.add(drop_idx)
            if dropped is not None:
                dropped.append(
                    f"Dropped dim->dim join {child} -> {parent} "
                    f"(kept {keep_child} -> {parent}; shared outrigger)"
                )

    return [rel for idx, rel in enumerate(surviving) if idx not in drop2]


def _role_from_fk(fk_col: str, dim_key_col: str) -> str:
    """Derive a role label for a role-playing dimension alias.

    Examples: ``orderdatekey`` vs dim key ``datekey`` -> ``Order``;
    ``shipdatekey`` -> ``Ship``; ``due_date_key`` -> ``DueDate``.
    Falls back to the FK column itself when no role can be extracted.
    """
    fk = (fk_col or "").strip().lower()
    dk = (dim_key_col or "").strip().lower()
    if dk and fk != dk and fk.endswith(dk):
        role = fk[: -len(dk)]
    else:
        role = fk
        for suf in ("_key", "key", "_id", "id"):
            if role.endswith(suf) and len(role) > len(suf):
                role = role[: -len(suf)]
                break
    role = role.strip("_")
    if not role:
        return fk_col
    return role.replace("_", " ").title().replace(" ", "")


def build_drd_graph(
    *,
    drd_name: str,
    drd_id: str,
    dataset_name_to_id: dict[str, str],
    relationships: list[SimpleRel],
    dataset_aliases: dict[str, str] | None = None,
    fact_dataset_names: set[str] | None = None,
    bridge_dataset_names: set[str] | None = None,
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
        bridge_dataset_names: Optional set of bridge dataset names.

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
    bridge_set = bridge_dataset_names or set()

    def _resolve_name(semantic_name: str) -> str:
        return (
            aliases.get(semantic_name)
            or aliases_ci.get(semantic_name.lower())
            or semantic_name
        )

    # Endpoint names before any pruning — used below to warn when a dataset
    # loses ALL of its joins and would silently have no DRD node.
    _orig_endpoint_names = {
        _resolve_name(r.left_dataset) for r in relationships
    } | {
        _resolve_name(r.right_dataset) for r in relationships
    }

    _pruned: list[str] = []
    relationships = _prune_snowflake_parents(
        relationships,
        aliases=aliases,
        aliases_ci=aliases_ci,
        fact_set=fact_set,
        bridge_set=bridge_set,
        dropped=_pruned,
    )

    # Group relationships by the unordered pair of resolved dataset names.
    # Kyvos does not allow two relations between the same pair of DRD nodes:
    #   * a fact joined to the same dimension on several FK columns is a
    #     role-playing dimension — each extra join becomes a separate DRD
    #     node sharing the dataset id but carrying a role alias (handled
    #     after base nodes are created below);
    #   * any other repeated pair (fact->bridge, dim->dim, ...) is a true
    #     duplicate — keep only the first relationship.
    pair_groups: dict[tuple[str, str], list[int]] = {}
    for _i, _rel in enumerate(relationships):
        _l = _resolve_name(_rel.left_dataset)
        _r = _resolve_name(_rel.right_dataset)
        _key = (_l, _r) if _l.lower() <= _r.lower() else (_r, _l)
        pair_groups.setdefault(_key, []).append(_i)

    _drop_idx: set[int] = set()
    _roleplay_pairs: dict[tuple[str, str], list[int]] = {}
    for _key, _idxs in pair_groups.items():
        if len(_idxs) <= 1:
            continue
        _a, _b = _key
        if (_a in fact_set) != (_b in fact_set) and _a not in bridge_set and _b not in bridge_set:
            _roleplay_pairs[_key] = _idxs
            continue
        for _i in _idxs[1:]:
            _drop_idx.add(_i)
            _rel = relationships[_i]
            _pruned.append(
                f"Dropped duplicate join {_rel.left_dataset} -> {_rel.right_dataset} "
                f"(same dataset pair already joined)"
            )

    # kept carries (original_index, rel) so alias-node overrides can be
    # looked up by the index recorded in pair_groups.
    kept: list[tuple[int, SimpleRel]] = [
        (i, r) for i, r in enumerate(relationships) if i not in _drop_idx
    ]

    # A dataset whose last join was pruned ends up with no DRD node at all —
    # warn loudly; a dropped edge is routine, a dropped dimension is not.
    _kept_names = set()
    for _i, _r in kept:
        _kept_names.add(_resolve_name(_r.left_dataset))
        _kept_names.add(_resolve_name(_r.right_dataset))
    for _name in sorted(_orig_endpoint_names - _kept_names, key=str.lower):
        _pruned.append(
            f"Dataset '{_name}' lost all joins during pruning — it will be "
            f"absent from the DRD (review the snowflake/outrigger rules)"
        )

    for _line in _pruned:
        print(f"  Snowflake prune: {_line}")

    # Ensure drd_id is non-empty for EntityRef validation
    if not drd_id or not drd_id.strip():
        drd_id = "drd_" + hashlib.sha256(drd_name.encode()).hexdigest()[:12]

    # Collect used datasets
    used_names: set[str] = set()
    for _i, rel in kept:
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

        node_type = "fact" if kyvos_name in fact_set else ("bridge" if kyvos_name in bridge_set else "")
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

    # Role-playing dimensions: a fact joined to the same dimension on
    # several FK columns becomes multiple DRD nodes sharing the same
    # dataset id but with distinct node ids and alias names (matches the
    # real Kyvos AdventureWorks export: DimDate1 / Shipdate / Order Date).
    # The first join keeps the base dimension node; each subsequent join
    # gets an alias node whose dataset_ref.name stays the real dataset
    # name so column lookups keep working.
    alias_node_for_rel: dict[int, tuple[str, str]] = {}  # rel idx -> (dim name, alias node_id)
    next_node_idx = len(used_names) + 1
    for _key in sorted(_roleplay_pairs, key=lambda k: (k[0].lower(), k[1].lower())):
        _a, _b = _key
        fact_name = _a if _a in fact_set else _b
        dim_name = _b if _a in fact_set else _a
        idxs = _roleplay_pairs[_key]
        ds_id = dataset_name_to_id.get(dim_name) or id_map_ci.get(dim_name.lower())
        if not ds_id or dim_name not in name_to_node_id:
            continue

        def _fk(i: int) -> str:
            r = relationships[i]
            return r.left_column if _resolve_name(r.left_dataset) == fact_name else r.right_column

        def _dk(i: int) -> str:
            r = relationships[i]
            return r.right_column if _resolve_name(r.left_dataset) == fact_name else r.left_column

        ordered = sorted(idxs, key=lambda i: (_fk(i).lower(), i))
        roles = {i: _role_from_fk(_fk(i), _dk(i)) for i in ordered}
        # The base node goes to the "order" role when present (the
        # canonical date role), else the first FK alphabetically.
        base_i = next((i for i in ordered if roles[i].lower() == "order"), ordered[0])
        for i in ordered:
            if i == base_i:
                continue
            alias_node_id = f"{ds_id}_{next_node_idx}"
            next_node_idx += 1
            nodes.append(DrdNode(
                node_id=alias_node_id,
                dataset_ref=EntityRef(
                    entity_type=EntityType.DATASET,
                    id=ds_id,
                    name=dim_name,
                ),
                alias=f"{dim_name} ({roles[i]})",
                node_type="",
            ))
            alias_node_for_rel[i] = (dim_name, alias_node_id)

    # BFS depth of each node from the nearest fact node (undirected). Used
    # to order snowflake dim->dim edges so the dim closer to a fact sits on
    # node1 — the relation list then reads fact -> dim -> dim.
    _adj: dict[str, set[str]] = {}
    for _i, rel in kept:
        _l = aliases.get(rel.left_dataset) or aliases_ci.get(rel.left_dataset.lower()) or rel.left_dataset
        _r = aliases.get(rel.right_dataset) or aliases_ci.get(rel.right_dataset.lower()) or rel.right_dataset
        if _l in name_to_node_id and _r in name_to_node_id:
            _adj.setdefault(_l, set()).add(_r)
            _adj.setdefault(_r, set()).add(_l)
    _depth: dict[str, int] = {}
    _queue: deque[str] = deque(sorted(n for n in _adj if n in fact_set))
    for _n in _queue:
        _depth[_n] = 0
    while _queue:
        _cur = _queue.popleft()
        for _nb in _adj.get(_cur, ()):
            if _nb not in _depth:
                _depth[_nb] = _depth[_cur] + 1
                _queue.append(_nb)

    # Build relations
    # Source/target and the join columns are preserved exactly as they are
    # declared in the semantic model.  The relationship type is normalized to
    # Kyvos values (e.g. MANY_TO_ONE stays MANY_TO_ONE) so the DRD reflects the
    # original fact -> dimension direction.
    relations: list[DrdRelation] = []
    for rel_idx, (orig_i, rel) in enumerate(kept, start=1):
        left_kyvos = aliases.get(rel.left_dataset) or aliases_ci.get(rel.left_dataset.lower()) or rel.left_dataset
        right_kyvos = aliases.get(rel.right_dataset) or aliases_ci.get(rel.right_dataset.lower()) or rel.right_dataset

        left_node_id = name_to_node_id.get(left_kyvos)
        right_node_id = name_to_node_id.get(right_kyvos)
        if not left_node_id or not right_node_id:
            continue

        # Kyvos DRD convention (verified against a real Kyvos DRD export):
        #   node1 / sourceId = the FK-holding "many" side (fact or child
        #     dimension) — displayed on the LEFT.
        #   node2 = the referenced "one" side (dimension or parent
        #     dimension) — displayed on the RIGHT.
        #   TYPE = "ONE_TO_MANY" for every resolved FK join (it is a fixed
        #     label, not a literal node1:node2 cardinality); MANY_TO_MANY is
        #     used only for genuine M:M edges (fact -> bridge).
        #
        # This makes the relationship list read fact -> dim and, in a
        # snowflake, fact -> dim -> dim (child dim left, parent dim right).
        rel_type = _normalize_rel_type(rel.relationship_type)
        right_is_fact = right_kyvos in fact_set
        left_is_fact = left_kyvos in fact_set
        if rel_type == "MANY_TO_MANY":
            # Keep declared order for the genuine M:M edge (fact -> bridge).
            # A bridge -> dimension edge is a regular FK join in Kyvos even
            # if the LLM declared it many_to_many: the many side (bridge)
            # stays on node1 and the label becomes ONE_TO_MANY.
            if left_kyvos in bridge_set and right_kyvos not in fact_set and right_kyvos not in bridge_set:
                rel_type = "ONE_TO_MANY"
            source, target, source_col, target_col = (
                left_kyvos,
                right_kyvos,
                rel.left_column,
                rel.right_column,
            )
        elif right_is_fact and not left_is_fact and left_kyvos not in bridge_set:
            # A plain dimension was recorded as the FK/left side of a
            # relationship into a fact table. Swap so the fact is node1,
            # matching every other fact<->dimension edge in the DRD.
            source, target, source_col, target_col = (
                right_kyvos,
                left_kyvos,
                rel.right_column,
                rel.left_column,
            )
            rel_type = "ONE_TO_MANY"
        elif rel_type == "ONE_TO_MANY":
            # Declared one->many: the many side is on the right — put it
            # on node1 so the edge reads many-side -> one-side.
            source, target, source_col, target_col = (
                right_kyvos,
                left_kyvos,
                rel.right_column,
                rel.left_column,
            )
            rel_type = "ONE_TO_MANY"
        elif rel_type == "ONE_TO_ONE":
            source, target, source_col, target_col = (
                left_kyvos,
                right_kyvos,
                rel.left_column,
                rel.right_column,
            )
        else:
            # MANY_TO_ONE (or unspecified): the declared left side is the
            # FK/many side — keep it on node1.
            source, target, source_col, target_col = _resolve_drd_source_target(
                rel=rel,
                left_name=left_kyvos,
                right_name=right_kyvos,
            )
            rel_type = "ONE_TO_MANY"

        # For resolved ONE_TO_MANY edges, keep the endpoint closer to a fact
        # on node1. Facts (depth 0) and bridges (depth 1) always stay first;
        # for snowflake dim -> dim edges this puts the fact-adjacent dim on
        # the left so the chain reads fact -> dim -> dim.
        if rel_type == "ONE_TO_MANY" and _depth.get(target, 1 << 30) < _depth.get(source, 1 << 30):
            source, target, source_col, target_col = target, source, target_col, source_col

        # A role-playing join points its dimension endpoint at the alias
        # node created for this specific relationship.
        _ovr = alias_node_for_rel.get(orig_i)
        source_node_id = _ovr[1] if _ovr and source == _ovr[0] else name_to_node_id[source]
        target_node_id = _ovr[1] if _ovr and target == _ovr[0] else name_to_node_id[target]
        relations.append(DrdRelation(
            relation_id=f"rel_{rel_idx}",
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            source_column=source_col,
            target_column=target_col,
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
    bridge_dataset_names: set[str] | None = None,
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
        bridge_dataset_names: Optional set of bridge dataset names.
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
        bridge_dataset_names=bridge_dataset_names,
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
    bridge_dataset_names: set[str] | None = None,
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
        bridge_dataset_names: Optional set of bridge dataset names.
        dataset_columns: Optional dataset columns metadata.
        fmt: "xml" or "json".

    Returns:
        ``kyvos_sdk.contracts.artifacts.CompiledArtifact``.

    Raises:
        ImportError: If ``kyvos-sdk-python`` is not installed.
    """
    try:
        from kyvos_sdk.compiler import compile_semantic_model
        from kyvos_sdk.contracts.adapters import (
            adapt_semantic_model,
        )
        from kyvos_sdk.contracts.artifacts import ArtifactFormat
    except ImportError as exc:
        raise ImportError(
            "kyvos-sdk-python is required for compile_smodel_artifact(). "
            "Install it with: pip install kyvos-sm-skills[sdk]"
        ) from exc

    # Remap dataset names, relationship names, measure/hierarchy source_dataset
    # from XMLA names to CamelCase server names BEFORE adaptation, since the
    # contract validator checks source_dataset against dataset names during
    # adapt_semantic_model().
    # Work on a deep copy so the caller's original spec is not mutated.
    if dataset_aliases:
        smodel = smodel.model_copy(deep=True)
        for ds in smodel.datasets:
            mapped = dataset_aliases.get(ds.name)
            if mapped:
                ds.name = mapped
        for rel in smodel.relationships:
            mapped_l = dataset_aliases.get(rel.left_dataset)
            if mapped_l:
                rel.left_dataset = mapped_l
            mapped_r = dataset_aliases.get(rel.right_dataset)
            if mapped_r:
                rel.right_dataset = mapped_r
        for m in smodel.measures:
            if m.source_dataset:
                mapped = dataset_aliases.get(m.source_dataset)
                if mapped:
                    m.source_dataset = mapped
        for h in smodel.hierarchies:
            if h.source_dataset:
                mapped = dataset_aliases.get(h.source_dataset)
                if mapped:
                    h.source_dataset = mapped

    # Defensive: drop measures/hierarchies whose source_dataset references a
    # dataset that no longer exists in the model (e.g. Power BI measure-only
    # tables with zero columns that were filtered out upstream).
    _ds_names = {ds.name for ds in smodel.datasets}
    _orphan_measures = [
        m.name for m in smodel.measures
        if m.source_dataset and m.source_dataset not in _ds_names
    ]
    _orphan_hierarchies = [
        h.name for h in smodel.hierarchies
        if h.source_dataset and h.source_dataset not in _ds_names
    ]
    _already_copied = bool(dataset_aliases)
    if (_orphan_measures or _orphan_hierarchies) and not _already_copied:
        smodel = smodel.model_copy(deep=True)
        _already_copied = True
    if _orphan_measures:
        _log.warning(
            "Dropping %d measures with orphan source_dataset: %s",
            len(_orphan_measures), _orphan_measures,
        )
        smodel.measures = [
            m for m in smodel.measures
            if not m.source_dataset or m.source_dataset in _ds_names
        ]
    if _orphan_hierarchies:
        _log.warning(
            "Dropping %d hierarchies with orphan source_dataset: %s",
            len(_orphan_hierarchies), _orphan_hierarchies,
        )
        smodel.hierarchies = [
            h for h in smodel.hierarchies
            if not h.source_dataset or h.source_dataset in _ds_names
        ]

    contract_smodel = adapt_semantic_model(smodel)

    graph = build_drd_graph(
        drd_name=drd_name,
        drd_id=drd_id,
        dataset_name_to_id=dataset_name_to_id,
        relationships=relationships,
        dataset_aliases=dataset_aliases,
        fact_dataset_names=fact_dataset_names,
        bridge_dataset_names=bridge_dataset_names,
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

def _resolve_drd_source_target(
    *,
    rel: SimpleRel,
    left_name: str,
    right_name: str,
) -> tuple[str, str, str, str]:
    """Return (source_name, target_name, source_column, target_column).

    The semantic model already stores fact -> dimension relationships with
    the correct direction (left is the many side, right is the one side for
    many_to_one), regardless of the declared ``relationship_type``. Preserve
    that direction in the DRD so the relationship list and join expression
    read fact-first in the UI.
    """
    return left_name, right_name, rel.left_column, rel.right_column


def _normalize_rel_type(rel_type: str | None) -> str:
    value = (rel_type or "").strip().lower().replace("_", "")
    if value in {"manytomany"}:
        return "MANY_TO_MANY"
    if value in {"onetoone"}:
        return "ONE_TO_ONE"
    if value in {"onetomany"}:
        return "ONE_TO_MANY"
    if value in {"manytoone"}:
        return "MANY_TO_ONE"
    return "ONE_TO_MANY"
