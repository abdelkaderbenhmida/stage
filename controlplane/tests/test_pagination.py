"""Pagination on the list endpoints (docs/TODO.md §8 item 6).

List responses stay plain JSON arrays for backward compatibility; page
metadata rides in Link / X-Total-Count / X-Page / X-Page-Size headers.
"""

import uuid

import pytest
from controlplane.models import Deployment, Project, Scan

VALID_SPEC = {
    "version": 1,
    "project": "page-proj",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ],
}


def _create_project(client, headers, name="page-proj"):
    return client.post(
        "/api/v1/projects",
        json={"name": name, "infra_spec": VALID_SPEC},
        headers=headers,
    ).json()


@pytest.fixture()
def many_projects(client, auth_headers, settings_override):
    """31 projects, crossing three 10-item pages."""
    headers = auth_headers()
    ids = []
    with settings_override(max_projects_per_user=100):
        for i in range(31):
            ids.append(_create_project(client, headers, f"page-proj-{i:02d}")["id"])
    return headers, ids


@pytest.mark.integration
def test_projects_pagination_headers_and_pages(client, many_projects):
    headers, ids = many_projects

    first = client.get("/api/v1/projects?page=1&page_size=10", headers=headers)
    assert first.status_code == 200
    assert len(first.json()) == 10
    assert first.headers["X-Total-Count"] == "31"
    assert first.headers["X-Page"] == "1"
    assert 'rel="next"' in first.headers["Link"]
    assert 'rel="prev"' not in first.headers["Link"]

    second = client.get("/api/v1/projects?page=2&page_size=10", headers=headers)
    assert len(second.json()) == 10
    assert 'rel="prev"' in second.headers["Link"]
    assert 'rel="next"' in second.headers["Link"]

    fourth = client.get("/api/v1/projects?page=4&page_size=10", headers=headers)
    assert len(fourth.json()) == 1
    assert 'rel="prev"' in fourth.headers["Link"]
    assert 'rel="next"' not in fourth.headers["Link"]

    fifth = client.get("/api/v1/projects?page=5&page_size=10", headers=headers)
    assert fifth.json() == []
    assert 'rel="prev"' in fifth.headers["Link"]
    assert 'rel="next"' not in fifth.headers["Link"]


@pytest.mark.integration
def test_projects_default_page_size_and_limit_cap(client, many_projects):
    headers, ids = many_projects
    default = client.get("/api/v1/projects", headers=headers)
    assert len(default.json()) == 20
    assert default.headers["X-Total-Count"] == "31"

    big = client.get("/api/v1/projects?page_size=1000", headers=headers)
    assert big.status_code == 422


@pytest.mark.integration
def test_projects_isolated_pagination_per_user(client, auth_headers, many_projects):
    headers, ids = many_projects
    other = auth_headers(email="stranger@example.com")
    resp = client.get("/api/v1/projects?page_size=10", headers=other)
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"
    assert "Link" not in resp.headers


@pytest.fixture()
def project_with_excess(client, auth_headers, session, many_projects):
    headers, ids = many_projects
    project = session.get(Project, uuid.UUID(ids[0]))
    project.status = "ready"
    for i in range(5):
        d = Deployment(
            project_id=project.id,
            service_name=f"svc-{i}",
            repo_url="https://github.com/example/svc.git",
            branch="main",
            port=8080 + i,
            replicas=2,
            strategy="deployment",
            status="live",
        )
        session.add(d)
        session.flush()
        scan = Scan(project_id=project.id, tool="trivy", target="img", status="completed")
        session.add(scan)
        session.flush()
    session.commit()
    return headers, project


@pytest.mark.integration
def test_deployments_pagination(client, project_with_excess):
    headers, project = project_with_excess
    first = client.get(
        f"/api/v1/projects/{project.id}/deployments?page_size=2", headers=headers
    )
    assert len(first.json()) == 2
    assert first.headers["X-Total-Count"] == "5"
    second = client.get(
        f"/api/v1/projects/{project.id}/deployments?page=3&page_size=2", headers=headers
    )
    assert len(second.json()) == 1
    assert 'rel="prev"' in second.headers["Link"]
    assert 'rel="next"' not in second.headers["Link"]


@pytest.mark.integration
def test_scans_pagination_and_tool_filter(client, project_with_excess):
    headers, project = project_with_excess
    first = client.get(
        f"/api/v1/projects/{project.id}/scans?page_size=2", headers=headers
    )
    assert len(first.json()) == 2
    assert first.headers["X-Total-Count"] == "5"

    filtered = client.get(
        f"/api/v1/projects/{project.id}/scans?tool=trivy", headers=headers
    )
    assert filtered.headers["X-Total-Count"] == "5"
    assert all(s["tool"] == "trivy" for s in filtered.json())

    none = client.get(
        f"/api/v1/projects/{project.id}/scans?tool=gitleaks", headers=headers
    )
    assert none.json() == []
    assert none.headers["X-Total-Count"] == "0"