"""同時編集の検出用の更新番号（revision）

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("target", "schedule"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for table in ("target", "schedule"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("revision")
