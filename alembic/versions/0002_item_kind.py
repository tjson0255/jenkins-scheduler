"""アイテムの種類（Jenkins / 自由記入）

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("target") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=10), nullable=False, server_default="jenkins"))
        batch.alter_column("job_path", existing_type=sa.String(length=500), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM target WHERE kind = 'memo'")
    with op.batch_alter_table("target") as batch:
        batch.alter_column("job_path", existing_type=sa.String(length=500), nullable=False)
        batch.drop_column("kind")
