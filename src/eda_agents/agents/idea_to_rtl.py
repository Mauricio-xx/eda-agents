"""Library entry point for NL idea -> digital GDS via Claude Code CLI.

Consolidates the Mode 3 flow originally embedded in
``examples/09_rtl2gds_digital.py`` into a reusable async function plus
typed result dataclass so other call sites (the MCP ``generate_rtl_draft``
tool, bench adapters, future CLI wizards) share a single implementation.

Pipeline (simple / single-shot; Fase 0 of the S11 idea-to-chip arc):

1. ``build_from_spec_prompt(description, pdk, ...)`` crafts the full
   NL-to-GDS prompt (RTL authoring, testbench generation with
   gate-level-safe constraints, LibreLane config from template, flow
   invocation, signoff summary).
2. ``ClaudeCodeHarness.run()`` launches ``claude --print`` with the
   prompt on stdin. The agent writes RTL, testbench, config, runs
   LibreLane, and reports.
3. ``run_post_flow_gl_sim_check(...)`` replays the agent's testbench
   against the hardened run's post-synth and post-PnR netlists via
   :class:`GlSimRunner`. This is the verification floor — see
   ``feedback_full_verification`` in auto-memory.
4. A :class:`IdeaToRTLResult` summarises wall time, cost, gds path,
   run dir, GL sim verdict, and the agent's final text.

``complexity`` is accepted today but ignored in Fase 0; Fase 1 introduces
an iterative loop variant (``IdeaToRTLLoop``) that consumes it.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from eda_agents.agents.claude_code_harness import ClaudeCodeHarness
from eda_agents.agents.tool_defs import build_from_spec_prompt
from eda_agents.core.pdk import PdkConfig, get_pdk, resolve_pdk, resolve_pdk_root

logger = logging.getLogger(__name__)


Complexity = Literal["simple", "medium", "complex"]


@dataclass
class IdeaToRTLResult:
    """Outcome of one ``generate_rtl_draft`` invocation.

    The result is JSON-serialisable end-to-end via ``asdict`` so MCP
    clients and bench adapters can surface the same shape without ad-hoc
    conversions. ``gl_sim`` matches the dict shape produced by
    :func:`run_post_flow_gl_sim_check`.
    """

    success: bool
    work_dir: Path
    prompt_length: int = 0
    wall_time_s: float = 0.0
    num_turns: int = 0
    cost_usd: float = 0.0
    result_text: str = ""
    error: str | None = None
    # Artifact paths derived from the agent's output dir (best-effort).
    config_path: Path | None = None
    gds_path: Path | None = None
    run_dir: Path | None = None
    design_name: str | None = None
    # GL sim report (see run_post_flow_gl_sim_check). None when skipped.
    gl_sim: dict[str, Any] | None = None
    # Raw CLI JSON (useful for audit).
    raw_json: dict[str, Any] = field(default_factory=dict)

    @property
    def all_passed(self) -> bool:
        """Overall gate: success AND (gl_sim skipped OR gl_sim passed).

        Cases, in order:
          1. harness failure            -> False (always).
          2. gl_sim is None             -> True  (gl_sim explicitly
                                                  skipped via
                                                  ``skip_gl_sim=True``).
          3. gl_sim["skipped"] is True  -> True  (defensive: future
                                                  infra-skip cases or
                                                  user-driven skip
                                                  reported via the
                                                  helper rather than
                                                  the kwarg).
          4. gl_sim["all_passed"]       -> that boolean.
        """
        if not self.success:
            return False
        if self.gl_sim is None:
            return True
        if self.gl_sim.get("skipped"):
            return True
        return bool(self.gl_sim.get("all_passed", False))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_rtl_draft(
    description: str,
    design_name: str,
    work_dir: Path | str,
    *,
    pdk: str | PdkConfig = "gf180mcu",
    pdk_root: str | Path | None = None,
    librelane_python: str = "python3",
    complexity: Complexity = "simple",  # noqa: ARG001 — Fase 1 hook
    allow_dangerous: bool = False,
    cli_path: str = "claude",
    timeout_s: int = 3600,
    max_budget_usd: float | None = None,
    model: str | None = None,
    skip_gl_sim: bool = False,
    dry_run: bool = False,
    tb_framework: str = "iverilog",
) -> IdeaToRTLResult:
    """Generate RTL + testbench + config + GDS from a natural language spec.

    Parameters
    ----------
    description:
        Natural-language description of the desired digital block.
    design_name:
        Target module name; drives filenames and the LibreLane
        ``DESIGN_NAME``. The agent is told to name the top module
        accordingly in the prompt.
    work_dir:
        Directory where the agent writes ``src/``, ``tb/``, ``config.yaml``,
        and ``runs/``.
    pdk:
        Either a PDK name (``"gf180mcu"`` / ``"ihp_sg13g2"``) or a
        :class:`PdkConfig` instance. Drives the prompt template and
        PDK env vars.
    pdk_root:
        Explicit PDK root path. When ``None``, falls back to
        ``$PDK_ROOT`` (if it contains the requested PDK) or
        ``pdk.default_pdk_root``. Raises via the returned ``error``
        field when neither resolves to a real directory.
    librelane_python:
        Python interpreter that can run ``python3 -m librelane`` inside
        the prompt's Phase 4 command. Typically points at the LibreLane
        venv (``/home/montanares/git/librelane/.venv/bin/python``).
    complexity:
        Reserved hook for Fase 1 (``IdeaToRTLLoop``). Accepted today and
        recorded in the result, but single-shot is used regardless.
    allow_dangerous:
        First gate for ``--dangerously-skip-permissions``. Also requires
        ``EDA_AGENTS_ALLOW_DANGEROUS=1`` in the env.
    cli_path, timeout_s, max_budget_usd, model:
        Pass-throughs to :class:`ClaudeCodeHarness`.
    skip_gl_sim:
        When True, skip the post-flow GL sim check. Default is to run
        both post-synth and post-PnR GL sim and surface a non-success
        verdict as ``gl_sim.all_passed=False``.
    dry_run:
        Build the prompt + validate inputs without launching the CLI.
        Returns ``success=True`` when the prompt is well-formed so
        callers can sanity-check setup cheaply.
    tb_framework:
        Testbench flavour — ``"iverilog"`` (default, plain Verilog TB
        driven by iverilog/vvp) or ``"cocotb"`` (cocotb + Makefile,
        guided by the ``digital.cocotb_testbench`` skill). Same
        post-synth / post-PnR GlSimRunner check either way; cocotb
        just changes what the agent writes in Phase 2.5.
    """
    work_dir = Path(work_dir).resolve()
    pdk_config = resolve_pdk(pdk) if not isinstance(pdk, PdkConfig) else pdk

    # Resolve pdk_root eagerly so dry-run surfaces the same failure mode
    # the live run would hit.
    try:
        resolved_root = resolve_pdk_root(
            pdk_config, str(pdk_root) if pdk_root else None
        )
    except ValueError as exc:
        return IdeaToRTLResult(
            success=False,
            work_dir=work_dir,
            error=f"pdk_root resolution failed: {exc}",
            design_name=design_name,
        )

    prompt = build_from_spec_prompt(
        spec=_augment_description_with_design_name(description, design_name),
        work_dir=str(work_dir),
        pdk_root=resolved_root,
        pdk_config=pdk_config,
        librelane_python=librelane_python,
        tb_framework=tb_framework,
    )

    if dry_run:
        return IdeaToRTLResult(
            success=True,
            work_dir=work_dir,
            prompt_length=len(prompt),
            design_name=design_name,
            raw_json={"dry_run": True, "pdk_root": resolved_root},
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "src").mkdir(exist_ok=True)
    (work_dir / "tb").mkdir(exist_ok=True)

    harness = ClaudeCodeHarness(
        prompt=prompt,
        work_dir=work_dir,
        allow_dangerous=allow_dangerous,
        cli_path=cli_path,
        timeout_s=timeout_s,
        max_budget_usd=max_budget_usd,
        model=model,
    )

    t0 = time.monotonic()
    harness_result = await harness.run()
    elapsed = time.monotonic() - t0

    result = IdeaToRTLResult(
        success=harness_result.success,
        work_dir=work_dir,
        prompt_length=len(prompt),
        wall_time_s=elapsed,
        num_turns=harness_result.num_turns,
        cost_usd=harness_result.total_cost_usd,
        result_text=harness_result.result_text,
        error=harness_result.error,
        design_name=design_name,
        raw_json=harness_result.raw_json,
    )

    # Locate config + latest run + gds regardless of success so callers
    # can inspect partial artifacts on failure.
    _populate_artifact_paths(result)

    if harness_result.success and not skip_gl_sim:
        result.gl_sim = run_post_flow_gl_sim_check(
            work_dir=work_dir,
            pdk_key=pdk_config.name,
            pdk_root=resolved_root,
            librelane_python=librelane_python,
        )

    return result


def _augment_description_with_design_name(description: str, design_name: str) -> str:
    """Ensure the agent knows the expected top-module name.

    ``build_from_spec_prompt`` otherwise leaves the DESIGN_NAME
    placeholder (``<YOUR_DESIGN_NAME>``) for the agent to fill in —
    callers who already committed to a name shouldn't have to gamble.
    """
    if design_name and design_name not in description:
        return (
            f"{description}\n\n"
            f"Top module name MUST be '{design_name}' (filenames, "
            f"module declaration, DESIGN_NAME, and testbench DUT "
            f"instantiation must all use this exact identifier)."
        )
    return description


def _populate_artifact_paths(result: IdeaToRTLResult) -> None:
    """Best-effort: find config.yaml, runs/RUN_*, and final GDS."""
    config_path = result.work_dir / "config.yaml"
    if config_path.is_file():
        result.config_path = config_path
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
            name = data.get("DESIGN_NAME")
            if name:
                # Trust the config over the caller's hint if they diverge.
                result.design_name = str(name)
        except yaml.YAMLError:
            logger.debug("Could not parse config.yaml in %s", result.work_dir)

    runs_dir = result.work_dir / "runs"
    if runs_dir.is_dir():
        candidates = sorted(
            runs_dir.glob("RUN_*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            result.run_dir = candidates[0]
            if result.design_name:
                gds = (
                    result.run_dir
                    / "final"
                    / "gds"
                    / f"{result.design_name}.gds"
                )
                if gds.is_file():
                    result.gds_path = gds


# ---------------------------------------------------------------------------
# Post-flow gate-level simulation check
# ---------------------------------------------------------------------------


def run_post_flow_gl_sim_check(
    *,
    work_dir: Path,
    pdk_key: str,
    pdk_root: str,
    librelane_python: str | None = None,
) -> dict[str, Any]:
    """Run post-synth + post-PnR GL sim against the agent's artefacts.

    Reads ``{work_dir}/config.yaml`` for DESIGN_NAME, locates the most
    recent ``{work_dir}/runs/RUN_*/`` directory, reconstructs a minimal
    :class:`DigitalDesign` pointing at the agent's testbench, and
    invokes :class:`GlSimRunner` twice.

    Both iverilog (``tb/tb_<design>.v``) and cocotb (``tb/Makefile``
    + ``tb/test_<design>.py``) flavours are supported. Detection lives
    in :meth:`GlSimRunner._detect_tb_flavour` so this helper just
    forwards the work directory and lets the runner pick the right
    backend. The cocotb path needs ``librelane_python`` to find
    ``cocotb-config`` (cocotb 2.x is installed in the LibreLane venv,
    not in the eda-agents venv).

    Returns a dict with:

    * ``all_passed`` — both stages green.
    * ``run_dir`` — absolute path of the LibreLane run used.
    * ``post_synth`` / ``post_pnr`` — per-stage ``{success, error,
      run_time_s}``.

    Does **not** raise on missing artefacts; callers check
    ``all_passed`` / ``error``.
    """
    # Import heavy modules lazily so dry-runs of generate_rtl_draft
    # don't pay the import cost.
    from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec
    from eda_agents.core.stages.gl_sim_runner import GlSimRunner
    from eda_agents.core.tool_environment import LocalToolEnvironment

    try:
        pdk_config = get_pdk(pdk_key)
    except KeyError as exc:
        return {
            "all_passed": False,
            "error": f"unknown PDK {pdk_key!r}: {exc}",
        }

    config_path = work_dir / "config.yaml"
    if not config_path.is_file():
        return {
            "all_passed": False,
            "error": f"config.yaml not found at {config_path}",
        }
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        return {
            "all_passed": False,
            "error": f"config.yaml parse failed: {exc}",
        }
    design_name = cfg.get("DESIGN_NAME")
    if not design_name:
        return {
            "all_passed": False,
            "error": "DESIGN_NAME missing from config.yaml",
        }

    runs_root = work_dir / "runs"
    runs = sorted(runs_root.glob("RUN_*"), key=lambda p: p.stat().st_mtime)
    if not runs:
        return {
            "all_passed": False,
            "error": f"No LibreLane run directories under {runs_root}",
        }
    run_dir = runs[-1]

    tb_path = work_dir / "tb" / f"tb_{design_name}.v"
    cocotb_tb = work_dir / "tb" / f"test_{design_name}.py"
    cocotb_makefile = work_dir / "tb" / "Makefile"
    has_iverilog_tb = tb_path.is_file()
    has_cocotb_tb = cocotb_tb.is_file() and cocotb_makefile.is_file()
    if not has_iverilog_tb and not has_cocotb_tb:
        return {
            "all_passed": False,
            "error": (
                f"Testbench not found: looked for iverilog at "
                f"{tb_path} and cocotb at {cocotb_tb} + "
                f"{cocotb_makefile}"
            ),
        }

    # Build the testbench spec the runner will see. The cocotb GL-sim
    # path inside GlSimRunner uses its own filesystem detection, so
    # this spec is mostly informational — but we keep it accurate so
    # logs and downstream callers (autoresearch, manual re-runs) can
    # tell which flavour was active.
    if has_cocotb_tb:
        tb_spec = TestbenchSpec(
            driver="cocotb",
            target="make sim",
            work_dir_relative="tb",
        )
    else:
        tb_spec = TestbenchSpec(
            driver="iverilog",
            target=str(tb_path.relative_to(work_dir)),
        )

    class _AgentDesign(DigitalDesign):
        """Minimal DigitalDesign reconstructed from agent artefacts."""

        def project_name(self) -> str:
            return design_name

        def specification(self) -> str:
            return ""

        def design_space(self) -> dict:
            return {}

        def flow_config_overrides(self) -> dict:
            return {}

        def project_dir(self) -> Path:
            return work_dir

        def librelane_config(self) -> Path:
            return config_path

        def compute_fom(self, metrics) -> float:  # noqa: ARG002
            return 0.0

        def check_validity(self, metrics):  # noqa: ARG002
            return True, []

        def prompt_description(self) -> str:
            return ""

        def design_vars_description(self) -> str:
            return ""

        def specs_description(self) -> str:
            return ""

        def fom_description(self) -> str:
            return ""

        def reference_description(self) -> str:
            return ""

        def testbench(self):
            return tb_spec

    design = _AgentDesign()
    runner = GlSimRunner(
        design=design,
        env=LocalToolEnvironment(),
        run_dir=run_dir,
        pdk_config=pdk_config,
        pdk_root=pdk_root,
        design_name=design_name,
        librelane_python=librelane_python,
    )

    synth_res = runner.run_post_synth()
    pnr_res = runner.run_post_pnr()

    return {
        "all_passed": synth_res.success and pnr_res.success,
        "run_dir": str(run_dir),
        "post_synth": {
            "success": synth_res.success,
            "error": synth_res.error,
            "run_time_s": synth_res.run_time_s,
        },
        "post_pnr": {
            "success": pnr_res.success,
            "error": pnr_res.error,
            "run_time_s": pnr_res.run_time_s,
            "sdf_warnings": pnr_res.metrics_delta.get("gl_sim_sdf_warnings", 0),
        },
    }


def print_gl_sim_report(report: dict[str, Any]) -> None:
    """Human-friendly print of a ``run_post_flow_gl_sim_check`` result."""
    print("\n" + "=" * 60)
    print("Gate-level simulation gates")
    print("=" * 60)
    if "error" in report and "run_dir" not in report:
        print(f"  SKIPPED: {report['error']}")
        return
    ps = report["post_synth"]
    pp = report["post_pnr"]
    status = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    print(f"  Run dir:        {report['run_dir']}")
    print(f"  Post-synth:     {status(ps['success'])}  ({ps['run_time_s']:.1f}s)")
    if not ps["success"]:
        print(f"    error: {ps['error']}")
    print(f"  Post-PnR (SDF): {status(pp['success'])}  ({pp['run_time_s']:.1f}s)")
    if not pp["success"]:
        print(f"    error: {pp['error']}")
    print(f"  SDF warnings:   {pp['sdf_warnings']}")


# ---------------------------------------------------------------------------
# JSON serialisation (for MCP / bench adapters)
# ---------------------------------------------------------------------------


def result_to_dict(result: IdeaToRTLResult) -> dict[str, Any]:
    """Flatten an :class:`IdeaToRTLResult` to a JSON-serialisable dict.

    Paths are emitted as strings; ``None`` values are preserved. Used
    by the MCP tool and bench adapters to produce stable JSON.
    """
    def _maybe_str(p: Path | None) -> str | None:
        return str(p) if p is not None else None

    return {
        "success": result.success,
        "all_passed": result.all_passed,
        "work_dir": str(result.work_dir),
        "design_name": result.design_name,
        "prompt_length": result.prompt_length,
        "wall_time_s": result.wall_time_s,
        "num_turns": result.num_turns,
        "cost_usd": result.cost_usd,
        "result_text_tail": result.result_text[-2000:] if result.result_text else "",
        "error": result.error,
        "config_path": _maybe_str(result.config_path),
        "gds_path": _maybe_str(result.gds_path),
        "run_dir": _maybe_str(result.run_dir),
        "gl_sim": result.gl_sim,
    }


def write_result_json(result: IdeaToRTLResult, destination: Path | str) -> Path:
    """Persist an ``IdeaToRTLResult`` to JSON. Returns the destination path."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result_to_dict(result), indent=2, default=str)
    )
    return destination


__all__ = [
    "Complexity",
    "IdeaToRTLResult",
    "generate_rtl_draft",
    "print_gl_sim_report",
    "result_to_dict",
    "run_post_flow_gl_sim_check",
    "write_result_json",
]
