"""実行した時点のスケジューラの件名とメモを run に残す（スケジューラを変更・削除しても履歴で分かるように）

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("title_snapshot", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("note_snapshot", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("note_snapshot")
        batch.drop_column("title_snapshot")
