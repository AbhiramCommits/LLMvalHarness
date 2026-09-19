import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ApiKeyRole, enum_values


class ApiKey(TimestampMixin, Base):
    """API key record. Only the salted PBKDF2 hash is stored, never the key."""

    __tablename__ = "api_key"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
        Index("ix_api_key_key_prefix", "key_prefix"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[ApiKeyRole] = mapped_column(
        SAEnum(ApiKeyRole, name="api_key_role", values_callable=enum_values),
        nullable=False,
    )
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
