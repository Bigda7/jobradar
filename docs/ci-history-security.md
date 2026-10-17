# Full Git-history security gate

October 13, 2026: locally implemented and verified, not published or deployed.

The main backend CI now uses fetch-depth0 for complete branch/tag history. Its
unchanged pinned Gitleaks image explicitly scans all available refs and merge-parent
diffs with --log-opts="--all --full-history -m",100% redaction and finding exit-code1.
The unconditional gate does not tolerate scanner failure. A new regression in
test_dependency_audits.py protects checkout and scanner settings.

Existing strict frozen Python runtime/dev export/audit gates remain required before
tests and image builds. No Python dependency, image pin, schema, API or source setting
changed in this package. The earlier local Work.ua fix remains a separate unpublished
change; three pre-existing user-owned dirty documents were preserved.

Local checks:765 non-integration tests passed,20 PostgreSQL tests deselected; Ruff,
formatting, strict mypy, application Bandit, offline lock and diff checks passed.
Frozen runtime/dev pip-audit scans found no known vulnerabilities on Windows; no
new PostgreSQL migration/rehearsal or Linux audit is claimed.

Fresh-fetched history is not shallow:119 reachable commits/81 branch,remote,tag refs;
Gitleaks processed117 nonempty patch commits, zero leaks. The existing pinned image
is labeledv8.30.0, while its executable reportsv8.30.1; no scanner upgrade occurred.
Positive scanner/audit failure fixtures are recorded in the sibling frontend document.

Changed workflows have not run on GitHub: no push/PR/merge/release/deployment. No
production data mutation, forced collection, notification send or new monitor.
Next proposed package: PostgreSQL/Caddy candidate assessment and rollback preparation,
not an authorized production component replacement.
