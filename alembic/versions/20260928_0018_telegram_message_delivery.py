"""Associate Telegram messages with automatic deliveries.

Revision ID: 20260928_0018
Revises: 20260928_0017
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0018"
down_revision: str | None = "20260928_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_opportunity_messages",
        sa.Column("delivery_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        op.f("ix_telegram_opportunity_messages_delivery_id"),
        "telegram_opportunity_messages",
        ["delivery_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_telegram_opportunity_messages_delivery_id",
        "telegram_opportunity_messages",
        "notification_deliveries",
        ["delivery_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_telegram_opportunity_messages_delivery_id",
        "telegram_opportunity_messages",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_telegram_opportunity_messages_delivery_id"),
        table_name="telegram_opportunity_messages",
    )
    op.drop_column("telegram_opportunity_messages", "delivery_id")
