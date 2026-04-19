#!/usr/bin/env python3
"""Run LibreLane flow for FFT8 design (Turn 3)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from eda_agents.core.librelane_runner import LibreLaneRunner

project_dir = Path(__file__).parent
config_file = "config.yaml"
pdk_root = "/home/montanares/git/wafer-space-gf180mcu"
timeout_s = 1800

runner = LibreLaneRunner(
    project_dir=project_dir,
    config_file=config_file,
    pdk_root=pdk_root,
    timeout_s=timeout_s,
)

result = runner.run_flow()
print(f"Flow result: {result}")
if result.returncode != 0:
    print(f"Error: {result.error}")
    sys.exit(1)
