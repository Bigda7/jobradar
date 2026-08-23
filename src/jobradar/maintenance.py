import argparse
import asyncio
import json

from jobradar.config import get_settings
from jobradar.db.session import engine, session_factory
from jobradar.ingestion.deduplication import CrossSourceDeduplicationService
from jobradar.opportunities.expiration import StaleExpirationService
from jobradar.opportunities.service import OpportunityStateService


async def reset_hidden() -> None:
    reset_count = await OpportunityStateService(session_factory).reset_hidden()
    print(json.dumps({"reset_hidden": reset_count}))


async def deduplicate_opportunities() -> None:
    summary = await CrossSourceDeduplicationService(session_factory).merge_existing()
    print(
        json.dumps(
            {
                "duplicate_groups": summary.duplicate_groups,
                "merged_opportunities": summary.merged_opportunities,
            }
        )
    )


async def expire_stale() -> None:
    settings = get_settings()
    summary = await StaleExpirationService(session_factory).expire_stale(
        employment_days=settings.employment_stale_after_days,
        freelance_days=settings.freelance_stale_after_days,
    )
    print(
        json.dumps(
            {
                "expired_total": summary.expired_total,
                "expired_employment": summary.expired_employment,
                "expired_freelance": summary.expired_freelance,
                "protected_favorites": summary.protected_favorites,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JobRadar maintenance commands.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset-hidden", help="Reset every hidden opportunity state.")
    subcommands.add_parser(
        "deduplicate-opportunities",
        help="Merge employment opportunities with equal normalized titles and companies.",
    )
    subcommands.add_parser(
        "expire-stale",
        help="Deactivate stale non-favorite employment and freelance listings.",
    )
    arguments = parser.parse_args()

    try:
        if arguments.command == "reset-hidden":
            asyncio.run(reset_hidden())
        elif arguments.command == "deduplicate-opportunities":
            asyncio.run(deduplicate_opportunities())
        elif arguments.command == "expire-stale":
            asyncio.run(expire_stale())
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
