from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Every account is admin — there is no non-admin role left, by explicit
    # request. The operator console's gate (api/rbac.py:require_platform_admin)
    # still checks this column; it is simply never false anymore. Kept as a
    # column (not removed) so the gate itself doesn't need touching and OIDC's
    # role_from_groups() mapping still has somewhere to write a value.
    role: Mapped[str] = mapped_column(String(16), default="admin", nullable=False)
