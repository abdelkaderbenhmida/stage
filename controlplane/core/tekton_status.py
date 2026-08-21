"""Translate Tekton PipelineRun/TaskRun status into this platform's vocabulary.

Kept free of any cluster access so it can be tested against recorded API
objects rather than a live Tekton install — the mapping is where the bugs are,
not the kubectl call.

The vocabulary is the same six values every other producer uses (pending,
running, succeeded, failed, cancelled, skipped). Tekton does not report a
status field at all: it reports a `Succeeded` *condition* whose value is the
string "True", "False" or "Unknown", qualified by a reason. "Unknown" is the
one that catches people out — it means both "still running" and "waiting to
start", and treating it as a failure fails every pipeline the moment it is
looked at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Reasons Tekton reports for a run that was deliberately stopped. These arrive
# with condition status "False", exactly like a genuine failure, so without
# this set a cancelled deploy is reported to the tenant as a broken one.
_CANCELLED_REASONS = {
    "Cancelled",
    "TaskRunCancelled",
    "PipelineRunCancelled",
    "StoppedRunFinally",
    "CancelledRunFinally",
}

# "Unknown" plus one of these means the work has not started yet, as opposed to
# being in flight.
_PENDING_REASONS = {"Pending", "Started", "PipelineRunPending", "TaskRunPending"}


@dataclass(frozen=True)
class StepState:
    """One Tekton task, in the shape the pipeline graph and job_steps want."""

    name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


def _condition(obj: dict) -> dict:
    """The `Succeeded` condition, which is the only one Tekton sets.

    Indexing [0] blindly is the common shortcut and it is wrong: the list is
    not ordered by contract, and an object that has not been reconciled yet has
    no conditions at all.
    """
    conditions = ((obj or {}).get("status") or {}).get("conditions") or []
    for condition in conditions:
        if condition.get("type") == "Succeeded":
            return condition
    return {}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Kubernetes emits RFC3339 with a trailing Z, which fromisoformat only
        # learned to accept in 3.11 — spelled out so this does not quietly
        # start returning None on an older interpreter.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def condition_status(obj: dict) -> str:
    """Map one PipelineRun or TaskRun onto the six-value vocabulary."""
    condition = _condition(obj)
    status = condition.get("status")
    reason = condition.get("reason", "")

    if status == "True":
        return "succeeded"
    if status == "False":
        return "cancelled" if reason in _CANCELLED_REASONS else "failed"
    if status == "Unknown":
        if reason in _CANCELLED_REASONS:
            return "cancelled"
        return "pending" if reason in _PENDING_REASONS else "running"
    # No condition yet: the object exists but Tekton has not looked at it.
    return "pending"


def taskrun_state(taskrun: dict) -> StepState:
    status = ((taskrun or {}).get("status") or {})
    labels = ((taskrun or {}).get("metadata") or {}).get("labels") or {}
    # The pipeline task name, not the TaskRun's own name: Tekton suffixes the
    # latter with a random string, so using it would show the tenant
    # "deploy-run-8f2kd-build" where "build" is meant.
    name = labels.get("tekton.dev/pipelineTask") or (taskrun.get("metadata") or {}).get("name", "")
    state = condition_status(taskrun)
    condition = _condition(taskrun)
    return StepState(
        name=name,
        status=state,
        started_at=_parse_time(status.get("startTime")),
        finished_at=_parse_time(status.get("completionTime")),
        error_message=condition.get("message") if state in ("failed", "cancelled") else None,
    )


def pipeline_steps(pipelinerun: dict, taskruns: list[dict], declared: list[str]) -> list[StepState]:
    """Every declared task, in pipeline order, whether or not it has run.

    ``declared`` is the task list from the Pipeline definition. Deriving the
    order from the TaskRuns instead would only ever show the tasks that already
    started, so the graph would grow boxes as it went instead of showing the
    tenant the shape of their pipeline up front — which is the entire point of
    drawing it.
    """
    by_name = {}
    for taskrun in taskruns:
        state = taskrun_state(taskrun)
        if state.name:
            by_name[state.name] = state

    overall = condition_status(pipelinerun)
    terminal = overall in ("succeeded", "failed", "cancelled")

    steps: list[StepState] = []
    for name in declared:
        state = by_name.get(name)
        if state is not None:
            steps.append(state)
            continue
        # No TaskRun for a declared task. Once the run is over that means it
        # never got to run — after a failure, or because it was skipped by a
        # `when` guard. While the run is still going it simply has not started.
        steps.append(StepState(name=name, status="skipped" if terminal else "pending"))
    return steps
