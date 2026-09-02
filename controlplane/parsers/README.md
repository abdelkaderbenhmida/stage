# parsers

Turn each security tool's raw JSON report into the platform's normalized
`ParsedFindings` shape (a common five-value severity scale plus per-finding detail),
so the scan gate and the UI don't need tool-specific logic.

- `__init__.py` — shared `ParsedFindings`/`Finding` types and the severity
  normalization scale.
- `trivy_parser.py` — Trivy image vulnerability report → `ParsedFindings`.
- `gitleaks_parser.py` — Gitleaks secret-scan report → `ParsedFindings`; Gitleaks has
  no native severity, so every finding is mapped to `high`.
- `pip_audit_parser.py` — pip-audit dependency report → `ParsedFindings`; severity may
  arrive as a plain string (`CRITICAL`) or a CVSS detail dict, both handled.

Corresponding fixtures for these parsers live in `controlplane/tests/fixtures/`.
