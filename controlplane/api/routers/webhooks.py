"""Git provider webhooks (docs/TODO.md Task 2.4).

This is the only unauthenticated, internet-facing endpoint in the product, so
the signature check below is the entire security boundary. Two details are not
negotiable:

* The signature is computed over the **raw request body**. Parsing the JSON and
  re-serialising it changes the bytes and breaks verification.
* The comparison uses ``hmac.compare_digest``. A plain ``==`` short-circuits on
  the first differing byte and leaks the expected signature to a timing attack.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.api.rate_limit import check_rate_limit
from controlplane.api.schemas import Message
from controlplane.db import get_db
from controlplane.models import Deployment, Project, WebhookSubscription
from controlplane.workers import tasks

router = APIRouter(tags=["webhooks"])


def _verify_github(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


def _verify_gitlab(secret: str, body: bytes, token: str | None) -> bool:
    # GitLab sends the shared secret verbatim rather than an HMAC, so the
    # comparison must still be constant time.
    return bool(token) and hmac.compare_digest(secret, token)


def _branch_from_ref(ref: str) -> str:
    return ref.split("/", 2)[-1] if ref.startswith("refs/heads/") else ref


@router.post("/webhooks/{provider}", response_model=Message)
async def receive_webhook(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
):
    if provider not in ("github", "gitlab"):
        raise HTTPException(status_code=404, detail="Unknown provider.")

    # Read the raw bytes before any parsing — see the module docstring.
    body = await request.body()

    # Rate limit on the source address: this endpoint is reachable by anyone,
    # and signature verification is not free.
    client_host = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"webhook:{client_host}", 120, 60):
        raise HTTPException(status_code=429, detail="Too many webhook deliveries.")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid JSON.") from None

    repo_url = (
        payload.get("repository", {}).get("clone_url")
        or payload.get("repository", {}).get("git_http_url")
        or payload.get("repository", {}).get("html_url")
        or ""
    )
    branch = _branch_from_ref(payload.get("ref", "") or "")

    candidates = list(
        db.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.active.is_(True),
                WebhookSubscription.provider == provider,
            )
        )
    )

    # Verify against every candidate rather than selecting by repository first:
    # the repository field is attacker-controlled, so it must not be trusted to
    # pick which secret to check.
    matched = None
    for subscription in candidates:
        verified = (
            _verify_github(subscription.secret, body, x_hub_signature_256)
            if provider == "github"
            else _verify_gitlab(subscription.secret, body, x_gitlab_token)
        )
        if verified:
            matched = subscription
            break

    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signature verification failed.",
        )

    deployment = db.get(Deployment, matched.deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment no longer exists.")
    project = db.get(Project, deployment.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project no longer exists.")

    # A closed or merged pull request tears the environment down rather than
    # rebuilding it.
    action = payload.get("action")
    if x_github_event == "pull_request" and action in ("closed", "merged"):
        if matched.pull_request_number and project.auto_destroy:
            tasks.queue_destroy(project.id, project.workspace_path, project.name, project.owner_id)
            return Message(message="Pull request closed — environment destroy queued.")
        return Message(message="Ignored: environment is not pull-request scoped.")

    if branch and branch != matched.branch:
        return Message(message=f"Ignored: push to {branch}, subscribed to {matched.branch}.")

    if repo_url and matched.repo_url and repo_url.rstrip(".git") != matched.repo_url.rstrip(".git"):
        return Message(message="Ignored: repository does not match the subscription.")

    try:
        tasks.queue_deploy(deployment, project, project.owner_id)
    except tasks.DeployAlreadyRunning as exc:
        # Pushes arrive in bursts. Failing the delivery would make the
        # provider retry and mark the hook unhealthy, so this is a normal,
        # successful outcome: the deploy in flight already covers the branch.
        return Message(
            message=f"Ignored: a deploy is already {exc.job.status} for this service."
        )
    return Message(message="Deploy queued.")
