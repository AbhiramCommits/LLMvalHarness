"""api keys with hashed storage

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

api_key_role_enum = postgresql.ENUM("admin", "reviewer", name="api_key_role")


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", api_key_role_enum, nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_key"),
        sa.UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
    )
    op.create_index("ix_api_key_key_prefix", "api_key", ["key_prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_key_key_prefix", table_name="api_key")
    op.drop_table("api_key")
    bind = op.get_bind()
    api_key_role_enum.drop(bind, checkfirst=True)
