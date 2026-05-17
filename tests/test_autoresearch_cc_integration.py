"""Marker-gated integration test for the Claude Code CLI backend.

Spawns the real ``claude --print`` subprocess to verify the
``ClaudeCodeHarness`` wiring inside :class:`AutoresearchRunner` end-to-end.
SPICE is faked (the runner stays oblivious) so the test exercises only
the proposal subprocess, the PROPOSAL block extraction, and the cost
telemetry round-trip.

Gated by:
  - ``pytest.mark.cc_cli`` — declared in ``pyproject.toml``.
  - ``EDA_AGENTS_RUN_CC_TESTS=1`` env var (off by default in CI).
  - ``claude`` binary present on ``PATH``.

Run locally with::

    EDA_AGENTS_RUN_CC_TESTS=1 .venv/bin/pytest -m cc_cli -v
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eda_agents.agents.autoresearch_runner import AutoresearchRunner
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.topology import CircuitTopology


_CC_AVAILABLE = shutil.which("claude") is not None
_CC_TESTS_ENABLED = os.environ.get("EDA_AGENTS_RUN_CC_TESTS") == "1"


pytestmark = [
    pytest.mark.cc_cli,
    pytest.mark.skipif(
        not _CC_AVAILABLE,
        reason="Claude Code CLI not found on PATH",
    ),
    pytest.mark.skipif(
        not _CC_TESTS_ENABLED,
        reason="set EDA_AGENTS_RUN_CC_TESTS=1 to opt into real CC subprocess tests",
    ),
]


class _Stub(CircuitTopology):
    """Minimal CircuitTopology so the runner can build prompts cheaply."""

    def topology_name(self):
        return "stub_for_cc_integration"

    def design_space(self):
        return {"x": (0.0, 10.0), "y": (1.0, 5.0)}

    def default_params(self):
        return {"x": 5.0, "y": 2.5}

    def params_to_sizing(self, params):
        return params

    def generate_netlist(self, sizing, work_dir):
        return work_dir

    def compute_fom(self, spice_result, sizing):
        return 1.0

    def check_validity(self, spice_result, sizing=None):
        return True, []

    def prompt_description(self):
        return "Stub topology for CC backend integration testing."

    def design_vars_description(self):
        return "- x: [0, 10] dimensionless\n- y: [1, 5] dimensionless"

    def specs_description(self):
        return "Any combination is valid (test fixture)."

    def fom_description(self):
        return "Constant FoM=1.0; the loop just verifies wiring."

    def reference_description(self):
        return "x=5, y=2.5."


@pytest.mark.anyio
async def test_cc_backend_smoke_with_stub_topology(tmp_path):
    """Two-eval Miller-free smoke through the real CC CLI.

    Exercises the full path: dispatcher -> _propose_via_cc_cli ->
    real ``claude --print`` subprocess -> HarnessResult -> PROPOSAL
    extraction -> design-space clamp -> SPICE (faked) -> persist.
    """
    runner = AutoresearchRunner(
        topology=_Stub(),
        model="claude-sonnet-4-6",
        budget=2,
        backend="cc_cli",
    )

    good_spice = SpiceResult(
        success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
    )

    with patch("eda_agents.core.spice_runner.SpiceRunner") as spice_cls:
        spice_inst = MagicMock()
        spice_inst.run_async = AsyncMock(return_value=good_spice)
        spice_cls.return_value = spice_inst
        result = await runner.run(tmp_path)

    assert result.total_evals == 2
    assert result.cost_usd is not None
    assert result.cost_usd > 0.0, (
        f"cost_usd should be positive after real CC calls, got {result.cost_usd!r}"
    )
    assert Path(result.tsv_path).is_file()
    assert (tmp_path / "program.md").is_file()
