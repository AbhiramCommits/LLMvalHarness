import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import Provider, enum_values


class ModelEndpoint(TimestampMixin, Base):
    __tablename__ = "model_endpoint"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[Provider] = mapped_column(
        SAEnum(Provider, name="provider", values_callable=enum_values),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
