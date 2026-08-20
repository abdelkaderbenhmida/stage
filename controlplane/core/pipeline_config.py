"""User-defined pipeline stages, read from a repository's ``.platform.yml``.

The deploy pipeline's seven stages are the platform's contract: clone, build,
push, scan, render, roll out, publish the URL. They are not a description of
what any particular application needs doing — one project wants its unit
tests run before an image is built, another wants a linter, a third wants a
database migration. Hardcoding the stage list meant none of that was
possible.

A repository may therefore declare its own stages:

    # .platform.yml
    stages:
      - name: unit tests
        run: pytest -q
      - name: lint
        run: ruff check .

Deliberately NOT configurable here:

* The security gate. `block_on: []` is not a setting. A tenant who could
  turn the Trivy gate off could ship anything, which is the one thing this
  platform exists to prevent, so the gate is applied to every build whatever
  this file says.
* Where the commands run. Every stage executes inside the same sandbox
  as the rest of the pipeline — an ephemeral container with CPU, memory and
  wall-clock limits and no docker socket. These are commands out of a
  tenant's repository; they must never touch the control-plane host.

Parsing fails closed with a message naming the problem: a malformed file
stops the deployment rather than being silently ignored, because silently
ignoring it would run a pipeline the author did not ask for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_FILENAME = ".platform.yml"
# Alternate spelling, accepted because half the ecosystem writes it this way
# and a deployment failing over the file extension helps nobody.
CONFIG_FILENAME_ALT = ".platform.yaml"

# A stage name ends up in a job log line, a database row and an SVG label, so
# keep it to something printable and short rather than arbitrary text.
_NAME_RE = re.compile(r"^[A-Za-z0-9 ._:+/()-]{1,60}$")

MAX_STAGES = 20
MAX_COMMAND_LENGTH = 2000

# Conservative image reference: registry/name[:tag][@digest]. Restrictive on
# purpose — this string is handed to `docker run`, so anything that could be
# read as an extra argument has no business being in it.
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}(:[A-Za-z0-9._-]{1,128})?(@sha256:[a-f0-9]{64})?$")


class InvalidPipelineConfig(ValueError):
    """The repository's pipeline file cannot be used as written."""


@dataclass(frozen=True)
class PipelineStage:
    name: str
    run: str
    # Container the command runs in. Stages default to the platform's sandbox
    # image, which carries the platform's own tooling (trivy, kubectl, git) —
    # not an application's dependencies. A repository that wants `pytest` has
    # to say which image has pytest in it, so the default is nearly always
    # wrong for a test stage and worth overriding:
    #
    #     - name: unit tests
    #       image: python:3.11-slim
    #       run: pip install -r requirements.txt && pytest -q
    image: str = ""


def find_config(repo_dir: Path) -> Path | None:
    for filename in (CONFIG_FILENAME, CONFIG_FILENAME_ALT):
        candidate = repo_dir / filename
        if candidate.is_file():
            return candidate
    return None


def load_stages(repo_dir: Path) -> list[PipelineStage]:
    """Stages declared by the repository, or [] when it declares none.

    Raises InvalidPipelineConfig if a file exists but cannot be honoured.
    """
    path = find_config(repo_dir)
    if path is None:
        return []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise InvalidPipelineConfig(f"{path.name} is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise InvalidPipelineConfig(f"could not read {path.name}: {exc}") from exc

    if not isinstance(raw, dict):
        raise InvalidPipelineConfig(f"{path.name} must be a mapping, not {type(raw).__name__}")

    declared = raw.get("stages", [])
    if declared in (None, []):
        return []
    if not isinstance(declared, list):
        raise InvalidPipelineConfig(f"{path.name}: 'stages' must be a list")
    if len(declared) > MAX_STAGES:
        raise InvalidPipelineConfig(
            f"{path.name}: {len(declared)} stages declared, at most {MAX_STAGES} are allowed"
        )

    stages: list[PipelineStage] = []
    seen: set[str] = set()
    for position, entry in enumerate(declared, start=1):
        if not isinstance(entry, dict):
            raise InvalidPipelineConfig(f"{path.name}: stage {position} must be a mapping")

        name = str(entry.get("name") or "").strip()
        if not name:
            raise InvalidPipelineConfig(f"{path.name}: stage {position} is missing 'name'")
        if not _NAME_RE.match(name):
            raise InvalidPipelineConfig(
                f"{path.name}: stage name {name!r} must be 1-60 printable characters"
            )
        # Duplicate names would produce two identically-labelled nodes in the
        # pipeline graph, so the reader could not tell which one failed.
        if name.lower() in seen:
            raise InvalidPipelineConfig(f"{path.name}: duplicate stage name {name!r}")
        seen.add(name.lower())

        command = entry.get("run")
        # YAML turns bare `run: true` / `run: no` / `run: 1` into a bool or a
        # number, and stringifying that would hand the shell "True" rather
        # than the `true` the author wrote. Say so instead of silently
        # running the wrong command.
        if isinstance(command, bool | int | float):
            raise InvalidPipelineConfig(
                f"{path.name}: stage {name!r} has a non-string 'run' — YAML read "
                f"{command!r} as a {type(command).__name__}; quote it, e.g. run: \"true\""
            )
        if not isinstance(command, str) or not command.strip():
            raise InvalidPipelineConfig(f"{path.name}: stage {name!r} is missing 'run'")
        if len(command) > MAX_COMMAND_LENGTH:
            raise InvalidPipelineConfig(
                f"{path.name}: stage {name!r} command exceeds {MAX_COMMAND_LENGTH} characters"
            )

        image = entry.get("image", "")
        if image is None:
            image = ""
        if not isinstance(image, str):
            raise InvalidPipelineConfig(f"{path.name}: stage {name!r} has a non-string 'image'")
        image = image.strip()
        if image and not _IMAGE_RE.match(image):
            raise InvalidPipelineConfig(
                f"{path.name}: stage {name!r} image {image!r} is not a valid image reference"
            )

        stages.append(PipelineStage(name=name, run=command.strip(), image=image))

    return stages
