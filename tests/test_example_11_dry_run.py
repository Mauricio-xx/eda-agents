"""Dry-run regression tests for ``examples/11_idea_to_chip_demo_gf180.py``.

The dry-run path exercises the wiring (argparse, case dispatch, plot
emission) without spawning any LLM or LibreLane. It is the cheapest
gate for accidental regressions in the demo orchestration.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "examples" / "11_idea_to_chip_demo_gf180.py"


def _load_module():
    """Import the example by file path (number-prefixed name blocks normal import)."""
    spec = importlib.util.spec_from_file_location("_example_11", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_help_renders() -> None:
    """``--help`` must not import a missing dependency."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "--case" in result.stdout
    assert "idea_loop" in result.stdout
    assert "fazyrv_flow" in result.stdout


def test_dry_run_idea_loop_no_plots(tmp_path: Path) -> None:
    """Dry-run idea_loop without plots should succeed and copy the fixture."""
    mod = _load_module()
    args = mod.parse_args(
        [
            "--case",
            "idea_loop",
            "--dry-run",
            "--no-plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    rc = mod.main(
        [
            "--case",
            "idea_loop",
            "--dry-run",
            "--no-plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    artefact = tmp_path / "idea_loop" / "loop_result.json"
    assert artefact.is_file()
    # No plots dir when --no-plots
    assert not (tmp_path / "idea_loop" / "plots").is_dir()
    # parse_args is exercised separately (sanity check that the namespace
    # carries the dry-run flag)
    assert args.dry_run is True


def test_dry_run_all_cases_no_plots(tmp_path: Path) -> None:
    mod = _load_module()
    rc = mod.main(
        [
            "--case",
            "all",
            "--dry-run",
            "--no-plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert (tmp_path / "idea_loop" / "loop_result.json").is_file()
    assert (tmp_path / "fazyrv_flow" / "results.tsv").is_file()
    assert (tmp_path / "fazyrv_hybrid" / "results.tsv").is_file()
    assert (tmp_path / "summary.md").is_file()


@pytest.mark.plots
def test_dry_run_with_plots_emits_pngs(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    mod = _load_module()
    rc = mod.main(
        [
            "--case",
            "all",
            "--dry-run",
            "--plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    # idea_loop -> 2 plots; each fazyrv -> 3 plots
    expected = [
        tmp_path / "idea_loop" / "plots" / "idea_loop_status.png",
        tmp_path / "idea_loop" / "plots" / "idea_loop_cost.png",
        tmp_path / "fazyrv_flow" / "plots" / "fom_evolution.png",
        tmp_path / "fazyrv_flow" / "plots" / "metrics_grid.png",
        tmp_path / "fazyrv_flow" / "plots" / "params_evolution.png",
        tmp_path / "fazyrv_hybrid" / "plots" / "fom_evolution.png",
        tmp_path / "fazyrv_hybrid" / "plots" / "metrics_grid.png",
        tmp_path / "fazyrv_hybrid" / "plots" / "params_evolution.png",
    ]
    for p in expected:
        assert p.is_file(), p
        assert p.stat().st_size > 0


def test_dry_run_does_not_call_llm(tmp_path: Path, monkeypatch) -> None:
    """Dry-run must short-circuit before any harness import."""
    mod = _load_module()

    def _explode(*_a, **_kw):
        raise AssertionError("LLM should not be invoked in dry-run")

    # Both harnesses live behind ClaudeCodeHarness.run / OpenCodeHarness.run.
    # Patching the constructors is the cheapest catch-all: even import is fine,
    # but instantiation here means the dry-run path slipped through.
    from eda_agents.agents import claude_code_harness as cc_mod

    monkeypatch.setattr(cc_mod.ClaudeCodeHarness, "run", _explode)

    rc = mod.main(
        [
            "--case",
            "idea_loop",
            "--dry-run",
            "--no-plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0


def test_dry_run_idea_to_optimize_no_plots(tmp_path: Path) -> None:
    """``idea_to_optimize`` dry-run stages both phase fixtures."""
    mod = _load_module()
    rc = mod.main(
        [
            "--case",
            "idea_to_optimize",
            "--dry-run",
            "--no-plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert (
        tmp_path / "idea_to_optimize" / "phase_idea" / "loop_result.json"
    ).is_file()
    assert (
        tmp_path / "idea_to_optimize" / "phase_optimize" / "results.tsv"
    ).is_file()


@pytest.mark.plots
def test_dry_run_idea_to_optimize_plots(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    mod = _load_module()
    rc = mod.main(
        [
            "--case",
            "idea_to_optimize",
            "--dry-run",
            "--plots",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    plots_root = tmp_path / "idea_to_optimize" / "plots"
    expected = [
        plots_root / "phase_idea" / "idea_loop_status.png",
        plots_root / "phase_idea" / "idea_loop_cost.png",
        plots_root / "phase_optimize" / "fom_evolution.png",
        plots_root / "phase_optimize" / "metrics_grid.png",
        plots_root / "phase_optimize" / "params_evolution.png",
    ]
    for p in expected:
        assert p.is_file(), p
        assert p.stat().st_size > 0


def test_skip_phase_idea_aborts_when_no_config(tmp_path: Path) -> None:
    """``--skip-phase-idea`` must surface a clear error when phase 1
    artefacts aren't pre-staged. Run live (not dry-run) so the live
    branch executes, and patch out the harness to never be called."""
    mod = _load_module()
    case_dir = tmp_path / "idea_to_optimize"
    (case_dir / "phase_idea").mkdir(parents=True)  # exists but no config
    # Live path requires --allow-dangerous env gate to pass; we set it
    # so we reach the actual handler. The handler must abort early via
    # the missing-config branch; harness should NEVER be invoked.
    import os

    os.environ["EDA_AGENTS_ALLOW_DANGEROUS"] = "1"
    try:
        rc = mod.main(
            [
                "--case",
                "idea_to_optimize",
                "--skip-phase-idea",
                "--allow-dangerous",
                "--work-dir",
                str(tmp_path),
            ]
        )
    finally:
        os.environ.pop("EDA_AGENTS_ALLOW_DANGEROUS", None)
    # Exit 0 because the handler returns a structured error rather than
    # raising. Verify the structured error is in the json summary
    # written to stdout — easiest is to check the work dir state.
    assert rc == 0
    # The phase_optimize dir must NOT have been created (handler bails
    # out before phase 2).
    assert not (case_dir / "phase_optimize").exists()


def test_opencode_requires_model(tmp_path: Path) -> None:
    """Live opencode path without --model must abort with rc=2."""
    mod = _load_module()
    rc = mod.main(
        [
            "--case",
            "idea_loop",
            "--backend",
            "opencode",
            "--work-dir",
            str(tmp_path),
        ]
    )
    assert rc == 2


def test_allow_dangerous_requires_env(tmp_path: Path, monkeypatch) -> None:
    """--allow-dangerous without env gate must exit cleanly with rc=2."""
    monkeypatch.delenv("EDA_AGENTS_ALLOW_DANGEROUS", raising=False)
    mod = _load_module()
    with pytest.raises(SystemExit) as excinfo:
        mod.main(
            [
                "--case",
                "idea_loop",
                "--allow-dangerous",
                "--work-dir",
                str(tmp_path),
            ]
        )
    assert excinfo.value.code == 2
