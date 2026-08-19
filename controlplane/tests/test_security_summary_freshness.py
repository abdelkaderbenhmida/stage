"""The security summary must report the LATEST scan, and must not let a
failed scan read as a clean one.

Two failures this guards, both of which make a project look safer than it is:

1. `list_all` returns newest-first, but the summary assigned into
   `latest_by_tool` on every iteration, so the last write — the OLDEST scan —
   won. A project that fixed nothing still showed its first-ever numbers, and
   a project that regressed still showed its old clean ones.

2. `current` only counts completed scans. When a tool's latest run fails it
   contributes nothing, so a project whose scans all failed reported zero of
   every severity: byte-identical to a genuinely clean project. That is the
   same false negative `_is_usable_trivy_report` exists to prevent on the
   gate side — an unscanned target is not a safe target.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from controlplane.models import Scan

pytestmark = pytest.mark.integration

NS_SPEC = {
    "version": 1,
    "project": "sec-summary",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [{"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}],
}


@pytest.fixture()
def project(client, auth_headers):
    """A project owned by a freshly registered user, created through the API
    so its team and ownership are wired exactly as in production."""
    auth = auth_headers(email=f"sec-{uuid.uuid4().hex[:8]}@example.com")
    resp = client.post(
        "/api/v1/projects",
        json={"name": f"sec-{uuid.uuid4().hex[:6]}", "infra_spec": NS_SPEC},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], auth


def _scan(session, project_id, tool, status, summary, minutes_ago):
    scan = Scan(
        project_id=uuid.UUID(project_id),
        tool=tool,
        target="https://github.com/org/repo.git",
        status=status,
        summary=summary,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    session.add(scan)
    session.commit()
    return scan


def _summary(client, project_id, auth):
    resp = client.get(f"/api/v1/projects/{project_id}/security/summary", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_summary_reports_the_latest_scan_not_the_first(client, session, project):
    """An old noisy scan must not outrank a newer clean one."""
    project_id, auth = project
    _scan(session, project_id, "trivy", "completed",
          {"critical": 5, "high": 5, "medium": 0, "low": 0, "unknown": 0}, minutes_ago=120)
    _scan(session, project_id, "trivy", "completed",
          {"critical": 0, "high": 1, "medium": 0, "low": 0, "unknown": 0}, minutes_ago=5)

    current = _summary(client, project_id, auth)["current"]

    assert current["critical"] == 0, f"reported an older scan's criticals: {current}"
    assert current["high"] == 1, current


def test_a_failed_latest_scan_is_reported_not_silently_zeroed(client, session, project):
    """Zero findings from a scan that never ran must be flagged, not implied clean."""
    project_id, auth = project
    _scan(session, project_id, "trivy", "completed",
          {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}, minutes_ago=120)
    _scan(session, project_id, "trivy", "failed", None, minutes_ago=5)

    report = _summary(client, project_id, auth)

    assert "trivy" in report["failed_tools"], report


def test_all_tools_healthy_reports_no_failures(client, session, project):
    project_id, auth = project
    _scan(session, project_id, "gitleaks", "completed",
          {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}, minutes_ago=5)

    assert _summary(client, project_id, auth)["failed_tools"] == []
