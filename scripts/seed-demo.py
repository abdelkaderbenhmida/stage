#!/usr/bin/env python3
"""Recreate a populated demo tenancy through the public API.

This instance's database has been wiped repeatedly by something outside the
project, taking every account and project with it. Rebuilding that by hand
each time is slow and produces a slightly different world each time, so this
script does it in one command and always produces the same one.

It drives the real HTTP API rather than writing rows directly, so what it
creates is exactly what a user could create, and a failure here is a real
failure of the platform rather than a quirk of the seeder.

Usage:
    python3 scripts/seed-demo.py [--base-url URL] [--deploy]

    --deploy also queues one deployment per project. That runs the full
    build/scan/push pipeline, which takes minutes per service and gates on
    CRITICAL/HIGH findings, so it is off by default.

Every account gets the same password, which is the one the test-mode console
sends. This is a lab fixture and is not suitable for anything reachable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

PASSWORD = "Test!Passw0rd123"

# Two admins and a spread of ordinary users, so role-dependent behaviour
# (the Operations nav, /platform access) is visible without editing anything.
USERS: list[tuple[str, str]] = [
    ("alice@example.com", "admin"),
    ("ana@scale.example.com", "admin"),
    ("ben@scale.example.com", "user"),
    ("cara@scale.example.com", "user"),
    ("dan@scale.example.com", "user"),
    ("eve@scale.example.com", "user"),
    ("finn@scale.example.com", "user"),
    ("gia@scale.example.com", "user"),
    ("hugo@scale.example.com", "user"),
    ("iris@scale.example.com", "user"),
    ("jon@scale.example.com", "user"),
]

# Deliberately reused across tenants: distinct namespaces for identically
# named projects are the Phase 2 guarantee, and this is what exercises it.
PROJECT_NAME = "staging"

# Public, small, and has a Dockerfile at its root — the three things the
# deploy pipeline needs.
DEMO_REPO = "https://github.com/docker/welcome-to-docker.git"
DEMO_BRANCH = "main"

SPEC = {
    "version": 1,
    "project": PROJECT_NAME,
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "demo.local"},
    "nodes": [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
    ],
}


def call(base: str, path: str, body=None, token: str | None = None, method: str | None = None):
    url = f"{base}/api/v1{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:200]}


def login(base: str, email: str) -> str | None:
    """Log in, waiting out the per-IP rate limit rather than failing on it.

    The limiter is brute-force protection doing its job; seeding eleven
    accounts from one address trips it every time.
    """
    for attempt in range(12):
        status, body = call(base, "/auth/login", {"email": email, "password": PASSWORD})
        if status == 200:
            return body["access_token"]
        if status == 429:
            time.sleep(15)
            continue
        print(f"  ! login {email} failed: {status} {body}", file=sys.stderr)
        return None
    print(f"  ! login {email} gave up after rate limiting", file=sys.stderr)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--deploy", action="store_true", help="also queue one deployment per project")
    parser.add_argument(
        "--no-provision",
        action="store_true",
        help="leave projects in draft (they cannot be deployed to until provisioned)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    status, _ = call(base, "/../healthz")
    print(f"seeding {base}")

    created = 0
    for email, role in USERS:
        status, body = call(base, "/auth/register", {
            "email": email, "password": PASSWORD, "password_confirm": PASSWORD,
        })
        if status in (200, 201):
            created += 1
        elif status == 429:
            time.sleep(15)
            status, body = call(base, "/auth/register", {
                "email": email, "password": PASSWORD, "password_confirm": PASSWORD,
            })
            if status in (200, 201):
                created += 1
        elif status not in (400, 409):
            print(f"  ! register {email}: {status} {body}", file=sys.stderr)

        token = login(base, email)
        if not token:
            continue

        status, body = call(base, "/projects", {"name": PROJECT_NAME, "infra_spec": SPEC}, token=token)
        if status == 201:
            project_id = body["id"]
            print(f"  {email:26} {role:5} project {project_id}")
        elif status == 409:
            status, body = call(base, "/projects", token=token)
            items = body if isinstance(body, list) else body.get("items", [])
            project_id = items[0]["id"] if items else None
            print(f"  {email:26} {role:5} project exists")
        else:
            print(f"  ! project for {email}: {status} {body}", file=sys.stderr)
            continue

        # A draft project has no namespace, no quota and no monitoring, and
        # refuses deployments — so an unprovisioned account still looks empty,
        # which is the complaint this seeder exists to answer. Provision by
        # default and wait, since the deployment below depends on it.
        if project_id and not args.no_provision:
            status, _ = call(base, f"/projects/{project_id}/provision", {}, token=token)
            if status not in (200, 202):
                print(f"  ! provision for {email}: {status}", file=sys.stderr)
            else:
                for _ in range(60):
                    _, detail = call(base, f"/projects/{project_id}", token=token)
                    if detail and detail.get("status") in ("ready", "failed"):
                        break
                    time.sleep(5)

        if args.deploy and project_id:
            status, body = call(base, f"/projects/{project_id}/deployments", {
                "service_name": "web",
                "repo_url": DEMO_REPO,
                "branch": DEMO_BRANCH,
                "port": 3000,
                "replicas": 1,
            }, token=token)
            if status != 201:
                print(f"  ! deployment for {email}: {status} {body}", file=sys.stderr)

    # Registration always creates an ordinary user, by design — there is no
    # API that hands out the admin role, and there should not be. Promoting
    # the two demo admins is therefore a direct database write, and is the one
    # thing here that a user could not do for themselves.
    promoted = _promote_admins()

    print(f"\n{created} accounts created; password for all: {PASSWORD}")
    if promoted:
        print(f"admins: {', '.join(promoted)}")
    return 0


def _promote_admins() -> list[str]:
    try:
        from controlplane.db import SessionLocal
        from controlplane.models import User
    except ImportError:
        print("  ! controlplane not importable; admin roles unchanged", file=sys.stderr)
        return []

    wanted = [email for email, role in USERS if role == "admin"]
    db = SessionLocal()
    try:
        promoted = []
        for email in wanted:
            user = db.query(User).filter(User.email == email).first()
            if user is not None:
                user.role = "admin"
                promoted.append(email)
        db.commit()
        return promoted
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
