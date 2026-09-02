# workers

The Celery worker: task definitions for provisioning, deployment, and scanning.
**Workers write their own DB sessions.** Helpers that stream progress (`_append_log`,
`_step` in `tasks.py`) open their own `SessionLocal()` and commit immediately — never
call them inside another transaction, or the job log stops updating until the outer
commit lands.

- `celery_app.py` — the Celery application. Handles graceful shutdown: on SIGTERM, any
  job in flight is marked appropriately rather than left `running` forever.
- `steps.py` — declared step templates for job pipeline graphs. Deliberately does not
  import Celery (or anything else heavy) so the API layer can import it too, to build
  the pipeline graph contract without pulling in worker dependencies.
- `tasks.py` — the tasks themselves: `provision_task`, `destroy_task`, `scan_task`,
  `deploy_task`, `undeploy_task`, plus their private helpers (workspace cleanup, repo
  clone, Dockerfile autogeneration, the secret/dependency scan gates, Tekton and GitOps
  glue, kubeconfig handling). Workers never execute user-supplied code directly — every
  external tool runs through `runners/sandbox.py`. The `[n/N] name` log line emitted by
  `_step()` is parsed by the console as a fallback pipeline view when the graph endpoint
  is unavailable — keep emitting that exact format.
