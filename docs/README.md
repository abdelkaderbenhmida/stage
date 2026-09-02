# docs

Reference material and planning documents for the DevOps Central Platform: architecture
explainers, the disaster-recovery plan, on-call runbooks, SLOs, and the internship
report and its supporting notes. `README.md` at the repo root remains the authoritative
description of the running system (API surface, config, scripts); this folder holds the
deeper narrative, historical planning docs, and operational playbooks that don't belong
in the top-level README.

- `comprendre-le-projet.md` — long-form French guide to what each tool does, how it
  works, and how the pieces fit together, with Mermaid diagrams.
- `DevOps_Central_Platform_Description.md` — professional-edition project description:
  security, GitOps, observability, and real incidents/resolutions.
- `DevOps_Central_Platform_Etapes_Implementation.md` — phase-by-phase build guide from
  scratch, with commands and validation criteria per phase.
- `PLATFORM-FIX-PLAN.md` — handoff document recording issues verified against the live
  cluster and the fixes applied.
- `disaster-recovery.md` — recovery plan for complete cluster loss (VMs destroyed,
  network config lost).
- `PRD.md` — product requirements for the pipeline graph feature (deployment pipeline
  and platform CI visualized as graphs instead of flat lists).
- `TASKS.md` — task list/status tracker for the pipeline graph work.
- `TECHNICAL.md` — technical specification (stack choices and rationale) for the
  pipeline graph feature.
- `runbook-index.md` — index of incident runbooks by severity.
- `runbook-pod-crashloop.md` — diagnosis/remediation for pods in CrashLoopBackOff.
- `runbook-vault-sealed.md` — diagnosis/remediation for a sealed Vault causing
  `/readyz` failures.
- `slo.md` — per-service SLOs, SLIs, and how each is measured.
- `test.md` — end-to-end manual test sequence exercising every tool in the platform.
- `RAPPORT_STAGE.md` — the internship report (French).
- `RAPPORT_TEMPLATE.md` — blank template/outline used to draft `RAPPORT_STAGE.md`.
