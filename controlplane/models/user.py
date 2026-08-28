from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # "user" or "admin". Admin means platform owner: the operator console
    # (api/rbac.py:require_platform_admin) is the only thing this column
    # gates, and everything that console shows is about the platform itself —
    # this repository's CI, its own app/ services, the devops-platform
    # namespace, Vault, cluster capacity. A tenant has no business seeing any
    # of it, so a tenant does not get this role.
    #
    # It briefly defaulted to "admin" for every account, which handed each new
    # signup the platform's own repository, service list and secret names.
    # New accounts are tenants; the first account created on an empty install
    # is the owner (repositories/users.py).
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
