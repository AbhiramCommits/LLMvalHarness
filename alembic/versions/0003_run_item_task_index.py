"""composite index on run_item(task_id, status) for leaderboard queries

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_run_item_task_id_status",
        "run_item",
        ["task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_item_task_id_status", table_name="run_item")
