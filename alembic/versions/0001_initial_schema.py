"""initial schema: task sets, tasks, model endpoints, eval runs, run items, grades, review items

Revision ID: 0001
Revises:
Create Date: 2026-09-17

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

grader_type_enum = postgresql.ENUM("exact_match", "regex", "llm_judge", name="grader_type")
provider_enum = postgresql.ENUM("openai", "anthropic", "local", name="provider")
eval_run_status_enum = postgresql.ENUM(
    "pending", "running", "completed", "failed", "cancelled", name="eval_run_status"
)
run_item_status_enum = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", "dead_lettered", name="run_item_status"
)
grader_kind_enum = postgresql.ENUM("deterministic", "llm_judge", name="grader_kind")
review_reason_enum = postgresql.ENUM("judge_disagreement", "low_confidence", name="review_reason")
review_status_enum = postgresql.ENUM("open", "claimed", "resolved", name="review_status")


def upgrade() -> None:
    op.create_table(
        "task_set",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_set"),
    )
    op.create_table(
        "task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_set_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("grader_type", grader_type_enum, nullable=False),
        sa.Column("grader_config", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task"),
        sa.ForeignKeyConstraint(
            ["task_set_id"],
            ["task_set.id"],
            name="fk_task_task_set_id_task_set",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "model_endpoint",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_endpoint"),
    )
    op.create_table(
        "eval_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_set_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            eval_run_status_enum,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(12, 6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_eval_run"),
        sa.ForeignKeyConstraint(
            ["task_set_id"],
            ["task_set.id"],
            name="fk_eval_run_task_set_id_task_set",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "run_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("eval_run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("model_endpoint_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            run_item_status_enum,
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("artifact_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_item"),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["eval_run.id"],
            name="fk_run_item_eval_run_id_eval_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_run_item_task_id_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_endpoint_id"],
            ["model_endpoint.id"],
            name="fk_run_item_model_endpoint_id_model_endpoint",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_run_item_eval_run_id_status",
        "run_item",
        ["eval_run_id", "status"],
    )
    op.create_table(
        "grade",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_item_id", sa.Uuid(), nullable=False),
        sa.Column("grader", grader_kind_enum, nullable=False),
        sa.Column("score", sa.Numeric(4, 3), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("rubric_scores", postgresql.JSONB(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grade"),
        sa.ForeignKeyConstraint(
            ["run_item_id"],
            ["run_item.id"],
            name="fk_grade_run_item_id_run_item",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_grade_run_item_id", "grade", ["run_item_id"])
    op.create_table(
        "review_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_item_id", sa.Uuid(), nullable=False),
        sa.Column("reason", review_reason_enum, nullable=False),
        sa.Column(
            "status",
            review_status_enum,
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("reviewer_label", sa.Numeric(4, 3), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_item"),
        sa.ForeignKeyConstraint(
            ["run_item_id"],
            ["run_item.id"],
            name="fk_review_item_run_item_id_run_item",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_review_item_status_created_at",
        "review_item",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_item_status_created_at", table_name="review_item")
    op.drop_table("review_item")
    op.drop_index("ix_grade_run_item_id", table_name="grade")
    op.drop_table("grade")
    op.drop_index("ix_run_item_eval_run_id_status", table_name="run_item")
    op.drop_table("run_item")
    op.drop_table("eval_run")
    op.drop_table("model_endpoint")
    op.drop_table("task")
    op.drop_table("task_set")

    bind = op.get_bind()
    for enum in reversed(
        [
            grader_type_enum,
            provider_enum,
            eval_run_status_enum,
            run_item_status_enum,
            grader_kind_enum,
            review_reason_enum,
            review_status_enum,
        ]
    ):
        enum.drop(bind, checkfirst=True)
