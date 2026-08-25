# JobRadar

JobRadar is a private, single-user service that collects job and freelance listings, normalizes them, stores them idempotently, and prepares them for deterministic matching and Telegram delivery.

Iteration 1 provides the application foundation, PostgreSQL schema, source abstraction, ingestion pipeline, worker process, and read-only API.

Iteration 2 adds the Djinni source. It reads the public remote-jobs page and extracts schema.org `JobPosting` JSON-LD data. The adapter validates every item as remote before ingestion and does not use browser automation.

Iteration 3 adds deterministic profile matching and idempotent Telegram notifications. Matching results include a score, positive reasons, and concerns. No language model is used.

Iteration 4 adds read-only Freelancer.com project collection through the official HTTP API, dedicated deterministic freelance scoring, scam rejection, currency-aware budget evaluation, and a project-specific Telegram message. The adapter searches active projects, excludes local projects and contests, and deduplicates projects returned by multiple search queries.

Iteration 5 adds Work.ua remote-job collection and centralized Telegram currency conversion. Work.ua does not provide a public job-search API and blocks direct HTTP collection, so the adapter requests the original HTML of configured public search and detail pages through a read-only reader endpoint. It parses vacancy cards locally, rejects every card without an explicit Remote marker, loads the complete `job-description` block from each accepted detail page, and does not use browser automation or authentication. Published salaries and project budgets are converted with the official National Bank of Ukraine rates and displayed in USD, UAH, and CZK.

Iteration 6 turns Telegram into a local opportunity-management interface. Every opportunity notification includes favorite, hide, and source-link inline buttons. A separate long-polling bot process handles callbacks and the `/latest`, `/favorites`, and `/stats` commands without requiring a public webhook or VPS. Favorite and hidden states are stored in PostgreSQL. Hidden opportunities are excluded from automatic notifications and manual latest results without changing source data or ingestion deduplication.

Iteration 7 adds reversible hiding, source-specific notification headings, and tracked Telegram message cleanup. A hidden opportunity exposes restore and source-link buttons; restoring it returns the opportunity to regular matching results. The bot stores every opportunity message ID sent by automatic delivery or `/latest`, and `/clear` deletes those tracked messages from the configured private chat.

Iteration 8 adds global deterministic rejection rules for unpaid, volunteer, military-recruitment, mobilization, and army-service opportunities. These rules run before both employment and freelance scoring and always produce a score of zero. A narrow context exception prevents paid volunteering-leave benefits from being mistaken for volunteer jobs. It also adds Jobs.cz through direct read-only HTTP requests to public, server-rendered search and detail pages. Jobs.cz cards are accepted only when they contain the exact fully remote arrangement marker; occasional home-office vacancies are rejected.

Iteration 9 adds the `/all` Telegram command. It sends every currently active opportunity that meets the configured score threshold and is not hidden, while `/latest` keeps its small configurable limit for quick checks.

Iteration 10 adds StartupJobs.cz, Prace.cz, and Freelance.cz. StartupJobs.cz is collected through its public read-only JSON search and detail endpoints; offers are accepted only when `remote` is their sole location preference. Prace.cz discovery reads the server-rendered `FULL_REMOTE` work-location value and then loads the complete public schema.org `JobPosting` description; `PARTIALLY_REMOTE` offers never reach ingestion. Freelance.cz uses its public JSON project search with the exact remote filter and keeps only project work, not permanent-employment advertisements. The visitor API deliberately truncates some Freelance.cz descriptions; such records are marked explicitly and receive a conservative scoring penalty instead of being treated as complete.

Iteration 11 adds persistent Telegram notification pause controls, the official Startup.jobs REST API, and independent source polling intervals. During `/pause`, collection and matching continue normally, but every new matching delivery event is finalized as `notification_skipped_paused`. `/resume` enables future automatic notifications without replaying anything accumulated during the pause; accumulated matches remain available through `/all`. Startup.jobs uses the official remote Engineering filter, cursor pagination, full descriptions, and source links. Each source is checked against its own interval using the persisted `sources.last_run_at` value, while a manual `worker --once` run intentionally forces every enabled source.

Iteration 12 adds global cross-source employment deduplication plus Jobicy and We Work Remotely. Employment listings with equal whitespace-normalized, case-insensitive titles and companies share one `Opportunity`; every platform URL remains stored as a separate `Listing`. The richest active listing is the deterministic canonical record used by matching, API results, and Telegram, and an opportunity that has already been sent or skipped during a pause is never sent again merely because another source or content revision appears. Existing duplicate groups are merged automatically before every matching cycle. Jobicy uses its public engineering JSON API with complete descriptions and a six-hour interval. We Work Remotely uses the official public Programming RSS feed with complete descriptions and an hourly interval.

Iteration 13 adds Arbeitnow and Remotive through their public JSON APIs. Arbeitnow keeps only records whose API payload explicitly declares `remote=true` and follows at most three pagination links per six-hour cycle. Remotive requests the Software Development category once every six hours, preserving the complete description, required candidate location, job type, and source URL. Published Remotive salary strings are parsed deterministically only when their currency is explicit; the existing notification layer then renders USD, UAH, and CZK. Both sources enter the same global cross-platform deduplication pipeline as every other employment source.

Iteration 14 adds a shared deterministic sanity check before employment and freelance scoring. It rejects US-only remote work, equity-only or profit-share work without a base salary, and explicit account-rental or proxy-bidding freelance scams. Employment salaries are normalized to a monthly USD reference before applying a 20-point hidden-seniority penalty above USD 2,000 and an additional 15-point full-time poverty-pay penalty below USD 400. Hourly salaries use 160 hours per month, so the hidden-seniority boundary is USD 12.50 per hour. Rockstar, ninja, and 10x language receives one five-point penalty. Plain uses of `agency` remain valid.

Iteration 15 adds source-inventory lifecycle reconciliation and quality-based canonical selection. After a source crawl reaches its natural end, active listings omitted from that crawl are marked inactive; interrupted crawls never deactivate data. Matching, Telegram `/latest` and `/all`, and API results require an active listing. Every listing stores its normalized snapshot and a deterministic quality score based on description length, salary availability, and metadata completeness. Cross-source merges promote the richest active record and automatically fall back to the next best active record when the canonical listing disappears. Jobs.cz and Prace.cz now accept only explicit full-remote evidence and reject every mostly-from-home, occasional-home-office, hybrid, and `PARTIALLY_REMOTE` record.

Phase 2 starts with DOU Jobs through its official remote RSS feed. The adapter keeps only entries whose headline explicitly declares remote work, rejects hybrid or mandatory-office descriptions before ingestion, and preserves the complete RSS description for centralized scoring. DOU salary ranges are parsed only when the currency is explicit. Military, defence-sector, mobilisation, volunteer, and unpaid markers continue through the shared hard-rejection layer and always receive score zero. Because DOU RSS is a rolling window rather than a complete source inventory, omission from a later feed does not deactivate an older listing.

Global stale expiration runs automatically in every worker cycle after cross-source deduplication and before matching. Employment listings expire at 30 days and freelance projects at 7 days, measured from the source publication time or the JobRadar first-seen time when publication time is unavailable. Expired listings remain stored but become inactive. Favorite opportunities are fully protected from age-based expiration.

The Muse integration uses the official public Jobs API and requests only `Flexible / Remote` Software Engineering positions at Entry or Mid level. The adapter verifies the remote marker again in every response, preserves the complete description, maps structured categories, tags, and levels into shared matching, and parses only explicit salary amounts that include a currency and period. The configured five-page window is a partial inventory and runs every six hours. The source stays disabled by default until an application is registered with The Muse for production use.

Reputation mode removes every negative score adjustment caused only by a small freelance budget. A fixed-price project between USD 5 and USD 100 receives a 15-point bonus when the source confirms that the employer has verified payment. Scam, incompatible-scope, unsupported-language, seniority, and competition rules remain unchanged. Every automatic source interval receives a stable deterministic jitter of plus or minus 15 percent derived from the source name and its previous run time; manual one-shot runs remain immediate.

The direct ATS integration reads `companies.yaml` and creates independent Greenhouse, Lever, and Ashby sources through their public JSON job-board APIs. Greenhouse requests complete post content, Lever uses structured workplace and salary fields, and Ashby requests structured compensation. Only explicit remote records are accepted; hybrid, onsite, office-based, and unlisted records are excluded before ingestion. The five initial boards are GitLab, Canonical, JumpCloud, Linear, and Supabase. ATS inventories run once per day with the global jitter. A successful complete provider crawl deactivates postings removed from that provider, while any interrupted request prevents reconciliation for the entire affected provider. Cross-source duplicates retain the richest description for matching, but API and Telegram source links prefer the direct ATS posting over DOU, The Muse, or another aggregator.

Upwork is intentionally outside the supported source scope. Upwork discontinued custom job-search RSS feeds in August 2024, and JobRadar does not replace them with HTML scraping, browser automation, or an unofficial endpoint.

## Local container startup

Copy the environment template if local overrides are needed:

```powershell
Copy-Item .env.example .env
```

Start the application:

```powershell
docker compose up --build
```

The API is available at `http://localhost:8000`.

Useful endpoints:

- `GET /health`
- `GET /ready`
- `GET /jobs`
- `GET /sources`
- `GET /matches`

`GET /jobs` returns remote opportunities by default and supports these filters:

- `q`: case-insensitive text search in title, company, and description
- `work_mode`: `remote`, `hybrid`, `onsite`, `flexible`, or `unknown`
- `employment_type`: normalized schema.org employment type such as `full_time`
- `min_salary`: minimum acceptable upper bound of a published salary range

Examples:

```text
GET /jobs?q=python
GET /jobs?employment_type=full_time&min_salary=1000
GET /jobs?work_mode=remote&q=react
```

`GET /matches` returns opportunities evaluated with the current profile rules. It accepts `min_score`, `limit`, and `offset`.

```text
GET /matches
GET /matches?min_score=70
```

Run one ingestion cycle manually:

```powershell
docker compose run --rm worker python -m jobradar.worker --once
```

## Source configuration

The default source configuration enables Djinni and disables the mock source:

```dotenv
MOCK_SOURCE_ENABLED=false
DJINNI_SOURCE_ENABLED=true
DJINNI_JOBS_URL=https://djinni.co/jobs/l-nonhr/remote/
DJINNI_REMOTE_ONLY=true
DJINNI_REQUEST_TIMEOUT_SECONDS=20
DJINNI_MAX_ITEMS=50
DJINNI_POLL_INTERVAL_SECONDS=3600
FREELANCER_SOURCE_ENABLED=false
FREELANCER_API_BASE_URL=https://www.freelancer.com/api/projects/0.1
FREELANCER_WEB_BASE_URL=https://www.freelancer.com
FREELANCER_OAUTH_TOKEN=replace-with-personal-access-token
FREELANCER_SEARCH_QUERIES=python django;react javascript typescript;shopify liquid;rest api postgresql
FREELANCER_REQUEST_TIMEOUT_SECONDS=20
FREELANCER_PAGE_SIZE=50
FREELANCER_MAX_PAGES_PER_QUERY=2
FREELANCER_POLL_INTERVAL_SECONDS=3600
WORKUA_SOURCE_ENABLED=true
WORKUA_READER_BASE_URL=https://r.jina.ai/http://www.work.ua
WORKUA_SEARCH_URLS=https://www.work.ua/en/jobs-remote-python/;https://www.work.ua/en/jobs-remote-django/;https://www.work.ua/en/jobs-remote-react/;https://www.work.ua/en/jobs-remote-javascript/;https://www.work.ua/en/jobs-remote-shopify/
WORKUA_REQUEST_TIMEOUT_SECONDS=30
WORKUA_MAX_ITEMS=50
WORKUA_REMOTE_ONLY=true
WORKUA_DETAIL_CACHE_TTL_SECONDS=86400
WORKUA_DETAIL_REQUEST_DELAY_SECONDS=1.5
WORKUA_RETRY_ATTEMPTS=2
WORKUA_POLL_INTERVAL_SECONDS=21600
JOBS_CZ_SOURCE_ENABLED=true
JOBS_CZ_SEARCH_URLS=https://www.jobs.cz/prace/?q%5B0%5D=Python%20remote;https://www.jobs.cz/prace/?q%5B0%5D=React%20remote;https://www.jobs.cz/prace/?q%5B0%5D=JavaScript%20remote;https://www.jobs.cz/prace/?q%5B0%5D=Shopify%20remote
JOBS_CZ_REQUEST_TIMEOUT_SECONDS=30
JOBS_CZ_MAX_ITEMS=20
JOBS_CZ_REMOTE_ONLY=true
JOBS_CZ_DETAIL_CACHE_TTL_SECONDS=86400
JOBS_CZ_DETAIL_REQUEST_DELAY_SECONDS=1.0
JOBS_CZ_RETRY_ATTEMPTS=2
JOBS_CZ_POLL_INTERVAL_SECONDS=21600
STARTUPJOBS_CZ_SOURCE_ENABLED=true
STARTUPJOBS_CZ_API_BASE_URL=https://back.startupjobs.cz
STARTUPJOBS_CZ_WEB_BASE_URL=https://www.startupjobs.cz
STARTUPJOBS_CZ_SEARCH_QUERIES=python django;react javascript typescript;fullstack frontend backend;shopify liquid api
STARTUPJOBS_CZ_REQUEST_TIMEOUT_SECONDS=30
STARTUPJOBS_CZ_PAGE_SIZE=20
STARTUPJOBS_CZ_MAX_PAGES_PER_QUERY=2
STARTUPJOBS_CZ_MAX_ITEMS=20
STARTUPJOBS_CZ_REMOTE_ONLY=true
STARTUPJOBS_CZ_DETAIL_CACHE_TTL_SECONDS=86400
STARTUPJOBS_CZ_DETAIL_REQUEST_DELAY_SECONDS=0.5
STARTUPJOBS_CZ_POLL_INTERVAL_SECONDS=21600
PRACE_CZ_SOURCE_ENABLED=true
PRACE_CZ_SEARCH_URLS=https://www.prace.cz/nabidky/programator/;https://www.prace.cz/nabidky/?q=python;https://www.prace.cz/nabidky/?q=react;https://www.prace.cz/nabidky/?q=javascript;https://www.prace.cz/nabidky/?q=django;https://www.prace.cz/nabidky/?q=shopify
PRACE_CZ_REQUEST_TIMEOUT_SECONDS=30
PRACE_CZ_MAX_ITEMS=20
PRACE_CZ_REMOTE_ONLY=true
PRACE_CZ_DETAIL_CACHE_TTL_SECONDS=86400
PRACE_CZ_DETAIL_REQUEST_DELAY_SECONDS=1.0
PRACE_CZ_RETRY_ATTEMPTS=2
PRACE_CZ_POLL_INTERVAL_SECONDS=21600
FREELANCE_CZ_SOURCE_ENABLED=true
FREELANCE_CZ_API_BASE_URL=https://www.freelance.cz/api/ui
FREELANCE_CZ_WEB_BASE_URL=https://www.freelance.cz
FREELANCE_CZ_CATEGORY=programovani-it
FREELANCE_CZ_REQUEST_TIMEOUT_SECONDS=30
FREELANCE_CZ_PAGE_SIZE=25
FREELANCE_CZ_MAX_PAGES=2
FREELANCE_CZ_MAX_ITEMS=25
FREELANCE_CZ_REMOTE_ONLY=true
FREELANCE_CZ_DETAIL_CACHE_TTL_SECONDS=86400
FREELANCE_CZ_DETAIL_REQUEST_DELAY_SECONDS=0.5
FREELANCE_CZ_POLL_INTERVAL_SECONDS=21600
STARTUP_JOBS_SOURCE_ENABLED=false
STARTUP_JOBS_API_BASE_URL=https://api.startup.jobs
STARTUP_JOBS_API_KEY=replace-with-startup-jobs-api-key
STARTUP_JOBS_ROLE=engineering
STARTUP_JOBS_REQUEST_TIMEOUT_SECONDS=30
STARTUP_JOBS_PAGE_SIZE=50
STARTUP_JOBS_MAX_PAGES=2
STARTUP_JOBS_MAX_ITEMS=100
STARTUP_JOBS_POLL_INTERVAL_SECONDS=21600
JOBICY_SOURCE_ENABLED=true
JOBICY_API_URL=https://jobicy.com/api/v2/remote-jobs
JOBICY_INDUSTRY=engineering
JOBICY_REQUEST_TIMEOUT_SECONDS=30
JOBICY_MAX_ITEMS=100
JOBICY_POLL_INTERVAL_SECONDS=21600
WE_WORK_REMOTELY_SOURCE_ENABLED=true
WE_WORK_REMOTELY_FEED_URL=https://weworkremotely.com/categories/remote-programming-jobs.rss
WE_WORK_REMOTELY_REQUEST_TIMEOUT_SECONDS=30
WE_WORK_REMOTELY_MAX_ITEMS=100
WE_WORK_REMOTELY_POLL_INTERVAL_SECONDS=3600
DOU_JOBS_SOURCE_ENABLED=true
DOU_JOBS_FEED_URL=https://jobs.dou.ua/vacancies/feeds/?remote
DOU_JOBS_REQUEST_TIMEOUT_SECONDS=30
DOU_JOBS_MAX_ITEMS=100
DOU_JOBS_POLL_INTERVAL_SECONDS=1800
HIMALAYAS_SOURCE_ENABLED=true
HIMALAYAS_API_URL=https://himalayas.app/jobs/api
HIMALAYAS_REQUEST_TIMEOUT_SECONDS=30
HIMALAYAS_PAGE_SIZE=20
HIMALAYAS_MAX_PAGES=5
HIMALAYAS_MAX_ITEMS=100
HIMALAYAS_POLL_INTERVAL_SECONDS=86400
THE_MUSE_SOURCE_ENABLED=false
THE_MUSE_API_URL=https://www.themuse.com/api/public/jobs
THE_MUSE_API_KEY=replace-with-registered-the-muse-api-key
THE_MUSE_CATEGORIES=Software Engineering
THE_MUSE_LEVELS=Entry Level;Mid Level
THE_MUSE_LOCATION=Flexible / Remote
THE_MUSE_REQUEST_TIMEOUT_SECONDS=30
THE_MUSE_MAX_PAGES=5
THE_MUSE_MAX_ITEMS=100
THE_MUSE_POLL_INTERVAL_SECONDS=21600
ATS_SOURCE_ENABLED=true
ATS_COMPANIES_FILE=companies.yaml
ATS_REQUEST_TIMEOUT_SECONDS=30
ATS_MAX_ITEMS_PER_COMPANY=500
ATS_POLL_INTERVAL_SECONDS=86400
ARBEITNOW_SOURCE_ENABLED=true
ARBEITNOW_API_URL=https://www.arbeitnow.com/api/job-board-api
ARBEITNOW_REQUEST_TIMEOUT_SECONDS=30
ARBEITNOW_MAX_PAGES=3
ARBEITNOW_MAX_ITEMS=100
ARBEITNOW_POLL_INTERVAL_SECONDS=21600
REMOTIVE_SOURCE_ENABLED=true
REMOTIVE_API_URL=https://remotive.com/api/remote-jobs
REMOTIVE_CATEGORY=software-dev
REMOTIVE_REQUEST_TIMEOUT_SECONDS=30
REMOTIVE_MAX_ITEMS=100
REMOTIVE_POLL_INTERVAL_SECONDS=21600
```

If `.env` was copied during Iteration 1, update `MOCK_SOURCE_ENABLED` to `false` and add the Djinni settings above before restarting the stack.

Keep `FREELANCER_SOURCE_ENABLED=false` until the personal access token has been added to the local `.env`. Enable the source and validate one read-only collection cycle with:

```powershell
docker compose run --rm worker python -m jobradar.worker --once
```

Work.ua is enabled by default. Search URLs must remain remote-only pages; the adapter also verifies the Remote marker in every parsed card and loads the complete description from the public vacancy detail page. Duplicate vacancies returned by several queries are collapsed by Work.ua vacancy ID.

Jobs.cz is enabled by default. The configured searches include the `remote` keyword but do not use the site's mostly-from-home arrangement filter. A card must contain an explicit strict marker such as `100% Remote`, `Full Remote`, `Remote Only`, or the Czech equivalent before its public detail page is requested. Any mostly-from-home, occasional-home-office, or hybrid marker is a hard rejection, even when another part of the vacancy claims full remote. Duplicate vacancies returned by several queries are collapsed by Jobs.cz advertisement ID.

StartupJobs.cz, Prace.cz, and Freelance.cz are enabled by default. StartupJobs.cz queries the public JSON endpoints and rechecks the exclusive remote mode on both the search result and full detail response. Prace.cz uses the embedded `FULL_REMOTE` value before requesting a detail page, then evaluates the complete JSON-LD description. Freelance.cz sends the platform's public `remotePreference=remote` filter and accepts only `project_only` results. When Freelance.cz returns `descriptionTruncated=true` to a visitor, JobRadar preserves the public excerpt, stores `description_truncated=true`, and applies a scoring penalty. Obtaining the private full text would require authenticated access and is intentionally outside this unauthenticated adapter.

Startup.jobs is disabled until its free API key is configured. Create a key at `https://startup.jobs/account/api_keys`, then set `STARTUP_JOBS_API_KEY` and `STARTUP_JOBS_SOURCE_ENABLED=true`. The adapter requests only `workplace_type=remote` jobs in the Engineering role, follows official cursor pagination, preserves the full public description, and uses the original Startup.jobs URL in every Telegram message.

The background worker wakes every `WORKER_INTERVAL_SECONDS`, but runs a source only when that source's own `*_POLL_INTERVAL_SECONDS` plus its stable `SOURCE_POLL_JITTER_RATIO` adjustment has elapsed. The default jitter is plus or minus 15 percent and changes only after the source runs, preventing synchronized requests without moving the deadline on every worker wake-up. Djinni, Freelancer.com, and the two RSS feeds keep source-appropriate shorter base intervals. Work.ua, Jobs.cz, Prace.cz, StartupJobs.cz, Freelance.cz, and Startup.jobs use six-hour base intervals. Himalayas and the three direct ATS sources use daily base intervals. A manual one-shot command ignores these intervals and must be used only for explicit verification.

Sources with separate search cards and detail pages persist a detail-fetch timestamp on each listing. A full description is requested only for a new card, a changed card, or after the 24-hour cache TTL. Requests for new details are serialized with a source-specific delay. Work.ua, Jobs.cz, and Prace.cz also retry one rate-limited request while respecting a bounded `Retry-After` value. A failed source walk remains ineligible for missing-listing reconciliation, so a temporary rate limit cannot deactivate stored jobs.

Jobicy and We Work Remotely are enabled by default and require no credentials. Jobicy is limited to one request every six hours, which follows its recommendation to poll only a few times per day and never more than hourly. We Work Remotely uses the public Programming RSS feed requested by the platform and polls at the feed's one-hour TTL. Both adapters keep the platform URL and source attribution in Telegram.

DOU Jobs is enabled by default and requires no credentials. It polls the official remote RSS feed every 30 minutes because the feed contains only the newest entries. A record must retain DOU's explicit remote marker and must not describe hybrid work or mandatory office days. Full RSS descriptions are normalized before the shared scoring rules run, so military and unpaid opportunities are stored for audit statistics but never qualify for notifications. The rolling feed is explicitly marked as a partial inventory, preventing the normal source reconciliation step from treating entries outside the newest RSS window as closed.

Himalayas is enabled by default and requires no credentials. It uses the public JSON browse API with opaque cursor pagination, requests at most 20 records per page, and polls once every 24 hours because the upstream dataset is refreshed daily. Salary amount, currency, and period are normalized into the shared salary fields; categories, parent categories, seniority, location restrictions, and timezone restrictions remain available in structured raw data for deterministic matching and audit. The configured five-page window is a partial inventory, so missing records are expired by the global stale-record policy instead of being deactivated after one collection. Job messages retain the Himalayas application link and source attribution.

The Muse is disabled by default. Its public API permits keyless testing, but the API terms require application registration for ongoing use. Register the personal application at `https://www.themuse.com/developers/api/v2/apps`, set `THE_MUSE_API_KEY`, and enable `THE_MUSE_SOURCE_ENABLED`. JobRadar sends at most five paginated requests every six hours, uses the official remote, category, and seniority filters, and still rejects any returned item without the exact remote location marker. Because this is a bounded rolling window, missing jobs are handled by global 30-day expiration rather than source reconciliation.

Direct ATS collection is disabled by the built-in settings default and enabled by `.env.example`. Set `ATS_SOURCE_ENABLED=true` and maintain the company catalog in `companies.yaml`. Every enabled item needs `name`, one of the providers `greenhouse`, `lever`, or `ashby`, and the provider-specific public board `identifier`. The runtime image copies this file to `/app/companies.yaml`. No API key, browser session, or HTML scraping is used.

Arbeitnow and Remotive are also enabled by default without credentials. Arbeitnow makes up to three paginated API requests every six hours and accepts only the API's explicit remote records. Remotive makes one Software Development API request every six hours, matching its published maximum of four requests per day. Remotive salaries are converted only when the source string contains a recognized currency; missing or ambiguous amounts remain empty instead of being inferred.

Cross-source deduplication requires both a non-empty title and company and applies only to employment vacancies, not freelance projects. Comparison ignores case and repeated whitespace. Existing duplicate rows can also be merged explicitly without waiting for the next worker cycle:

```powershell
docker compose run --rm worker python -m jobradar.maintenance deduplicate-opportunities
```

Force every active opportunity to be evaluated with the current profile and rules version:

```powershell
docker compose run --rm worker python -m jobradar.maintenance rescore-all
```

The command updates or creates the current-version evaluation without deleting historical rule-version rows. `/latest`, `/all`, API matches, and statistics query the current rules version, so an opportunity that falls below `MATCHING_MIN_SCORE` disappears from matching results immediately after the command completes.

## Matching and Telegram

The built-in profile targets remote junior full-stack and front-end roles based on React, JavaScript, Python, Django, PostgreSQL, REST APIs, and Shopify/Liquid experience. Employment and freelance projects use separate deterministic strategies under rules version `bohdan-multi-source-v11-negative-skills`; the default notification threshold is 55. Employment listings receive a single 15-point penalty when they mention unsupported backend, frontend, or CMS technologies. An unsupported technology in the title, required stack, or structured skills keeps the final score below the notification threshold, while an explicitly optional technology only receives the soft penalty. Before platform-specific scoring, shared rejection and sanity layers assign score zero to volunteer or unpaid work, military recruitment, mobilisation, armed-forces and defence-sector work, army service, US-only remote work, work without base pay, and explicit account-rental scams. Equal-opportunity boilerplate that merely protects military or veteran status is ignored and does not weaken rejection of actual military work. Telegram headings, field labels, scoring explanations, concerns, employment and contract types, monetary periods, employer status, and test messages are rendered in Russian.

Freelance scoring converts published budgets to USD using the exchange rate supplied by Freelancer.com, evaluates fixed and hourly budgets separately, accounts for bid competition and available employer reputation, penalizes broad or incompatible scope, and rejects explicit scam patterns such as deposits, account sharing, credential requests, off-platform payment, and unpaid trials. Small budgets are neutral instead of negative; verified fixed-price projects in the USD 5-100 reputation-building range receive an additional 15 points.

Configure Telegram in `.env`:

```dotenv
MATCHING_ENABLED=true
MATCHING_MIN_SCORE=55
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=replace-with-bot-token
TELEGRAM_CHAT_ID=replace-with-chat-id
TELEGRAM_NOTIFY_EXISTING=false
TELEGRAM_MAX_MESSAGES_PER_CYCLE=3
TELEGRAM_REQUEST_TIMEOUT_SECONDS=20
TELEGRAM_POLLING_ENABLED=true
TELEGRAM_POLL_TIMEOUT_SECONDS=15
TELEGRAM_LATEST_LIMIT=5
NBU_RATES_URL=https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json
NBU_REQUEST_TIMEOUT_SECONDS=20
EMPLOYMENT_STALE_AFTER_DAYS=30
FREELANCE_STALE_AFTER_DAYS=7
SOURCE_POLL_JITTER_RATIO=0.15
```

When an opportunity publishes an amount, the notification service loads one official NBU rate snapshot per dispatch and renders the same range in USD, UAH, and CZK. A rate failure prevents delivery of an incomplete monetary notification; the opportunity remains available for retry on the next worker cycle. Opportunities without a published amount are still delivered without an invented salary or budget.

The bot container registers and handles these commands:

- `/latest`: sends the newest matching opportunities already stored in the database; it does not start a concurrent source collection cycle.
- `/all`: sends every active opportunity evaluated at or above the current matching threshold, without a result limit; hidden opportunities are excluded.
- `/favorites`: sends a linked list of favorite opportunities.
- `/stats`: shows collected, evaluated, matched, filtered, favorite, and hidden counts.
- `/clear`: deletes every tracked opportunity message from the Telegram chat.
- `/pause`: pauses automatic notifications while collection and matching continue.
- `/resume`: resumes future notifications without replaying matches accumulated during the pause.

The favorite button toggles the favorite state. The hide button marks an opportunity as hidden and removes it from subsequent automatic and `/latest` results. A hidden message exposes a restore button that returns the opportunity to the default state. The source-link button remains available after hiding. Every notification heading starts with the stored source display name.

Reset every hidden state without changing collected opportunities or evaluations:

```powershell
docker compose run --rm worker python -m jobradar.maintenance reset-hidden
```

Run the same stale-expiration policy manually without waiting for the next worker cycle:

```powershell
docker compose run --rm worker python -m jobradar.maintenance expire-stale
```

`TELEGRAM_NOTIFY_EXISTING=false` prevents the first enabled cycle from sending every historical match. New matching opportunities are delivered once per profile rules version and listing content hash. Failed deliveries are retried up to three times.

After opening the bot chat and sending `/start`, validate the connection with one test message:

```powershell
docker compose run --rm worker python -m jobradar.worker --test-telegram
```

## Tests

Install development dependencies in a Python 3.13 virtual environment:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -m "not integration"
```

Run PostgreSQL integration tests in containers:

```powershell
docker compose --profile test run --rm test
```

Run quality checks:

```powershell
ruff check .
mypy src
```

## Database migrations

Create a migration after changing SQLAlchemy models:

```powershell
alembic revision --autogenerate -m "describe change"
```

Apply migrations:

```powershell
alembic upgrade head
```
