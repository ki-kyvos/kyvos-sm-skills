"""Split role-playing dimensions into one dataset per role.

A fact joined to the same dimension on several FK columns (e.g.
``factinternetsales.orderdatekey / duedatekey / shipdatekey -> dimdate``)
is a role-playing ("custom rollup") dimension.  Instead of modelling the
extra roles as DRD alias nodes over a single dataset, each non-base role
gets its own dataset built from the same SQL (``SELECT * FROM
<schema>.dimdate``), e.g. ``Dimdate_Due`` and ``Dimdate_Ship``.  The DRD
then joins every role to a distinct dataset and the semantic model gets
one independent dimension per role.

This module is side-effect free: it takes the spec tables, relationships
and hierarchies and returns rewritten copies plus the bookkeeping the
deployment step needs to compile each role dataset from its source table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from kyvos_sm_skills.contract_adapter import _role_from_fk

_INVALID_DATASET_CHARS = re.compile(r"[^A-Za-z0-9~@#^_-]")


def _pascal_case(name: str) -> str:
    return "".join(w.capitalize() for w in name.split("_"))


def role_dataset_name(base_table_name: str, role: str) -> str:
    """Kyvos dataset name for a role copy, e.g. ``Dimdate_Ship``.

    Kyvos only accepts ``A-Za-z 0-9 ~ @ # ^ _ -`` in dataset names, so the
    ``Dimdate (Ship)`` display form cannot be used.
    """
    return _INVALID_DATASET_CHARS.sub("", f"{_pascal_case(base_table_name)}_{role}")


@dataclass
class RolePlayingSplit:
    """Result of :func:`split_role_playing_dimensions`."""

    tables: list[Any]
    relationships: list[Any]
    hierarchies: list[Any]
    datasets: list[Any] = field(default_factory=list)
    # role table name -> source (base) table name, used to build the SQL
    source_table: dict[str, str] = field(default_factory=dict)
    # role table name -> Kyvos dataset name
    dataset_name: dict[str, str] = field(default_factory=dict)
    # role table name -> role label (e.g. "Ship")
    role: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.source_table)


def split_role_playing_dimensions(
    *,
    tables: list[Any],
    relationships: list[Any],
    hierarchies: list[Any] | None = None,
    datasets: list[Any] | None = None,
) -> RolePlayingSplit:
    """Give every non-base role of a role-playing dimension its own table.

    Roles are derived from the fact FK column (``shipdatekey`` -> ``Ship``).
    The base dimension keeps the ``Order`` role when present, otherwise the
    alphabetically first role.  Roles are decided per dimension across all
    facts, so ``factinternetsales`` and ``factresellersales`` both route
    their ``shipdatekey`` join to the same ``Dimdate_Ship`` dataset.
    A single (non role-playing) join whose role matches an existing role
    copy is routed to that copy too, keeping the model consistent.
    """
    hierarchies = list(hierarchies or [])
    by_lower = {t.name.lower(): t for t in tables}
    fact_lower = {t.name.lower() for t in tables if t.table_type == "fact"}

    def _fact_dim(rel: Any) -> tuple[str, str, str, str] | None:
        """(fact, dim, fk_col, dim_key_col) for a fact<->dimension join."""
        left, right = rel.left_dataset.lower(), rel.right_dataset.lower()
        if (left in fact_lower) == (right in fact_lower):
            return None
        if left in fact_lower:
            return rel.left_dataset, rel.right_dataset, rel.left_column, rel.right_column
        return rel.right_dataset, rel.left_dataset, rel.right_column, rel.left_column

    active = [r for r in relationships if getattr(r, "active", True)]

    # Group fact->dim joins per (fact, dim) to find role-playing pairs.
    pair_rels: dict[tuple[str, str], list[Any]] = {}
    for rel in active:
        fd = _fact_dim(rel)
        if fd is None:
            continue
        fact, dim, _, _ = fd
        if dim.lower() not in by_lower or by_lower[dim.lower()].table_type in ("fact", "bridge"):
            continue
        pair_rels.setdefault((fact.lower(), dim.lower()), []).append(rel)

    # Roles per dimension across every role-playing fact.
    dim_roles: dict[str, set[str]] = {}
    for (_fact, dim), rels in pair_rels.items():
        fk_cols = {(_fact_dim(r) or ("", "", "", ""))[2].lower() for r in rels}
        if len(fk_cols) < 2:
            continue
        for r in rels:
            _, _, fk, dk = _fact_dim(r)  # type: ignore[misc]
            dim_roles.setdefault(dim, set()).add(_role_from_fk(fk, dk))

    datasets = list(datasets or [])
    result = RolePlayingSplit(
        tables=list(tables), relationships=list(relationships),
        hierarchies=hierarchies, datasets=datasets,
    )
    if not dim_roles:
        return result

    # role table name per (dim, role) for every non-base role.
    role_table: dict[tuple[str, str], str] = {}
    existing = {t.name.lower() for t in tables}
    for dim, roles in sorted(dim_roles.items()):
        ordered = sorted(roles, key=str.lower)
        base_role = next((r for r in ordered if r.lower() == "order"), ordered[0])
        base_tbl = by_lower[dim]
        for role in ordered:
            if role == base_role:
                continue
            name = f"{base_tbl.name}_{role.lower()}"
            if name.lower() in existing:
                continue  # never shadow a real warehouse table
            existing.add(name.lower())
            role_table[(dim, role.lower())] = name
            result.source_table[name] = base_tbl.name
            result.dataset_name[name] = role_dataset_name(base_tbl.name, role)
            result.role[name] = role
            result.tables.append(base_tbl.model_copy(update={
                "name": name,
                "description": f"{role} role of {base_tbl.name}",
            }))
            # Semantic-model datasets are validated against hierarchy /
            # relationship names, so the role copy needs its own entry.
            for ds in datasets:
                if ds.name.lower() == dim:
                    result.datasets.append(ds.model_copy(update={"name": name}))
            for h in hierarchies:
                if (h.source_dataset or "").lower() == dim:
                    result.hierarchies.append(h.model_copy(update={
                        "name": f"{h.name} ({role})",
                        "source_dataset": name,
                    }))

    # Route each fact->dim join to its role table.
    rewritten: list[Any] = []
    for rel in relationships:
        fd = _fact_dim(rel) if getattr(rel, "active", True) else None
        target = None
        if fd is not None:
            _, dim, fk, dk = fd
            target = role_table.get((dim.lower(), _role_from_fk(fk, dk).lower()))
        if target is None:
            rewritten.append(rel)
        elif rel.left_dataset.lower() == fd[1].lower():
            rewritten.append(rel.model_copy(update={"left_dataset": target}))
        else:
            rewritten.append(rel.model_copy(update={"right_dataset": target}))
    result.relationships = rewritten
    return result
