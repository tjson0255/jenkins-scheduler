"""その回だけの変更（置き換え）: 置き換え先の run が、元の run とパラメータの上書きを持つ

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("replaces_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("override_params", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("override_params")
        batch.drop_column("replaces_run_id")
