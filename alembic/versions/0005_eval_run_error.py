"""eval_run.error column for abort messages

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18

"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_run", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_run", "error")
