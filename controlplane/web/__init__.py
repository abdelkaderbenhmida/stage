"""Static web UI for the control plane (docs/PLATFORM_SPEC.md §2.1 F1-F9).

Served directly by FastAPI as plain HTML/CSS/JS with no build step: the page
talks to the same ``/api/v1`` endpoints an external client would use, holding
its access token in localStorage. Keeping it buildless means the UI ships with
the API container and needs no Node toolchain in CI.
"""

from controlplane.web.router import router

__all__ = ["router"]
