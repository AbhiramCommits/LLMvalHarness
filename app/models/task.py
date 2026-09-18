import uuid
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import GraderType, enum_values


class TaskSet(TimestampMixin, Base):
    __tablename__ = "task_set"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="task_set",
        cascade="all, delete-orphan",
    )


class Task(TimestampMixin, Base):
    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_set.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    grader_type: Mapped[GraderType] = mapped_column(
        SAEnum(GraderType, name="grader_type", values_callable=enum_values),
        nullable=False,
    )
    grader_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    task_set: Mapped[TaskSet] = relationship(back_populates="tasks")
