# tests

Platform-level tests: they check things outside `controlplane/` — that the demo
microservices (`app/`) boot and respond, that the platform console's static JS is
internally consistent, and that the root `README.md` stays truthful about the system it
describes. This is separate from `controlplane/tests/`, which covers the control-plane
API/worker code itself. Both suites run together as the default gate:
`pytest controlplane/tests tests/` (see repo root `CLAUDE.md`).

- `test_docs_conformance.py` — mechanically checks claims in the root README against
  the code: every DB table, every `controlplane.workers.tasks.*` Celery task, and every
  `scripts/*.sh`/`*.py` file must be mentioned in the README; the stated `/api/v1` and
  platform endpoint counts must match what the app actually serves. Failures should be
  fixed by updating the README, not the test.
- `test_services.py` — imports `shared/` config/logging/vault helpers and boots the
  `users-service` demo app with `TestClient`, checking `/`, `/livez`, and `/users`.
- `test_ui.py` — exercises the platform console's introspection endpoints
  (`controlplane/platform_ops.py` mounted standalone with auth stubbed out) and regexes
  `controlplane/web/static/platform/app.js` for literals like `state.configTab` to guard
  against renaming console tabs without updating the default/tab list together.
