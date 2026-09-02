# runners/scanners

Security scanner runners. Each produces a raw JSON report; normalization to the
platform's shared finding shape happens in `controlplane/parsers/`, not here. The scan
gate fails closed — an unreadable or failed report blocks the rollout exactly like a
CRITICAL finding, with no bypass flag.

- `base.py` — `RawResult` / `ScannerError` shared types.
- `trivy.py` — image vulnerability scan against a built image.
- `gitleaks.py` — secret scan against an already-cloned repo, run with `--network=none`.
- `pip_audit.py` — dependency scan against `requirements.txt`, queried against the PyPI
  OSV index.
