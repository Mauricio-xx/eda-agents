"""Open-source EDA bridge — orchestrates ngspice / xschem / KLayout / magic.

Tool-agnostic Python facade over the runners in ``eda_agents.core``.
Inspired by (but not copied from) the proprietary ``virtuoso-bridge-lite``
package; see ``docs/license_status.md`` for licensing notes — the upstream
repo has no LICENSE file, so every line here is reimplemented.

Public surface:

  - ``BridgeResult`` / ``SimulationResult`` / ``ExecutionStatus`` — Pydantic
    v2 result models with ``.ok`` and ``.save_json()``.
  - ``JobRegistry`` — UUID-keyed JSON registry with ThreadPoolExecutor for
    parallel simulation submit / poll / cancel.
  - ``SSHRunner`` / ``RemoteSshEnv`` / ``CommandResult`` — generic OpenSSH
    wrapper, jump-host capable, fully mockable for tests.
  - ``XschemRunner`` — headless ``xschem -n -s -q -x`` schematic-to-SPICE
    netlister.
  - ``KLayoutOps`` — thin facade that delegates DRC / LVS to the existing
    ``core.klayout_*`` runners.

The bridge does NOT reimplement DRC / LVS / PEX / synthesis — it wraps the
existing runners so the agent layer (and the CLI ``eda-bridge``) can drive
the full flow through one Python object.
"""

from __future__ import annotations

from eda_agents.bridge.jobs import JobRecord, JobRegistry, JobStatus
from eda_agents.bridge.klayout_ops import KLayoutOps
from eda_agents.bridge.models import (
    BridgeResult,
    ExecutionStatus,
    SimulationResult,
)
from eda_agents.bridge.ssh import CommandResult, RemoteSshEnv, SSHRunner
from eda_agents.bridge.xschem import XschemNetlistResult, XschemRunner

__all__ = [
    "BridgeResult",
    "CommandResult",
    "ExecutionStatus",
    "JobRecord",
    "JobRegistry",
    "JobStatus",
    "KLayoutOps",
    "RemoteSshEnv",
    "SSHRunner",
    "SimulationResult",
    "XschemNetlistResult",
    "XschemRunner",
]
