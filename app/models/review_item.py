import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ReviewReason, ReviewStatus, enum_values


class ReviewItem(TimestampMixin, Base):
    __tablename__ = "review_item"
    __table_args__ = (Index("ix_review_item_status_created_at", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[ReviewReason] = mapped_column(
        SAEnum(ReviewReason, name="review_reason", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[ReviewStatus] = mapped_column(
        SAEnum(ReviewStatus, name="review_status", values_callable=enum_values),
        nullable=False,
        default=ReviewStatus.open,
        server_default=text("'open'"),
    )
    reviewer_label: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
