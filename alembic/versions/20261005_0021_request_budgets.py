"""Persist request windows and separate metadata backlog from discovery failures."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261005_0021"
down_revision: str | None = "20261005_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources", sa.Column("request_budget", JSONB(), nullable=False, server_default="{}")
    )
    op.add_column(
        "source_runs",
        sa.Column("metadata_deferred_count", sa.Integer(), nullable=False, server_default="0"),
    )
    bind = op.get_bind()
    source_id = bind.scalar(sa.text("SELECT id FROM sources WHERE name='djinni'"))
    if source_id is not None:
        now = datetime.now(UTC).timestamp()
        attempts = []
        for raw in bind.scalars(
            sa.text(
                "SELECT raw_data->>'metadata_attempted_at' FROM listings WHERE source_id=:id "
                "AND raw_data ? 'metadata_attempted_at'"
            ),
            {"id": source_id},
        ):
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                stamp = parsed.timestamp()
            except (ValueError, TypeError, OverflowError):
                continue
            if stamp > now - 3600:
                attempts.append(stamp)
        # Hold RSS briefly on rollout because old code did not record exact request timestamps.
        bind.execute(
            sa.update(sa.table("sources", sa.column("id"), sa.column("request_budget", JSONB())))
            .where(sa.column("id") == source_id)
            .values(request_budget={"rss": [now] * 100, "metadata": sorted(attempts)[-100:]})
        )


def downgrade() -> None:
    op.drop_column("source_runs", "metadata_deferred_count")
    op.drop_column("sources", "request_budget")
