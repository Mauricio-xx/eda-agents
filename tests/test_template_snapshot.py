"""Byte-level snapshot tests for LibreLane templates.

The two ``.yaml.tmpl`` files under ``src/eda_agents/agents/templates/``
are infrastructure. Any accidental content drift (stray whitespace,
reordered keys, placeholder rename) should fail CI. These tests fill
each template with a fixed parameter set and compare the result against
a golden file in ``tests/snapshots/``.

To regenerate after an intentional change: ``REGEN_SNAPSHOTS=1 pytest
tests/test_template_snapshot.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agents.agents.librelane_config_templates import get_config_template
from eda_agents.core.pdk import resolve_pdk

SNAPSHOT_PARAMS: dict = {
    "design_name": "counter_4bit",
    "verilog_file": "rtl/counter_4bit.v",
    "clock_port": "clk",
    "clock_period": 10,
    "die_width": 300.0,
    "die_height": 300.0,
}

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

CASES = [
    ("gf180mcu", "gf180.filled.yaml"),
    ("ihp_sg13g2", "ihp_sg13g2.filled.yaml"),
]


@pytest.mark.parametrize("pdk_key, snapshot_name", CASES)
def test_template_matches_snapshot(pdk_key: str, snapshot_name: str) -> None:
    pdk = resolve_pdk(pdk_key)
    tpl, _ = get_config_template(pdk)
    filled = tpl.format(**SNAPSHOT_PARAMS)
    path = SNAPSHOT_DIR / snapshot_name

    if os.environ.get("REGEN_SNAPSHOTS") == "1":
        path.write_text(filled, encoding="utf-8")
        pytest.skip(f"regenerated {snapshot_name}")

    assert path.exists(), f"missing snapshot file {path}"
    golden = path.read_text(encoding="utf-8")
    assert filled == golden, (
        f"template drift for {pdk_key}; rerun with REGEN_SNAPSHOTS=1 if "
        f"the change is intentional"
    )
