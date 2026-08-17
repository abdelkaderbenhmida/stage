"""Team management (docs/TODO.md Task 3.1, 3.2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.api.deps import audit, get_current_user
from controlplane.api.rate_limit import check_rate_limit
from controlplane.api.rbac import require_team_role
from controlplane.api.schemas import (
    CostOut,
    GitCredentialIn,
    GitCredentialStatus,
    Message,
    TeamCreate,
    TeamMemberCreate,
    TeamMemberOut,
    TeamOut,
)
from controlplane.core.config import settings
from controlplane.core.costs import summarise
from controlplane.core.git_credentials import (
    InsecureSecretStore,
    delete_team_token,
    has_team_token,
    set_team_token,
    store_is_encrypted,
)
from controlplane.db import get_db
from controlplane.models import Project, User
from controlplane.repositories.base import NotFoundError
from controlplane.repositories.teams import TeamRepository

router = APIRouter(tags=["teams"])


@router.get("/teams", response_model=list[TeamOut])
def list_teams(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return TeamRepository(db, user.id).list_teams()


@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(
    body: TeamCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = TeamRepository(db, user.id)
    team = repo.create_team(body.name, body.description)
    db.commit()
    audit(db, user.id, "team.create", request, resource_type="team", resource_id=str(team.id), team_id=team.id)
    db.commit()
    return team


@router.get("/teams/{team_id}", response_model=TeamOut)
def get_team(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return TeamRepository(db, user.id).get_team(team_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Team not found.") from None


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
def list_members(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        members = TeamRepository(db, user.id).members(team_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Team not found.") from None
    return [
        TeamMemberOut(user_id=member.user_id, email=account.email, role=member.role)
        for member, account in members
    ]


@router.post("/teams/{team_id}/members", response_model=TeamMemberOut, status_code=201)
def add_member(
    team_id: uuid.UUID,
    body: TeamMemberCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_team_role(team_id, user, db, "team.manage")

    # get_by_email's 404/200 split makes this endpoint an account-enumeration
    # oracle for anyone in any team (repositories/users.py); bound the abuse
    # rate rather than change the response shape, which callers rely on.
    if not check_rate_limit(f"team-invite:{user.id}", settings.team_invites_per_hour, 3600):
        raise HTTPException(status_code=429, detail="Too many invite attempts. Try again later.")

    account = db.scalar(select(User).where(User.email == body.email))
    if account is None:
        raise HTTPException(status_code=404, detail="No user with that email address.")

    membership = TeamRepository(db, user.id).add_member(team_id, account.id, body.role)
    db.commit()
    audit(
        db, user.id, "team.member.add", request,
        resource_type="team", resource_id=str(team_id),
        detail={"member": str(account.id), "role": body.role}, team_id=team_id,
    )
    db.commit()
    return TeamMemberOut(user_id=account.id, email=account.email, role=membership.role)


@router.delete("/teams/{team_id}/members/{user_id}", response_model=Message)
def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_team_role(team_id, user, db, "team.manage")
    try:
        TeamRepository(db, user.id).remove_member(team_id, user_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Member not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    audit(
        db, user.id, "team.member.remove", request,
        resource_type="team", resource_id=str(team_id), detail={"member": str(user_id)}, team_id=team_id,
    )
    db.commit()
    return Message(message="Member removed.")


@router.get("/teams/{team_id}/costs", response_model=CostOut)
def team_costs(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Estimated spend for every project the team owns (docs/TODO.md Task 5.1)."""
    require_team_role(team_id, user, db, "project.read")
    projects = list(db.scalars(select(Project).where(Project.team_id == team_id)))
    return summarise(projects)


# --- git credentials for private repositories -------------------------------
#
# Deliberately write-only. There is no endpoint that returns the token: the
# platform can use a credential it holds, but nobody can read it back, so a
# stolen session cannot exfiltrate the key to a tenant's source. Status
# reports only whether one exists.


@router.put("/teams/{team_id}/git-credential", response_model=GitCredentialStatus)
def put_git_credential(
    team_id: uuid.UUID,
    body: GitCredentialIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store the token the platform uses to clone this team's private repositories."""
    # Writing a credential is cheap but repeated attempts are a smell; bound
    # per user rather than per IP so one tenant cannot lock out another.
    if not check_rate_limit(f"git-credential:{user.id}", 20, 3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many credential updates. Try again later.",
        )
    require_team_role(team_id, user, db, "team.manage")
    try:
        set_team_token(team_id, body.token, username=body.username)
    except InsecureSecretStore as exc:
        # A misconfigured deployment, not a bad request.
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # The token itself is never written to the audit trail, only the fact.
    audit(
        db, user.id, "team.git_credential.set", request,
        resource_type="team", resource_id=str(team_id), team_id=team_id,
    )
    return GitCredentialStatus(configured=True, encrypted=store_is_encrypted())


@router.get("/teams/{team_id}/git-credential", response_model=GitCredentialStatus)
def get_git_credential_status(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Whether a credential is configured. Never returns the value."""
    require_team_role(team_id, user, db, "team.manage")
    return GitCredentialStatus(
        configured=has_team_token(team_id), encrypted=store_is_encrypted()
    )


@router.delete("/teams/{team_id}/git-credential", response_model=GitCredentialStatus)
def remove_git_credential(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_team_role(team_id, user, db, "team.manage")
    delete_team_token(team_id)
    audit(
        db, user.id, "team.git_credential.delete", request,
        resource_type="team", resource_id=str(team_id), team_id=team_id,
    )
    return GitCredentialStatus(configured=False, encrypted=store_is_encrypted())
