"""ログイン状態（AD 認証）

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_session",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("username", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_session_username", "user_session", ["username"])
    op.create_index("ix_user_session_expires_at", "user_session", ["expires_at"])


def downgrade() -> None:
    op.drop_table("user_session")
