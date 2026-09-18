"""nullable grade score/passed for failed judge grades

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "grade",
        "score",
        existing_type=sa.Numeric(4, 3),
        nullable=True,
    )
    op.alter_column(
        "grade",
        "passed",
        existing_type=sa.Boolean(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE grade SET score = 0 WHERE score IS NULL")
    op.execute("UPDATE grade SET passed = FALSE WHERE passed IS NULL")
    op.alter_column(
        "grade",
        "score",
        existing_type=sa.Numeric(4, 3),
        nullable=False,
    )
    op.alter_column(
        "grade",
        "passed",
        existing_type=sa.Boolean(),
        nullable=False,
    )
