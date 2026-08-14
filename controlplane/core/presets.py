"""Environment size presets (docs/TODO.md Task 2.1).

A developer spinning up an environment to test a branch does not want to size
nodes individually — that is an operator concern. Presets expand server-side
into an ordinary ``InfraSpec`` which then goes through the *same* validator as
a hand-written one: the preset is a convenience, never a trust boundary.
"""

from dataclasses import dataclass
from typing import Literal

PresetName = Literal["small", "medium", "large"]


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    node_count: int
    vcpu: int
    memory_mb: int
    disk_gb: int

    def nodes(self) -> list[dict]:
        """Expand into node dicts.

        The first node is the control-plane node; a single-node preset runs
        everything on the master, which is why it is not given any workers.
        """
        nodes = [
            {
                "name": "master",
                "vcpu": self.vcpu,
                "memory_mb": self.memory_mb,
                "disk_gb": self.disk_gb,
                "role": "k8s_master",
            }
        ]
        for index in range(1, self.node_count):
            nodes.append(
                {
                    "name": f"worker-{index}",
                    "vcpu": self.vcpu,
                    "memory_mb": self.memory_mb,
                    "disk_gb": self.disk_gb,
                    "role": "k8s_worker",
                }
            )
        return nodes


PRESETS: dict[str, Preset] = {
    "small": Preset(
        name="small",
        description="Single node — one service under test.",
        node_count=1,
        vcpu=2,
        memory_mb=4096,
        disk_gb=30,
    ),
    "medium": Preset(
        name="medium",
        description="Two nodes — a handful of services.",
        node_count=2,
        vcpu=4,
        memory_mb=8192,
        disk_gb=40,
    ),
    "large": Preset(
        name="large",
        description="Three nodes — a full stack.",
        node_count=3,
        vcpu=4,
        memory_mb=8192,
        disk_gb=50,
    ),
}


def expand_preset(preset: str, project_name: str, network: dict | None = None) -> dict:
    """Build a complete InfraSpec document from a preset name.

    The result is returned as a plain dict so the caller feeds it through
    ``InfraSpec.model_validate`` exactly like user-supplied input.
    """
    if preset not in PRESETS:
        raise ValueError(
            f"Unknown preset {preset!r}. Available presets: {', '.join(sorted(PRESETS))}."
        )
    definition = PRESETS[preset]
    return {
        "version": 1,
        "project": project_name,
        "network": network or {"cidr": "192.168.56.0/24", "domain": "devops.local"},
        "nodes": definition.nodes(),
    }
