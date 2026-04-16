"""Analog 4-role DAG: Librarian / Architect / Designer / Verifier.

This is the open-source reimplementation of the analog-agents role
taxonomy that lives in ``~/git/arcadia-review/analog-agents`` (no
LICENSE -- not copied, only inspired). The harness here is harness-
agnostic: it accepts any executor implementing ``RoleExecutor`` so the
same DAG can drive a dry-run path (used in tests / examples), an ADK
multi-agent setup, or a Claude Code CLI loop.

Public surface::

    from eda_agents.agents.analog_roles import (
        AnalogRolesHarness,
        DryRunExecutor,
        Role,
        RoleResult,
        run_analog_roles,
    )
"""

from eda_agents.agents.analog_roles.roles import (
    Role,
    RoleExecutor,
    RoleResult,
)
from eda_agents.agents.analog_roles.harness import (
    AnalogRolesHarness,
    DryRunExecutor,
    HarnessOutput,
    run_analog_roles,
)

__all__ = [
    "AnalogRolesHarness",
    "DryRunExecutor",
    "HarnessOutput",
    "Role",
    "RoleExecutor",
    "RoleResult",
    "run_analog_roles",
]
