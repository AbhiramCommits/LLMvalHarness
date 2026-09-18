import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Numeric, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import GraderKind, enum_values
from app.models.eval_run import RunItem


class Grade(TimestampMixin, Base):
    __tablename__ = "grade"
    __table_args__ = (Index("ix_grade_run_item_id", "run_item_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    grader: Mapped[GraderKind] = mapped_column(
        SAEnum(GraderKind, name="grader_kind", values_callable=enum_values),
        nullable=False,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rubric_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    run_item: Mapped[RunItem] = relationship()
