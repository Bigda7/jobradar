from sqlalchemy import and_, func, or_
from sqlalchemy.sql.elements import ColumnElement

from jobradar.db.models import Listing, Source
from jobradar.sources.link_policy import SOURCE_LISTING_HOSTS


def trusted_listing_condition() -> ColumnElement[bool]:
    source_conditions = [
        and_(
            Source.name == source_name,
            or_(
                *(
                    func.lower(Listing.source_url).startswith(f"https://{host}{port}/")
                    for host in sorted(hosts)
                    for port in ("", ":443")
                )
            ),
        )
        for source_name, hosts in SOURCE_LISTING_HOSTS.items()
    ]
    return or_(~Source.name.in_(tuple(SOURCE_LISTING_HOSTS)), *source_conditions)
