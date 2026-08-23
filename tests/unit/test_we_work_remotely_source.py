import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.we_work_remotely import WeWorkRemotelySource

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Example Labs: Junior React Developer</title>
      <region>Anywhere in the World</region>
      <category>Front-End Programming</category>
      <description><![CDATA[
        <p><strong>Description</strong></p>
        <p>Build React and TypeScript interfaces backed by REST APIs.</p>
      ]]></description>
      <pubDate>Sat, 22 Aug 2026 12:00:00 +0000</pubDate>
      <guid>https://weworkremotely.com/remote-jobs/example-junior-react-developer</guid>
      <link>https://weworkremotely.com/remote-jobs/example-junior-react-developer</link>
    </item>
    <item>
      <title>Example Labs: Duplicate</title>
      <region>Anywhere in the World</region>
      <description><![CDATA[<p>Duplicate URL</p>]]></description>
      <pubDate>Sat, 22 Aug 2026 12:00:00 +0000</pubDate>
      <link>https://weworkremotely.com/remote-jobs/example-junior-react-developer</link>
    </item>
  </channel>
</rss>
"""


@pytest.mark.asyncio
async def test_we_work_remotely_source_parses_public_programming_rss() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=RSS))
    ) as client:
        source = WeWorkRemotelySource(
            feed_url="https://weworkremotely.test/programming.rss",
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    assert listings[0].external_id == "example-junior-react-developer"
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior React Developer"
    assert normalized.company == "Example Labs"
    assert normalized.description is not None
    assert "Build React and TypeScript interfaces" in normalized.description
    assert normalized.location_text == "Anywhere in the World"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T12:00:00+00:00"
