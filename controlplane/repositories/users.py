import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.models import RefreshToken, User


class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self.session.get(User, user_id)

    def create(self, email: str, password_hash: str, role: str = "user") -> User:
        user = User(email=email, password_hash=password_hash, role=role)
        self.session.add(user)
        self.session.flush()
        return user


class RefreshTokenRepository:
    def __init__(self, session: Session):
        self.session = session

    def store(self, user_id: uuid.UUID, token_hash: str, ttl_days: int) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
        )
        self.session.add(token)
        self.session.flush()
        return token

    def get_active(self, token_hash: str) -> RefreshToken | None:
        now = datetime.now(UTC)
        return self.session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > now,
            )
        )

    def revoke(self, token_hash: str) -> None:
        token = self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if token:
            token.revoked = True
            self.session.flush()

    def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        tokens = self.session.scalars(
            select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        )
        for token in tokens:
            token.revoked = True
        self.session.flush()


class AuditLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def record(
        self,
        user_id: uuid.UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        detail: dict | None = None,
    ) -> None:
        from controlplane.models import AuditLog

        self.session.add(
            AuditLog(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                ip_address=ip_address,
                detail=detail,
                created_at=datetime.now(UTC),
            )
        )
        self.session.flush()
