"""Unit tests for the S12-A IdeaToRTLLoop wrapper.

Mirrors the harness-mocking pattern in
``tests/test_idea_to_rtl.py::TestGenerateRtlDraftLivePaths`` so the
loop's branching logic (early success, budget exhausted, cost cap,
critique propagation) can be covered without touching the real
Claude CLI.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from eda_agents.agents.idea_to_rtl import IdeaToRTLResult, generate_rtl_draft
from eda_agents.agents.idea_to_rtl_loop import (
    IdeaToRTLLoopResult,
    LoopIteration,
    _build_critique_description,
    _classify_gl_sim,
    _classify_sim,
    _extract_failure_excerpt,
    _is_infra_error,
    run_idea_to_rtl_loop,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_idea_result(
    *,
    work_dir: Path,
    success: bool = True,
    all_passed: bool = True,
    cost_usd: float = 0.5,
    result_text: str = "RTL SIM PASS\nLibreLane signoff clean\nDONE",
    error: str | None = None,
    gl_sim_passed: bool = True,
    config_present: bool = True,
) -> IdeaToRTLResult:
    """Build an IdeaToRTLResult shaped like a real per-turn outcome."""
    if config_present:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "config.yaml").write_text(
            yaml.safe_dump({"DESIGN_NAME": "widget"})
        )
    gl_sim: dict | None
    if not success:
        gl_sim = None
    elif gl_sim_passed:
        gl_sim = {
            "all_passed": True,
            "post_synth": {"success": True, "error": None, "run_time_s": 1.0},
            "post_pnr": {"success": True, "error": None, "run_time_s": 1.0},
        }
    else:
        gl_sim = {
            "all_passed": False,
            "post_synth": {
                "success": False,
                "error": "Simulation reported FAIL/ERROR/ASSERT",
                "run_time_s": 1.0,
            },
            "post_pnr": {
                "success": False,
                "error": "Simulation reported FAIL/ERROR/ASSERT",
                "run_time_s": 1.0,
            },
        }

    # all_passed is a property derived from success + gl_sim — we
    # intentionally don't pass it; just construct the result fields
    # so the property returns the value the test wants.
    r = IdeaToRTLResult(
        success=success,
        work_dir=work_dir,
        cost_usd=cost_usd,
        result_text=result_text,
        error=error,
        gl_sim=gl_sim,
        design_name="widget",
        num_turns=2,
    )
    # Sanity: caller's all_passed expectation matches what the
    # property returns. Reveals fixture mistakes early.
    if r.all_passed is not all_passed:
        raise RuntimeError(
            f"_make_idea_result: caller asked for all_passed={all_passed} "
            f"but property derived {r.all_passed}. Fix fixture."
        )
    return r


def _patch_generate(monkeypatch, results: list[IdeaToRTLResult]):
    """Replace generate_rtl_draft in the loop module with a deterministic
    iterator over canned IdeaToRTLResult instances.

    Each loop turn pops one result from the front. Test asserts on
    how many are consumed.
    """
    from eda_agents.agents import idea_to_rtl_loop as loop_mod

    queue = list(results)
    calls: list[dict] = []

    async def _fake_generate(**kwargs):
        calls.append(kwargs)
        if not queue:
            raise AssertionError(
                "loop called generate_rtl_draft more times than the test "
                "queued IdeaToRTLResults — runaway loop?"
            )
        return queue.pop(0)

    monkeypatch.setattr(loop_mod, "generate_rtl_draft", _fake_generate)
    return calls


# ---------------------------------------------------------------------------
# Loop dispatch + single-shot byte-equivalence
# ---------------------------------------------------------------------------


async def test_max_turns_one_invokes_generate_once(tmp_path, monkeypatch):
    work = tmp_path / "work"
    fake = _make_idea_result(work_dir=work, success=True, all_passed=True)
    calls = _patch_generate(monkeypatch, [fake])

    out = await run_idea_to_rtl_loop(
        description="counter",
        design_name="widget",
        work_dir=work,
        max_turns=1,
        pdk_root="/tmp/fake_pdk",
    )
    assert len(calls) == 1
    assert out.converged_turn == 1
    assert out.reason == "converged"
    assert out.idea_result is fake
    # The loop attaches itself to the final result for diagnostic
    # propagation through the result_to_dict() serialisation.
    assert fake.loop_result is out


async def test_loop_budget_one_via_generate_rtl_draft_is_byte_equivalent(
    tmp_path, monkeypatch
):
    """When loop_budget=1, generate_rtl_draft must NOT dispatch to the loop.

    This is the byte-equivalence guarantee for S11 evidence: every
    pre-S12-A consumer keeps working unchanged.
    """
    from eda_agents.agents import idea_to_rtl_loop as loop_mod

    sentinel = AsyncMock(side_effect=AssertionError(
        "loop must NOT be invoked for loop_budget=1"
    ))
    monkeypatch.setattr(loop_mod, "run_idea_to_rtl_loop", sentinel)

    # Patch the inner harness so generate_rtl_draft can complete
    # without launching the CLI.
    from eda_agents.agents import idea_to_rtl as mod
    from eda_agents.agents.claude_code_harness import HarnessResult

    class _FakeHarness:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            return HarnessResult(success=True, total_cost_usd=0.0)

    monkeypatch.setattr(mod, "ClaudeCodeHarness", _FakeHarness)

    result = await generate_rtl_draft(
        description="x",
        design_name="widget",
        work_dir=tmp_path / "work",
        pdk="gf180mcu",
        pdk_root="/tmp/fake_pdk",
        skip_gl_sim=True,
        loop_budget=1,
    )
    sentinel.assert_not_called()
    assert result.success is True
    assert result.loop_result is None  # no loop ran


# ---------------------------------------------------------------------------
# Convergence + budget exhaustion + cost cap
# ---------------------------------------------------------------------------


async def test_early_success_short_circuits_loop(tmp_path, monkeypatch):
    work = tmp_path / "work"
    success = _make_idea_result(work_dir=work, success=True, all_passed=True)
    # Queue a second result that should never be consumed.
    extra = _make_idea_result(work_dir=work, success=True, all_passed=True)
    calls = _patch_generate(monkeypatch, [success, extra])

    out = await run_idea_to_rtl_loop(
        description="x",
        design_name="widget",
        work_dir=work,
        max_turns=8,
        pdk_root="/tmp/fake_pdk",
    )
    assert len(calls) == 1, "loop kept going after convergence"
    assert out.converged_turn == 1
    assert out.reason == "converged"
    assert out.budget_exhausted is False
    assert len(out.iterations) == 1


async def test_budget_exhausted_honest_fail(tmp_path, monkeypatch):
    work = tmp_path / "work"
    failures = [
        _make_idea_result(
            work_dir=work,
            success=True,
            all_passed=False,
            gl_sim_passed=False,
            cost_usd=0.5,
            result_text=f"turn {i}: still broken\nRTL SIM FAIL",
        )
        for i in range(1, 4)
    ]
    calls = _patch_generate(monkeypatch, failures)

    out = await run_idea_to_rtl_loop(
        description="x",
        design_name="widget",
        work_dir=work,
        max_turns=3,
        pdk_root="/tmp/fake_pdk",
    )
    assert len(calls) == 3
    assert out.converged_turn is None
    assert out.budget_exhausted is True
    assert out.reason == "budget_exhausted"
    assert len(out.iterations) == 3
    assert out.total_cost_usd == pytest.approx(1.5)
    # Honest-fail diagnostics persist on disk so the bench report can
    # find them without running the loop again.
    persisted = work / "loop_result.json"
    assert persisted.is_file()
    import json
    data = json.loads(persisted.read_text())
    assert data["budget_exhausted"] is True
    assert len(data["iterations"]) == 3


async def test_cost_cap_stops_before_max_turns(tmp_path, monkeypatch):
    work = tmp_path / "work"
    failures = [
        _make_idea_result(
            work_dir=work,
            success=True,
            all_passed=False,
            gl_sim_passed=False,
            cost_usd=2.0,  # 2 turns -> $4 -> hits $3 cap mid-way
            result_text=f"turn {i}: timing fail\nRTL SIM FAIL",
        )
        for i in range(1, 5)
    ]
    calls = _patch_generate(monkeypatch, failures)

    out = await run_idea_to_rtl_loop(
        description="x",
        design_name="widget",
        work_dir=work,
        max_turns=4,
        max_budget_usd=3.0,
        pdk_root="/tmp/fake_pdk",
    )
    # 1st turn: $2 (under cap, keeps going)
    # 2nd turn: $2 -> total $4 (hits cap, breaks)
    assert len(calls) == 2
    assert out.reason == "cost_cap"
    assert out.total_cost_usd == pytest.approx(4.0)


async def test_infra_error_aborts_loop(tmp_path, monkeypatch):
    work = tmp_path / "work"
    rate_limited = _make_idea_result(
        work_dir=work,
        success=False,
        all_passed=False,
        cost_usd=0.0,
        result_text="rate limit hit",
        error="Subprocess error: 429 rate-limit returned by API",
        gl_sim_passed=False,
    )
    extra = _make_idea_result(work_dir=work, success=True, all_passed=True)
    calls = _patch_generate(monkeypatch, [rate_limited, extra])

    out = await run_idea_to_rtl_loop(
        description="x",
        design_name="widget",
        work_dir=work,
        max_turns=4,
        pdk_root="/tmp/fake_pdk",
    )
    assert len(calls) == 1, "loop must not retry an infra error"
    assert out.reason == "error"


# ---------------------------------------------------------------------------
# Critique propagation
# ---------------------------------------------------------------------------


async def test_critique_header_propagates_into_turn_two_prompt(
    tmp_path, monkeypatch
):
    """Turn 2's description must carry the critique skill text + the failure
    excerpt from turn 1, so the agent has actionable feedback."""
    work = tmp_path / "work"
    turn1 = _make_idea_result(
        work_dir=work,
        success=True,
        all_passed=False,
        gl_sim_passed=False,
        cost_usd=0.5,
        result_text=(
            "Building counter4...\n"
            "RTL SIM FAIL\n"
            "ASSERTION FAILED in tb_widget.v:42 — "
            "expected count=1, got count=0\n"
        ),
    )
    turn2 = _make_idea_result(work_dir=work, success=True, all_passed=True)
    calls = _patch_generate(monkeypatch, [turn1, turn2])

    out = await run_idea_to_rtl_loop(
        description="A 4-bit synchronous counter with enable.",
        design_name="widget",
        work_dir=work,
        max_turns=3,
        pdk_root="/tmp/fake_pdk",
    )
    assert out.converged_turn == 2
    # Turn 1 sees the bare description.
    assert "PREVIOUS ITERATION FAILURE" not in calls[0]["description"]
    # Turn 2 sees the critique header + excerpt + bare description appended.
    t2_desc = calls[1]["description"]
    assert "PREVIOUS ITERATION FAILURE" in t2_desc
    assert "RTL SIM FAIL" in t2_desc  # excerpt content
    assert "MINIMAL PATCH" in t2_desc.upper()  # critique skill content
    assert "A 4-bit synchronous counter with enable." in t2_desc
    # Critique skill content includes the reset-discipline / read-only
    # guidance — confirm the sim-failure skill was selected.
    assert "ReadOnly" in t2_desc or "READONLY" in t2_desc


async def test_synth_lint_critique_selected_for_yosys_failure(
    tmp_path, monkeypatch
):
    """When the failure looks like a yosys / lint error, the loop should
    pick the digital.critique_synth_lint skill for the next prompt."""
    work = tmp_path / "work"
    # success=False so flow_status=fail, sim_status=missing,
    # gl_sim_status=missing -> matches the synth-lint dispatch branch.
    yosys_fail = _make_idea_result(
        work_dir=work,
        success=False,
        all_passed=False,
        cost_usd=0.5,
        result_text=(
            "ERROR: Wire \\foo is used but not driven\n"
            "yosys synth aborted\n"
        ),
        error="LibreLane synthesis stage failed",
        gl_sim_passed=False,
    )
    win = _make_idea_result(work_dir=work, success=True, all_passed=True)
    calls = _patch_generate(monkeypatch, [yosys_fail, win])

    out = await run_idea_to_rtl_loop(
        description="A multiplier",
        design_name="widget",
        work_dir=work,
        max_turns=3,
        pdk_root="/tmp/fake_pdk",
    )
    assert out.converged_turn == 2
    t2_desc = calls[1]["description"]
    # Synth-lint critique markers.
    assert "Wire <name> is used but not driven" in t2_desc
    assert "Combinational loop" in t2_desc or "logic loop" in t2_desc.lower()


# ---------------------------------------------------------------------------
# Per-turn classification helpers
# ---------------------------------------------------------------------------


def test_classify_sim_pass_marker(tmp_path):
    r = _make_idea_result(
        work_dir=tmp_path,
        success=True,
        all_passed=True,
        result_text="checkpoint\nRTL SIM PASS\nproceeding to LibreLane",
    )
    assert _classify_sim(r) == "pass"


def test_classify_sim_fail_marker(tmp_path):
    r = _make_idea_result(
        work_dir=tmp_path,
        success=True,
        all_passed=False,
        gl_sim_passed=False,
        result_text="ASSERTION FAILED in tb_widget.v",
    )
    assert _classify_sim(r) == "fail"


def test_classify_gl_sim_skipped(tmp_path):
    r = _make_idea_result(
        work_dir=tmp_path, success=True, all_passed=True
    )
    r.gl_sim = {"skipped": True, "all_passed": None}
    assert _classify_gl_sim(r) == "skipped"


def test_extract_failure_excerpt_prioritises_harness_error(tmp_path):
    r = _make_idea_result(
        work_dir=tmp_path,
        success=False,
        all_passed=False,
        gl_sim_passed=False,
        error="Timeout after 3600s",
        result_text="x" * 100,
    )
    excerpt = _extract_failure_excerpt(r)
    assert "harness error" in excerpt
    assert "Timeout after 3600s" in excerpt


def test_is_infra_error_detects_rate_limit():
    assert _is_infra_error("Subprocess error: 429 rate limit") is True
    assert _is_infra_error("Claude CLI not found at resolved path") is True
    assert _is_infra_error("Timeout after 600s") is True
    assert _is_infra_error("Simulation reported FAIL") is False
    assert _is_infra_error("DRC violation count=3") is False


# ---------------------------------------------------------------------------
# IdeaToRTLLoopResult shape
# ---------------------------------------------------------------------------


def test_loop_result_to_dict_round_trips(tmp_path):
    iteration = LoopIteration(
        turn=1,
        success=True,
        all_passed=True,
        sim_status="pass",
        flow_status="pass",
        gl_sim_status="pass",
        cost_usd=0.5,
        duration_s=10.0,
    )
    res = IdeaToRTLLoopResult(
        idea_result=_make_idea_result(work_dir=tmp_path / "x"),
        iterations=[iteration],
        total_cost_usd=0.5,
        converged_turn=1,
        reason="converged",
    )
    d = res.to_dict()
    import json
    assert json.dumps(d, default=str)  # serialisable
    assert d["reason"] == "converged"
    assert d["converged_turn"] == 1
    assert len(d["iterations"]) == 1
    assert d["iterations"][0]["sim_status"] == "pass"


def test_build_critique_description_falls_back_to_sim_critique(tmp_path):
    """No explicit fail markers -> still pick a critique (sim-failure)."""
    r = _make_idea_result(
        work_dir=tmp_path,
        success=False,
        all_passed=False,
        gl_sim_passed=False,
        result_text="something happened",
    )
    iteration = LoopIteration(
        turn=1,
        success=False,
        all_passed=False,
        sim_status="missing",
        flow_status="fail",  # would normally route to synth-lint
        gl_sim_status="missing",
        failure_excerpt="some failure context",
    )
    desc = _build_critique_description(
        base_description="some idea",
        previous_result=r,
        previous_iteration=iteration,
        turn=2,
        work_dir=tmp_path,
    )
    # flow_status=fail without sim/gl_sim fail routes to synth-lint.
    assert "synth" in desc.lower() or "yosys" in desc.lower()
    # Original description still present at the end.
    assert desc.endswith("some idea")
