"""Soft Delete Mixin Module.

This module provides the soft delete capability for SQLAlchemy declarative models,
managing record suppression and restoration using timezone-aware timestamps.
"""

from datetime import datetime

from sqlalchemy import DateTime, Select
from sqlalchemy.orm import Mapped, mapped_column

from zcore.utils.timezone import now


class SoftDeleteMixin:
    """Mixin providing soft-deletion capabilities to SQLAlchemy declarative models."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @classmethod
    def scope_query(cls, query: Select) -> Select:
        return query.where(cls.deleted_at.is_(None))

    def soft_delete(self) -> None:
        self.deleted_at = now()

    def restore(self) -> None:
        self.deleted_at = None