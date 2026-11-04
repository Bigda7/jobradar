# Robota.ua partner export review

Date: 2026-10-20 (updated with provider IP allowlist and schema/closure confirmation; implemented in v1.2.29).

## Latest support reply and implementation status

The provider replied to the follow-up inquiry confirming:

1. Server IP allowlisting actions were completed so automated requests from the JobRadar server pass without Cloudflare restrictions.
2. Vacancy records in `vacancies_dodomu.json` include title (`DescriptionShort`) and full HTML description (`Description`), along with `Url`, `WorkType`, `Salary`, `SalaryFrom`, `SalaryTo`, `SalaryTxt`, `LastUpdate`, `CityId`, `CompanyId`, `RubricId`, `BranchId`, and `ProfessionId`.
3. Only active, published vacancies are included in `vacancies_dodomu.json`. If a previously obtained vacancy ID is absent in a completely downloaded new export snapshot, the vacancy is closed (`source_closed`).
4. Export update hours (`03:50`, `08:50`, `13:50`, `15:50`, and `19:50`) are tied to `Europe/Kyiv` local time with seasonal daylight-saving transitions.

Read-only verification from the production worker confirmed `HTTP 200 OK` for `vacancies_dodomu.json` (~55.4 MB gzipped, ~277.35 MB uncompressed, ~65,000 active vacancies), all six dictionary files (`cities.json`, `rubrics.json`, `branches.json`, `professions.json`, `regions.json`, `companies.json`), and direct vacancy detail endpoints (`https://api.robota.ua/vacancy?id=<id>`).

### Hybrid partner export + direct API architecture (v1.2.29)

Because `vacancies_dodomu.json` does not include the `clusters` array where Robota.ua stores the remote-work checkbox (`group.id == 2004`, `"Удаленная работа"` / `"Віддалена робота"`), and 96% of remote vacancies on Robota.ua use that structured cluster badge without repeating the word "remote" in body text, `RobotaUaSource` in `v1.2.29` implements a hybrid architecture:

- **Streaming partner export snapshot (`vacancies_dodomu.json`):** Streamed with incremental `gzip` decompression and incremental JSON object decoding so memory overhead remains bounded. Extracts the complete active vacancy ID set (`record_snapshot_external_ids`), refreshes cached listings in place, and discovers IT (`RubricId == 1` or `BranchId == 2`) and Czechia (`CityId == 1001`) remote candidates directly from the export.
- **Exact vacancy closure reconciliation:** When the export JSON array completes cleanly, `IngestionService` marks active `robota_ua` listings absent from the snapshot as closed (`is_active = False`, `archive_reason = "source_closed"`), protected by the 80% `SuspiciousSourceInventoryError` safety guard. Truncated or failed export downloads never deactivate stored listings.
- **Direct API detail enrichment (`https://api.robota.ua/vacancy?id=<id>`):** Fetches structured `companyName`, `cityName`, `scheduleId`, HTML `description`, and `clusters` (`group.id == 2004`) directly from `api.robota.ua` without routing detail requests through `r.jina.ai`.

## Evidence and meaning

The owner supplied a support reply stating that the employer Swagger API returns only
the registered employer's own vacancies. Aggregation uses partner access instead.
Support reviewed the project request, supplied integration documentation and stated
that signing a contract is unnecessary for the service they described.

Documented export root:

`https://ministry-education-export.robota.ua/ministry-education-export/`

Files: `branches.json`, `cities.json`, `professions.json`, `rubrics.json`,
`regions.json`, `companies.json`, and `vacancies_dodomu.json`.

The document says export runs daily at 03:50, 08:50, 13:50, 15:50 and 19:50 (`Europe/Kyiv`).

The reply is useful project-specific integration guidance, not proof that every
possible future commercial use, retention period or redistribution format is settled.
Do not interpret "public service" as a blanket license or permission to bypass protection.
