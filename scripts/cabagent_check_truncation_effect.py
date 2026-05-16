#!/usr/bin/env python3
"""Effect verification for the autoresearch prompt truncation helper.

Builds the user-prompt that AutoresearchRunner would send to the LLM,
first with truncation disabled (pre-change behaviour, simulated by
monkey-patching _truncate_for_prompt to identity) and then with it
enabled, on:

  (a) a clean history (no oversized fields), and
  (b) a history with a 50k-char fake traceback in one entry.

Prints prompt sizes so a human reviewer can see the bound holds.

Usage:
    .venv/bin/python scripts/cabagent_check_truncation_effect.py
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.agents import _autoresearch_core
from eda_agents.agents.autoresearch_runner import AutoresearchRunner
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.topology import CircuitTopology


class _NullTopology(CircuitTopology):
    """Minimal CircuitTopology stub so we can instantiate the runner."""

    def topology_name(self) -> str:
        return "stub"

    def design_space(self) -> dict[str, tuple[float, float]]:
        return {"x": (0.0, 1.0)}

    def params_to_sizing(self, params):
        return {}

    def generate_netlist(self, sizing, work_dir: Path):
        return work_dir / "ckt.cir"

    def compute_fom(self, spice_result: SpiceResult, sizing) -> float:
        return 0.0

    def check_validity(self, spice_result, sizing=None):
        return True, []

    def prompt_description(self) -> str:
        return "stub"

    def design_vars_description(self) -> str:
        return "- x: [0, 1]"

    def specs_description(self) -> str:
        return "always-valid"

    def fom_description(self) -> str:
        return "always-zero"

    def reference_description(self) -> str:
        return "x=0.5"


def _clean_history(n: int = 20) -> list[dict]:
    return [
        {
            "eval": i,
            "params": {"x": i * 0.05},
            "fom": float(i),
            "valid": True,
            "violations": [],
            "status": "kept",
            "kept": True,
        }
        for i in range(1, n + 1)
    ]


def _poisoned_history(n: int = 20) -> list[dict]:
    h = _clean_history(n)
    fake_traceback = (
        "Traceback (most recent call last):\n"
        + "  File \"loop.py\", line 42, in run\n    raise RuntimeError(\"sim blew up\")\n"
        + ("noise " * 10000)
    )
    h[5] = {
        "eval": 6,
        "params": {"x": 0.30},
        "fom": 0.0,
        "valid": False,
        "violations": [
            "Adc=42.0 < 50.0 dB",
            "GBW=0.5 < 1.0 MHz",
            "PM=45.0 < 60.0 deg",
        ] * 50,
        "status": "crash",
        "kept": False,
        "error": fake_traceback,
    }
    return h


def _build_prompt(history: list[dict]) -> str:
    runner = AutoresearchRunner(topology=_NullTopology(), budget=50)
    best = {
        "eval": 1,
        "params": {"x": 0.05},
        "fom": 1.0,
        "valid": True,
        "Adc_dB": 60.0,
        "GBW_Hz": 2.0e6,
        "PM_deg": 65.0,
    }
    return runner._build_proposal_prompt(history, best, eval_num=len(history) + 1)


def _measure(label: str, history: list[dict]) -> None:
    original = _autoresearch_core._truncate_for_prompt
    try:
        _autoresearch_core._truncate_for_prompt = lambda s, max_chars=2000, label="text": ("" if s is None else str(s))
        import eda_agents.agents.autoresearch_runner as ar
        ar._truncate_for_prompt = _autoresearch_core._truncate_for_prompt
        before = _build_prompt(history)
    finally:
        _autoresearch_core._truncate_for_prompt = original
        import eda_agents.agents.autoresearch_runner as ar
        ar._truncate_for_prompt = original

    after = _build_prompt(history)
    saved = len(before) - len(after)
    pct = (100.0 * saved / len(before)) if before else 0.0
    print(f"[{label}]")
    print(f"  pre-change prompt size:  {len(before):>10,d} chars")
    print(f"  post-change prompt size: {len(after):>10,d} chars")
    print(f"  saved:                   {saved:>10,d} chars ({pct:.1f}%)")
    print()


def main() -> None:
    print("Autoresearch prompt truncation effect check")
    print("=" * 56)
    print()
    _measure("clean history (no oversized fields)", _clean_history())
    _measure("history with 50k-char traceback in one entry", _poisoned_history())


if __name__ == "__main__":
    main()
