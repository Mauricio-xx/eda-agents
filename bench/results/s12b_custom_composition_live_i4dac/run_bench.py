"""Live bench: run AnalogCompositionLoop against the 4-bit current-steering DAC.

Target:
    4-bit binary-weighted current-steering DAC, 1 uA LSB,
    differential output, IHP SG13G2 1.2 V supply.

Run:
    cd /path/to/eda-agents
    .venv/bin/python bench/results/s12b_custom_composition_live_i4dac/run_bench.py

Needs OPENROUTER_API_KEY in env (or .env). Budget: 8 iterations,
$10 ceiling. Gemini Flash model — expected spend < $2.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Hot-loading eda-agents if not installed in current sys.path (support
# running directly against the worktree without pip install -e).
WORKTREE = Path(__file__).resolve().parents[3]
if str(WORKTREE / "src") not in sys.path:
    sys.path.insert(0, str(WORKTREE / "src"))

from eda_agents.agents.analog_composition_loop import AnalogCompositionLoop  # noqa: E402


HERE = Path(__file__).resolve().parent
WORK_DIR = HERE / "loop_state"


def _load_env_key() -> None:
    """Load OPENROUTER_API_KEY from main repo .env if not already set."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    env_path = Path("/home/montanares/personal_exp/eda-agents/.env")
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "OPENROUTER_API_KEY":
            os.environ["OPENROUTER_API_KEY"] = v.strip().strip("'\"")
            return


def main() -> None:
    _load_env_key()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set — refusing to run live bench")
        sys.exit(1)

    WORK_DIR.mkdir(exist_ok=True)

    loop = AnalogCompositionLoop(
        pdk="ihp_sg13g2",
        work_dir=WORK_DIR,
        model="google/gemini-2.5-flash",
        max_iterations=8,
        max_budget_usd=10.0,
        attempt_layout=True,
        attempt_drc_lvs=False,  # top-level placer is future work
    )

    nl = (
        "A 4-bit binary-weighted current-steering DAC on IHP SG13G2 1.2 V. "
        "Four NMOS current sources sized as 1x, 2x, 4x, 8x unit currents "
        "(LSB = 1 uA, MSB = 8 uA). Each source steered to either the "
        "positive (IOP) or negative (ION) output leg by a pair of NMOS "
        "differential switches whose gates are the 4-bit thermometer / "
        "binary control inputs B0..B3. The outputs sum on a pair of "
        "resistors (or sense nodes) to produce a differential analog "
        "current. Target: INL < 0.5 LSB, DNL < 0.5 LSB, static, op point."
    )
    constraints = {
        "supply_v": 1.2,
        "lsb_current_uA": 1.0,
        "n_bits": 4,
        "inl_lsb_max": 0.5,
        "dnl_lsb_max": 0.5,
    }

    t0 = time.time()
    result = loop.loop(nl, constraints=constraints)
    elapsed = time.time() - t0

    summary = {
        "nl_description": nl,
        "constraints": constraints,
        "converged": result.converged,
        "honest_fail_reason": result.honest_fail_reason,
        "iterations_run": len(result.iterations),
        "total_tokens": result.total_tokens,
        "total_cost_usd": result.total_cost_usd,
        "total_time_s": round(elapsed, 2),
        "final_composition_keys": list((result.final_composition or {}).keys()),
        "final_sizing_keys": list((result.final_sizing or {}).keys()),
        "final_spice_pass_per_spec": (
            (result.final_spice or {}).get("pass_per_spec")
        ),
        "gds_paths": result.gds_paths,
        "last_iteration_verdict": (
            result.iterations[-1].critique.get("verdict")
            if result.iterations and result.iterations[-1].critique
            else None
        ),
    }
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
