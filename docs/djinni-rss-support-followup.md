# Djinni RSS Metadata Follow-Up

Prepared: 2026-10-05. Status: owner reports sending the follow-up and supplied the provider reply.
See [the received clarification](djinni-provider-clarification-2026-10-05.md). The original draft
below is retained as preparation history; this task did not send a provider message.

## Scope and Evidence

The owner approved starting the remaining Djinni steps in order. This document covers
the first step: identifying missing RSS data and preparing a focused support reply.
The current adapter and tests were inspected; no new live traversal or test run was
performed for this documentation-only task. Earlier live RSS evidence is recorded in
[the migration review](djinni-rss-review.md).

The inspected RSS snapshots contain title, link, description, publication-date label,
GUID and categories. They do not expose the following facts as separate fields:

| Missing field | Why it matters | Current behavior |
| --- | --- | --- |
| Company | Display and cross-source duplicate confidence | New listings leave it unset; known cached metadata may be stale. |
| Salary range, currency and period | Salary scoring and sanity checks | New structured values remain unset, even if amounts appear in prose. |
| Eligible countries or regions | Remote work may still have country restrictions | Remote status follows the RSS filter; it does not establish worldwide eligibility. Existing text rules cannot detect restrictions omitted from the description. |
| Employment type | Full-time, part-time and contract scoring | Unknown new values remain unset; the remote filter describes work mode, not employment type. |

The adapter retains older metadata with `metadata_origin=previously_stored_metadata`.
That marker does not verify freshness. Full descriptions remain available to existing
matching rules; no reliable extraction of every missing structured field is claimed.
The provider subsequently confirmed that RSS `pubDate` is update/bump time, not first publication.

## Reply Draft

The following Ukrainian text is intended for the existing support conversation.
It has not been delivered by JobRadar or this task.

### Subject

Уточнення щодо даних вакансій у RSS для JobRadar

### Message

Сергію, добридень!

Дякую за відповідь. Перевели збір вакансій у JobRadar на рекомендований вами RSS.
Використовуємо фільтри та обмежуємо частоту приблизно до 75 запитів на хвилину,
нижче зазначених вами 100. Повторні вакансії об'єднуємо за їхніми ідентифікаторами.
Нова реалізація поки перевірена локально, ще не встановлена на робочий сервер.

У перевірених RSS-відповідях є назва, повний опис, посилання, дата та категорії,
але немає окремих полів із назвою компанії, зарплатою, типом зайнятості й
географічними обмеженнями для кандидата. Ці дані важливі для коректного підбору:
наприклад, віддалена вакансія може бути доступна лише кандидатам з певної країни,
а в описі це не завжди зазначено.

Підкажіть, будь ласка:

1. Чи можна отримати ці поля через RSS — окремі XML-поля, розширений формат
   або додаткові параметри? Якщо ні, чи плануєте ви додати їх?
2. Якщо цих даних немає в RSS, чи є інший рекомендований вами спосіб їх отримувати
   автоматично для тих самих вакансій? Якщо такого способу немає, залишимо
   відсутні значення невідомими.
3. Що означає `pubDate`: дату першої публікації вакансії чи її останнього
   оновлення або підняття у списку?
4. У перевірених вибірках одна RSS-відповідь містить максимум 100 вакансій;
   параметр `page=2` повертає ту саму вибірку. Чи є рекомендований спосіб отримати
   всі актуальні вакансії, що відповідають фільтрам, або варто об'єднувати
   окремі RSS-вибірки за категоріями, досвідом та рівнем англійської?

Дякую!
Богдан

## Actions After the Reply

- If Djinni supplies a documented RSS extension or recommended supplemental endpoint,
  review its schema and access limits, then propose a scoped adapter change with regression
  tests for missing fields, provenance, stable identities and notification deduplication.
- If no additional source exists, retain explicit unknown values and the documented cache
  limitation. Do not guess data, silently add HTML enrichment or claim the gap is fixed.
- Clarification is not a prerequisite for publishing the current RSS implementation if the
  owner accepts these known limitations. It does not authorize publication or deployment.
- Sending this draft, GitHub publication, production configuration changes, rollout and
  real notification delivery remain separate actions requiring explicit authorization.

## Next Steps, Not Yet Executed

1. Separately authorized publication and rollout of the existing verified RSS implementation;
   review the deployed item cap and traversal budgets without changing polling or disabling
   notifications. Preserve unrelated working-tree edits and prepare rollback evidence.
2. After rollout, verify a real production collection, coverage warnings, worker resources,
   database/storage growth and notification queue progress without resending historical matches.

No code, dependencies, matching rules, API contracts, production settings, database rows,
notification delivery, commits or remote resources were changed by preparing this document.
