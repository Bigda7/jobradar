import argparse
import asyncio
import json

from jobradar.config import get_settings
from jobradar.db.session import engine, session_factory
from jobradar.diagnostics import diagnose_sources, format_diagnostic_table
from jobradar.ingestion.deduplication import CrossSourceDeduplicationService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.opportunities.expiration import StaleExpirationService
from jobradar.opportunities.service import OpportunityStateService
from jobradar.sources.registry import build_source_registry


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


async def audit_duplicates() -> None:
    audit = await CrossSourceDeduplicationService(session_factory).audit_existing()
    print(
        json.dumps(
            {
                "candidate_groups": audit.candidate_groups,
                "candidate_opportunities": audit.candidate_opportunities,
                "groups": [
                    {
                        "normalized_title": group.normalized_title,
                        "normalized_company": group.normalized_company,
                        "opportunity_ids": group.opportunity_ids,
                        "titles": group.titles,
                        "companies": group.companies,
                    }
                    for group in audit.groups
                ],
            },
            ensure_ascii=False,
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
                "archived_favorites": summary.archived_favorites,
                "restored_recent": summary.restored_recent,
            }
        )
    )


async def rescore_all() -> None:
    summary = await MatchingService(session_factory).evaluate(BOHDAN_PROFILE, force=True)
    print(
        json.dumps(
            {
                "profile_id": BOHDAN_PROFILE.profile_id,
                "rules_version": BOHDAN_PROFILE.rules_version,
                "rescored": summary.evaluated,
                "skipped": summary.unchanged,
            }
        )
    )


async def test_adapters(
    source_name: str | None,
    timeout_seconds: float,
    sample_size: int | None,
) -> None:
    sources = build_source_registry(get_settings())
    if source_name is not None:
        sources = tuple(source for source in sources if source.name == source_name)
        if not sources:
            raise SystemExit(f"Enabled source not found: {source_name}")
    results = await diagnose_sources(
        sources,
        timeout_seconds=timeout_seconds,
        sample_size=sample_size,
    )
    print(format_diagnostic_table(results))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JobRadar maintenance commands.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset-hidden", help="Reset every hidden opportunity state.")
    subcommands.add_parser(
        "deduplicate-opportunities",
        help="Merge employment opportunities with equal normalized titles and companies.",
    )
    subcommands.add_parser(
        "audit-duplicates",
        help="Report conservative duplicate candidates without changing the database.",
    )
    subcommands.add_parser(
        "expire-stale",
        help="Deactivate stale non-favorite employment and freelance listings.",
    )
    subcommands.add_parser(
        "rescore-all",
        help="Force active opportunities to be evaluated with the current matching rules.",
    )
    adapter_parser = subcommands.add_parser(
        "test-adapters",
        help="Fetch and normalize enabled sources without writing to the database.",
    )
    adapter_parser.add_argument("--source", help="Test one enabled source by its internal name.")
    adapter_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Maximum seconds allowed for each source.",
    )
    adapter_parser.add_argument(
        "--sample-size",
        type=int,
        help="Stop after this many discovered listings per source.",
    )
    arguments = parser.parse_args()

    try:
        if arguments.command == "reset-hidden":
            asyncio.run(reset_hidden())
        elif arguments.command == "deduplicate-opportunities":
            asyncio.run(deduplicate_opportunities())
        elif arguments.command == "audit-duplicates":
            asyncio.run(audit_duplicates())
        elif arguments.command == "expire-stale":
            asyncio.run(expire_stale())
        elif arguments.command == "rescore-all":
            asyncio.run(rescore_all())
        elif arguments.command == "test-adapters":
            if arguments.timeout <= 0:
                parser.error("--timeout must be positive.")
            if arguments.sample_size is not None and arguments.sample_size <= 0:
                parser.error("--sample-size must be positive.")
            asyncio.run(test_adapters(arguments.source, arguments.timeout, arguments.sample_size))
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
