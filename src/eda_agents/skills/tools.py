"""Tool-spec skills: static OpenAI/ADK function-calling dicts.

These wrap the legacy constants exported by
``eda_agents.agents.tool_defs`` (``SIMULATE_TOOL_SPEC``,
``GMID_LOOKUP_TOOL_SPEC``, ``EVALUATE_TOOL_SPEC``). For dynamic
topology-driven specs use ``CircuitTopology.tool_spec()`` directly.
"""

from __future__ import annotations

from eda_agents.agents.tool_defs import (
    EVALUATE_TOOL_SPEC,
    GMID_LOOKUP_TOOL_SPEC,
    SIMULATE_TOOL_SPEC,
)
from eda_agents.skills.base import Skill
from eda_agents.skills.registry import register_skill

register_skill(
    Skill(
        name="tools.simulate_miller_ota",
        description=(
            "Legacy static tool spec for the Miller OTA ngspice simulator "
            "(IHP SG13G2). Prefer topology.tool_spec() for new topologies."
        ),
        tool_spec=SIMULATE_TOOL_SPEC,
    )
)

register_skill(
    Skill(
        name="tools.gmid_lookup",
        description=(
            "Legacy static tool spec for the IHP SG13G2 gm/ID lookup table "
            "(ngspice PSP103 sweeps)."
        ),
        tool_spec=GMID_LOOKUP_TOOL_SPEC,
    )
)

register_skill(
    Skill(
        name="tools.evaluate_miller_ota",
        description=(
            "Legacy static tool spec for the Miller OTA evaluator (gm/ID "
            "parameterization) on IHP SG13G2."
        ),
        tool_spec=EVALUATE_TOOL_SPEC,
    )
)
