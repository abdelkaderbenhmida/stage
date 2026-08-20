"""A repository defines its own pipeline stages in `.platform.yml`.

The seven built-in stages are the platform's contract, not a description of
what any particular application needs doing. These cover the parsing rules
that decide whether a tenant's declared stages can be honoured, and the two
properties that must hold no matter what the file says: the file cannot turn
the security gate off, and a malformed file stops the deployment rather than
being silently ignored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from controlplane.core.pipeline_config import (
    MAX_STAGES,
    InvalidPipelineConfig,
    load_stages,
)


def _write(tmp_path: Path, content: str, name: str = ".platform.yml") -> Path:
    (tmp_path / name).write_text(content)
    return tmp_path


def test_a_repository_without_the_file_declares_no_stages(tmp_path):
    assert load_stages(tmp_path) == []


def test_stages_are_read_in_order(tmp_path):
    _write(tmp_path, """
stages:
  - name: unit tests
    run: pytest -q
  - name: lint
    run: ruff check .
""")
    stages = load_stages(tmp_path)

    assert [s.name for s in stages] == ["unit tests", "lint"]
    assert [s.run for s in stages] == ["pytest -q", "ruff check ."]


def test_the_yaml_extension_is_accepted_too(tmp_path):
    _write(tmp_path, "stages:\n  - name: t\n    run: echo ok\n", name=".platform.yaml")
    assert [s.name for s in load_stages(tmp_path)] == ["t"]


def test_a_stage_may_choose_its_own_image(tmp_path):
    """The default sandbox carries the platform's tooling, not the app's
    dependencies, so a test stage nearly always needs to say otherwise."""
    _write(tmp_path, """
stages:
  - name: unit tests
    image: python:3.11-slim
    run: pytest -q
""")
    stage = load_stages(tmp_path)[0]

    assert stage.image == "python:3.11-slim"


def test_a_stage_without_an_image_defers_to_the_platform_default(tmp_path):
    _write(tmp_path, "stages:\n  - name: t\n    run: echo ok\n")
    assert load_stages(tmp_path)[0].image == ""


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("stages:\n  - name: x\n   run: [\n", "not valid YAML"),
        ("stages: nope\n", "must be a list"),
        ("- just\n- a\n- list\n", "must be a mapping"),
        ("stages:\n  - run: echo hi\n", "missing 'name'"),
        ("stages:\n  - name: x\n", "missing 'run'"),
        ("stages:\n  - name: x\n    run: ''\n", "missing 'run'"),
        ("stages:\n  - name: t\n    run: a\n  - name: T\n    run: b\n", "duplicate stage name"),
        ("stages:\n  - name: '  '\n    run: a\n", "missing 'name'"),
    ],
)
def test_a_malformed_file_is_rejected_with_a_reason(tmp_path, content, expected):
    """Fail closed and say why: silently ignoring the file would run a
    pipeline its author did not ask for."""
    _write(tmp_path, content)

    with pytest.raises(InvalidPipelineConfig, match=expected):
        load_stages(tmp_path)


def test_an_unreasonable_number_of_stages_is_rejected(tmp_path):
    _write(tmp_path, "stages:\n" + "".join(
        f"  - name: s{i}\n    run: echo ok\n" for i in range(MAX_STAGES + 1)
    ))

    with pytest.raises(InvalidPipelineConfig, match="at most"):
        load_stages(tmp_path)


@pytest.mark.parametrize(
    "image",
    [
        "evil --privileged",          # would become an extra docker run argument
        "img -v /:/host",
        "img\nmalicious",
        "-rm",
    ],
)
def test_an_image_that_could_smuggle_arguments_is_rejected(tmp_path, image):
    """The image string is handed to `docker run`, so anything that could be
    read as another argument has no business being in it."""
    _write(tmp_path, f'stages:\n  - name: t\n    image: "{image}"\n    run: echo ok\n')

    with pytest.raises(InvalidPipelineConfig, match="not a valid image reference"):
        load_stages(tmp_path)


def test_the_file_cannot_switch_the_security_gate_off(tmp_path):
    """There is no setting for this on purpose: a tenant who could disable
    the scan gate could ship anything, which is the one thing this platform
    exists to prevent. Keys it does not understand are simply not honoured."""
    _write(tmp_path, """
block_on: []
scan: false
skip_trivy: true
stages:
  - name: t
    run: echo ok
""")
    stages = load_stages(tmp_path)

    assert [s.name for s in stages] == ["t"]
    assert not hasattr(stages[0], "block_on")
