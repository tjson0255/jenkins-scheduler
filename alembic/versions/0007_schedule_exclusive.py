"""この期間は同じレーンの他のスケジューラを止める（臨時のスケジューラ用）

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("schedule") as batch:
        batch.add_column(sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("schedule") as batch:
        batch.drop_column("exclusive")
