# Robota.ua partner export review

Date: 2026-10-16. Status: documentation reviewed; export access blocked; no adapter change.

## Latest support reply and follow-up draft

Newest checkpoint: the owner reports sending the latest follow-up described below.
Await the provider's response; mailbox delivery was not independently verified.
The separately approved local frontend Escape fix does not change the export loader
or current source adapter. Earlier draft-only status below is historical.

The owner supplied another reply on October16: no API key is required; compressed
versions are unavailable; the vacancy file contains current vacancies and omits
closed ones; export hours are described as Kyiv time UTC+3. The sentence saying
the vacancy description is absent is ambiguous in this correspondence: whether it
means missing vacancy body text or missing field documentation is not established.
Full-market coverage and seasonal-clock handling are also not established.

Owner approved working on the reply only, not implementing the loader or fixing UI.
At14:43UTC a read-only diagnostic from the running production worker confirmed the
public egress IP through AWS checkip. A single bounded GET to the documented vacancy
export still returned403/Cloudflare HTML/challenge, with a provider request identifier.
At most1024 response bytes were read; no full export, source run, DB operation,
service restart or notification was triggered. The actual IP/request identifier are
in the owner-facing workspace draft rather than public repository documentation.

Ready follow-up: workspace `ROBOTA_EXPORT_FOLLOWUP_LETTER_2026-10-16.md`, for replying
to CARE-175372. It requests access diagnosis and clarifies body/schema, coverage/
absence semantics, seasonal time and snapshot completion. Draft NOT sent by agent;
owner sending is not yet confirmed. Earlier timezone/authentication uncertainty below
is historical where superseded by this reply, not a new unresolved question about keys.

Later October16 update: the owner reported sending the follow-up letter and requested
waiting for Robota's reply before starting the export loader. No loader implementation
has begun. Five owner-supplied dictionaries were parsed successfully with unique IDs
within each:40 branches,3755 cities,448 professions,35 rubrics,27431 companies.
Regions were pasted separately. Vacancy schema and actual export size remain unknown.
The owner's browser consumed approximately5GB of RAM while opening the vacancy link;
this does not measure the file's transfer size. Automated403 responses and browser
memory consumption are distinct observations. No blanket inaccessibility claim is made.
The scheduled old Reader adapter still failed12 times in the latest72h production
review; this is not evidence that the unimplemented partner export was attempted.
See workspace SOURCE_AND_TRACKER_REVIEW_2026-10-16.md for the dated read-only snapshot.

## Evidence and meaning

The owner supplied a support reply stating that the employer Swagger API returns only
the registered employer's own vacancies. Aggregation uses partner access instead.
Support reviewed the project request, supplied integration documentation and stated
that signing a contract is unnecessary for the service they described.

The supplied SharePoint document was retrieved through its normal anonymous download
link, without signing in. Its title is "Екпорт вакансій". This is a JSON file export,
not documentation for search endpoints in the employer API. The attachment URL with
a temporary access token is intentionally not copied here.

Documented export root:

`https://ministry-education-export.robota.ua/ministry-education-export/`

Files: `branches.json`, `cities.json`, `professions.json`, `rubrics.json`,
`regions.json`, `companies.json`, and `vacancies_dodomu.json`.
The document lists HTTP links for dictionaries and an HTTPS link for vacancies.

The document says export runs daily at 03:50, 08:50, 13:50, 15:50 and 19:50.
It does not identify a timezone, completion delay, rate limit, schema, authentication,
snapshot consistency guarantees, geographic coverage or deletion semantics.

The reply is useful project-specific integration guidance, not proof that every
possible future commercial use, retention period or redistribution format is settled.
Do not interpret "public service" as a blanket license or permission to bypass protection.

## Read-only checks

Bounded HTTPS GET requests from this workstation to `vacancies_dodomu.json`,
`cities.json`, `regions.json` and `companies.json` all returned HTTP 403 HTML,
not JSON. The vacancy response identified Cloudflare and contained the challenge
page markers `Just a moment` and `challenge-platform`.

No authenticated API, pagination guessing, proxy fallback, protection bypass,
production collection, notification or data mutation was attempted. The full export
was not downloaded. The JSON record schema, size, vacancy count, stable IDs, company,
salary, description and remote/geographic fields remain unverified.

A subsequent single bounded GET from the existing production server also returned
HTTP 403, Cloudflare HTML and the same challenge markers for `vacancies_dodomu.json`.
This was an HTTP diagnostic only, not a collection trigger, database operation or
service restart. Both checked locations therefore failed to obtain the export;
this is not a claim about every network or permanent availability.
Ask the provider to enable the intended server-to-server path or supply their
supported access procedure and a sanitized sample.

## Recommended next implementation package

After access and schema are confirmed, replace the Robota Reader transport with the
recommended export in a separately approved local package. Keep source identity,
canonical links, existing listing IDs and notification idempotency. Do not implement
a speculative field mapping from filenames alone.

The package should cover bounded downloads/timeouts and retry policy, scheduling
after confirmed export completion, required dictionary joins, remote/Prague matching
without invented residence permissions, source health diagnostics and fixture tests.
Validate an entire snapshot before accepting it. An unavailable, empty, truncated or
inconsistent snapshot must not mark old vacancies closed. Confirm full versus partial
coverage before interpreting an absent ID as a closed vacancy.

No dependency addition, publication, production switch or historical resend is
authorized by this documentation review. Work.ua, Djinni and DOU behavior is unchanged.

## Suggested reply for the owner

Добрий день! Дякую за відповідь і документацію — готовий перейти на рекомендований
вами JSON-експорт замість поточного способу отримання вакансій.

Під час перевірки 16 жовтня посилання
https://ministry-education-export.robota.ua/ministry-education-export/vacancies_dodomu.json
повертає HTTP 403 і HTML-сторінку перевірки Cloudflare замість JSON. Аналогічна відповідь
для cities.json, regions.json і companies.json. Окремий запит до файлу вакансій
із сервера JobRadar також повернув Cloudflare 403. Документ із SharePoint відкривається.

Підкажіть, будь ласка, як налаштувати підтримуваний серверний доступ до цих файлів:
чи потрібні реєстрація інтеграції, ключ або додавання IP сервера до списку дозволених?
Обходити перевірку Cloudflare не планую. За потреби надам IP сервера окремо.

Також прошу уточнити:

1. У якому часовому поясі вказані години експорту та через який час файли готові
   до завантаження? Яка рекомендована частота запитів?
2. Чи містить vacancies_dodomu.json усі опубліковані вакансії або окрему вибірку?
   Як визначати оновлення й закриття вакансій та узгодженість зі словниками?
3. Чи можете надати опис полів або невеликий приклад JSON? Особливо цікавлять
   стабільний ID, оригінальне посилання, компанія, зарплата, повний опис, формат роботи
   й обмеження щодо країни проживання кандидата.
4. Чи правильно розумію, що для описаного JobRadar дозволено зберігати отримані дані
   та показувати картки й повні описи у відкритій демоверсії та публічному API,
   з обов'язковим посиланням на robota.ua? Чи є строк зберігання та окремі умови
   для майбутнього комерційного використання?

Дякую!
Богдан
