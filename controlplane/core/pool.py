"""Warm cluster pool claiming (docs/TODO.md Task 2.5).

Provisioning from cold takes minutes. Keeping a small number of pre-provisioned
clusters idle lets most requests be served instantly.

The critical property is that two concurrent provisions must never claim the
same cluster. That is handled with ``SELECT … FOR UPDATE SKIP LOCKED``, which
makes the claim atomic at the database level rather than relying on a
check-then-act sequence in Python that two workers can interleave.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.models import PooledCluster
from controlplane.schemas.spec import InfraSpec


def spec_hash(spec: InfraSpec) -> str:
    """Stable fingerprint of everything that affects the provisioned shape.

    Only the fields that change the resulting infrastructure are included —
    the project name and network are per-project and must not participate, or
    no two projects would ever share a pooled cluster.
    """
    shape = {
        "mode": spec.mode,
        "config": spec.config.model_dump(mode="json"),
        "nodes": sorted(
            (
                {
                    "vcpu": node.vcpu,
                    "memory_mb": node.memory_mb,
                    "disk_gb": node.disk_gb,
                    "role": node.role,
                }
                for node in spec.nodes
            ),
            key=lambda node: (node["role"], node["vcpu"], node["memory_mb"], node["disk_gb"]),
        ),
    }
    encoded = json.dumps(shape, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def claim_cluster(db: Session, spec: InfraSpec, project_id: uuid.UUID) -> PooledCluster | None:
    """Atomically claim an available warm cluster matching ``spec``.

    Returns ``None`` when the pool has nothing suitable, in which case the
    caller provisions normally.
    """
    wanted = spec_hash(spec)
    cluster = db.scalar(
        select(PooledCluster)
        .where(PooledCluster.spec_hash == wanted, PooledCluster.status == "available")
        .order_by(PooledCluster.created_at)
        .limit(1)
        # SKIP LOCKED means a concurrent claimer takes the next row instead of
        # blocking on this one, so the two never collide.
        .with_for_update(skip_locked=True)
    )
    if cluster is None:
        return None

    cluster.status = "claimed"
    cluster.claimed_by_project_id = project_id
    db.flush()
    return cluster


def pool_target(spec_fingerprint: str, configured: dict[str, int]) -> int:
    """How many warm clusters to keep for a given spec shape."""
    return configured.get(spec_fingerprint, 0)
