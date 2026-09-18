import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import EvalRunStatus, RunItemStatus, enum_values
from app.models.model_endpoint import ModelEndpoint
from app.models.task import Task, TaskSet


class EvalRun(TimestampMixin, Base):
    __tablename__ = "eval_run"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_set.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[EvalRunStatus] = mapped_column(
        SAEnum(EvalRunStatus, name="eval_run_status", values_callable=enum_values),
        nullable=False,
        server_default=text("'pending'"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    task_set: Mapped[TaskSet] = relationship()


class RunItem(Base):
    __tablename__ = "run_item"
    __table_args__ = (Index("ix_run_item_eval_run_id_status", "eval_run_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_endpoint.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[RunItemStatus] = mapped_column(
        SAEnum(RunItemStatus, name="run_item_status", values_callable=enum_values),
        nullable=False,
        default=RunItemStatus.queued,
        server_default=text("'queued'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    eval_run: Mapped[EvalRun] = relationship()
    task: Mapped[Task] = relationship()
    model_endpoint: Mapped[ModelEndpoint] = relationship()
