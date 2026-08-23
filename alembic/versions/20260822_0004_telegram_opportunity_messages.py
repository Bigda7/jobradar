"""Track Telegram opportunity messages.

Revision ID: 20260822_0004
Revises: 20260822_0003
Create Date: 2026-08-22 19:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0004"
down_revision: str | None = "20260822_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_opportunity_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("opportunity_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_telegram_opportunity_messages_opportunity_id_opportunities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_opportunity_messages")),
        sa.UniqueConstraint(
            "telegram_message_id",
            name=op.f("uq_telegram_opportunity_messages_telegram_message_id"),
        ),
    )
    op.create_index(
        op.f("ix_telegram_opportunity_messages_deleted_at"),
        "telegram_opportunity_messages",
        ["deleted_at"],
    )
    op.create_index(
        op.f("ix_telegram_opportunity_messages_opportunity_id"),
        "telegram_opportunity_messages",
        ["opportunity_id"],
    )


def downgrade() -> None:
    op.drop_table("telegram_opportunity_messages")
