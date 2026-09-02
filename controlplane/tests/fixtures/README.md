# tests/fixtures

Recorded scanner JSON reports, used by `controlplane/tests/test_parsers.py` and related
tests to exercise `controlplane/parsers/` without invoking the real tools.

- `trivy_results.json` / `trivy_empty.json` — a Trivy report with findings, and a clean
  (empty `Results`) report.
- `gitleaks_report.json` / `gitleaks_empty.json` — a Gitleaks report with a finding
  (e.g. an AWS access token match), and an empty one.
- `pip_audit_results.json` / `pip_audit_empty.json` — a pip-audit report with a CVE
  (e.g. against `flask`), and an empty one.

Each empty fixture is what the scan gate must treat as "clean," while the non-empty
ones exercise severity mapping per tool.
