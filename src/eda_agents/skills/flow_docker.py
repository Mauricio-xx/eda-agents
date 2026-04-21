"""Flow skills that run inside the IIC-OSIC-TOOLS Docker container.

These skills teach agents how to drive `hpretl/iic-osic-tools` for
GF180MCU signoff work — RTL-to-GDS via LibreLane, and analog DRC/LVS
via KLayout + Magic/Netgen. The bodies are authored as markdown under
``skills/_bundles/gf180_docker/`` and concatenated here via the
existing :func:`_load_markdown_bundle` helper.
"""

from __future__ import annotations

from eda_agents.skills.analog import _load_markdown_bundle
from eda_agents.skills.base import Skill
from eda_agents.skills.registry import register_skill


def _rtl2gds_gf180_docker_prompt() -> str:
    return _load_markdown_bundle("gf180_docker", ["common", "rtl2gds"])


def _analog_signoff_gf180_docker_prompt() -> str:
    return _load_markdown_bundle("gf180_docker", ["common", "analog_signoff"])


register_skill(
    Skill(
        name="flow.rtl2gds_gf180_docker",
        description=(
            "Guide an agent end-to-end through RTL-to-GDS on GF180MCU using "
            "the hpretl/iic-osic-tools Docker container. Covers image setup, "
            "wafer-space template, LibreLane invocation, signoff artefacts, "
            "and the six known gotchas. Signature: ()."
        ),
        prompt_fn=_rtl2gds_gf180_docker_prompt,
    )
)


register_skill(
    Skill(
        name="flow.analog_signoff_gf180_docker",
        description=(
            "Run KLayout DRC and Magic+Netgen LVS on a GF180 analog layout "
            "inside the hpretl/iic-osic-tools container. Composes with "
            "flow.drc_checker / flow.drc_fixer for violation triage. "
            "Signature: ()."
        ),
        prompt_fn=_analog_signoff_gf180_docker_prompt,
    )
)
