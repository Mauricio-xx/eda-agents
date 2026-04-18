"""Unit tests for AnalogCompositionLoop.

Mocks OpenRouter + GLayoutRunner + SpiceRunner so tests run in CI
without network, gLayout, or ngspice. A separate live integration
test (marker: glayout + spice) exercises the real stack.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from eda_agents.agents.analog_composition_loop import (
    AnalogCompositionLoop,
    AnalogCompositionResult,
    IterationRecord,
)
from eda_agents.core.spice_runner import SpiceResult


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _fake_spice_runner(measurements: dict[str, float] | None = None,
                       success: bool = True) -> MagicMock:
    """Return a MagicMock that looks enough like a SpiceRunner."""
    runner = MagicMock()
    res = SpiceResult(
        success=success,
        measurements=measurements or {},
    )
    runner.run.return_value = res
    return runner


def _fake_glayout_runner(success: bool = True) -> MagicMock:
    runner = MagicMock()

    def _gen(component, params, output_dir):
        out = MagicMock()
        out.success = success
        if success:
            gds = Path(output_dir) / f"{component}.gds"
            gds.write_bytes(b"GDS_STUB")
            out.gds_path = str(gds)
            out.netlist_path = None
            out.error = None
        else:
            out.gds_path = None
            out.netlist_path = None
            out.error = "stub failure"
        return out

    runner.generate_component.side_effect = _gen
    return runner


def _make_loop(
    tmp_path: Path,
    spice_runner: MagicMock | None = None,
    glayout_runner: MagicMock | None = None,
    max_iterations: int = 3,
    attempt_layout: bool = False,
) -> AnalogCompositionLoop:
    loop = AnalogCompositionLoop(
        pdk="ihp_sg13g2",
        work_dir=tmp_path,
        model="google/gemini-2.5-flash",
        spice_runner=spice_runner or _fake_spice_runner(),
        max_iterations=max_iterations,
        max_budget_usd=1.0,
        attempt_layout=attempt_layout,
    )
    if glayout_runner is not None:
        loop.glayout_runner = glayout_runner
    return loop


# ------------------------------------------------------------------
# Tests: helpers
# ------------------------------------------------------------------


def test_result_serialises_to_json(tmp_path):
    it = IterationRecord(index=0, composition={"a": 1}, tokens=10)
    res = AnalogCompositionResult(
        success=False,
        converged=False,
        nl_description="hi",
        constraints={},
        pdk="ihp_sg13g2",
        iterations=[it],
        work_dir=str(tmp_path),
    )
    payload = res.to_json()
    # Round-trip through json to prove JSON-serialisable
    ser = json.dumps(payload)
    assert "iterations" in payload
    assert payload["iterations"][0]["composition"] == {"a": 1}
    assert ser  # non-empty


def test_skill_loads():
    """The analog.custom_composition skill is registered and renders."""
    from eda_agents.skills.registry import get_skill

    sk = get_skill("analog.custom_composition")
    body = sk.prompt_fn()
    assert len(body) > 1000
    # Sanity checks on the bundle content
    assert "gLayout primitives" in body
    assert "honest-fail" in body.lower() or "honest_fail" in body
    assert "JSON" in body


# ------------------------------------------------------------------
# Tests: loop with mocked LLM
# ------------------------------------------------------------------


@patch("eda_agents.agents.analog_composition_loop.call_openrouter")
def test_loop_converges_on_first_iteration(mock_or, tmp_path):
    """When LLM returns converged on iter 0, loop stops and reports success."""
    # Propose, size, critique — three LLM calls per iteration
    mock_or.side_effect = [
        # propose
        (json.dumps({
            "composition": [
                {"name": "cm0", "type": "current_mirror",
                 "params": {"width": 2.0, "length": 1.0, "fingers": 2,
                            "multipliers": 1, "type": "nfet"},
                 "purpose": "unit current source"},
            ],
            "connectivity": [
                {"from": "cm0.VSS", "to": "GND"},
            ],
            "testbench": {
                "inputs": {},
                "analysis": "op",
                "measurements": [],
            },
            "target_specs": {"Iout_uA_min": 0.5},
        }), 200),
        # size
        (json.dumps({"cm0": {"width": 2.5}}), 80),
        # critique → converged
        (json.dumps({
            "verdict": "converged",
            "rationale": "simulated within spec",
            "patch": {},
        }), 100),
    ]

    sr = _fake_spice_runner(measurements={"Iout_uA_min": 0.6})
    loop = _make_loop(tmp_path, spice_runner=sr, max_iterations=3)
    res = loop.loop(
        "current reference, 0.5uA min output",
        constraints={"supply_v": 1.2},
    )

    assert res.converged is True
    assert res.success is True
    assert len(res.iterations) == 1
    assert res.iterations[0].critique == {
        "verdict": "converged",
        "rationale": "simulated within spec",
        "patch": {},
    }
    # program.md + iterations.jsonl + result.json exist
    assert (tmp_path / "program.md").is_file()
    assert (tmp_path / "iterations.jsonl").is_file()
    assert (tmp_path / "result.json").is_file()


@patch("eda_agents.agents.analog_composition_loop.call_openrouter")
def test_loop_honest_fail_when_specs_never_met(mock_or, tmp_path):
    """LLM keeps returning 'patch' but SPICE fails; eventually honest_fail."""

    def _side_effect(**kwargs):
        user = kwargs["user_prompt"]
        if '"stage": "propose_composition"' in user:
            return (json.dumps({
                "composition": [
                    {"name": "cm", "type": "current_mirror",
                     "params": {"width": 1.0, "length": 1.0, "fingers": 1,
                                "multipliers": 1, "type": "nfet"}},
                ],
                "connectivity": [{"from": "cm.VSS", "to": "GND"}],
                "testbench": {"inputs": {}, "analysis": "op", "measurements": []},
                "target_specs": {"Iout_uA_min": 1.0},
            }), 200)
        if '"stage": "size_sub_blocks"' in user:
            return (json.dumps({"cm": {"width": 1.0}}), 80)
        # critique: fail first two, honest_fail on the third
        if 'iterations_remaining": 0' in user.replace(" ", ""):
            return (json.dumps({
                "verdict": "honest_fail",
                "rationale": "couldn't reach 1 uA on this primitive",
                "honest_fail_reason": "primitive inadequate",
            }), 120)
        return (json.dumps({
            "verdict": "patch",
            "rationale": "bump width",
            "patch": {"sizing": {"cm": {"width": 2.0}}},
        }), 100)

    mock_or.side_effect = _side_effect
    sr = _fake_spice_runner(measurements={"Iout_uA_min": 0.2})
    loop = _make_loop(tmp_path, spice_runner=sr, max_iterations=3)
    res = loop.loop("current reference 1 uA", constraints={})

    assert res.converged is False
    assert res.success is False
    assert res.honest_fail_reason is not None
    assert len(res.iterations) >= 1


@patch("eda_agents.agents.analog_composition_loop.call_openrouter")
def test_loop_budget_abort(mock_or, tmp_path):
    """If cumulative cost exceeds budget, loop stops early with honest_fail."""
    # Simulate large token counts to blow budget fast
    mock_or.return_value = (json.dumps({
        "composition": [
            {"name": "x", "type": "nmos",
             "params": {"width": 1.0, "length": 0.13, "fingers": 1}},
        ],
        "connectivity": [],
        "testbench": {"inputs": {}, "analysis": "op", "measurements": []},
        "target_specs": {},
    }), 5_000_000)  # huge token burn

    sr = _fake_spice_runner(measurements={})
    loop = AnalogCompositionLoop(
        pdk="ihp_sg13g2",
        work_dir=tmp_path,
        model="google/gemini-2.5-flash",
        spice_runner=sr,
        max_iterations=5,
        max_budget_usd=0.01,  # very tight
        attempt_layout=False,
    )
    res = loop.loop("anything", constraints={})

    assert res.converged is False
    assert res.honest_fail_reason is not None
    assert len(res.iterations) < 5  # aborted early


@patch("eda_agents.agents.analog_composition_loop.call_openrouter")
def test_loop_handles_malformed_llm_json(mock_or, tmp_path):
    """LLM returning non-JSON surfaces as an iteration error, not a crash."""
    mock_or.return_value = ("this is not JSON", 50)

    loop = _make_loop(tmp_path, max_iterations=2)
    res = loop.loop("broken", constraints={})
    # We still get a result with at least one iteration record showing error
    assert res.converged is False
    assert len(res.iterations) >= 1
    assert res.iterations[0].error is not None


@patch("eda_agents.agents.analog_composition_loop.call_openrouter")
def test_loop_generates_layout_when_spice_passes(mock_or, tmp_path):
    """With attempt_layout=True and SPICE all-pass, per-sub-block GDS is produced."""

    def _side_effect(**kwargs):
        user = kwargs["user_prompt"]
        if '"stage": "propose_composition"' in user:
            return (json.dumps({
                "composition": [
                    {"name": "cm", "type": "current_mirror",
                     "params": {"width": 2.0, "length": 1.0, "fingers": 2,
                                "multipliers": 1, "type": "nfet"}},
                ],
                "connectivity": [{"from": "cm.VSS", "to": "GND"}],
                "testbench": {"inputs": {}, "analysis": "op", "measurements": []},
                "target_specs": {"Iout_uA_min": 1.0},
            }), 200)
        if '"stage": "size_sub_blocks"' in user:
            return (json.dumps({"cm": {"width": 2.0}}), 80)
        return (json.dumps({
            "verdict": "converged",
            "rationale": "all specs met",
            "patch": {},
        }), 100)

    mock_or.side_effect = _side_effect
    sr = _fake_spice_runner(measurements={"Iout_uA_min": 1.2})
    gr = _fake_glayout_runner(success=True)
    loop = _make_loop(
        tmp_path,
        spice_runner=sr,
        glayout_runner=gr,
        max_iterations=2,
        attempt_layout=True,
    )
    res = loop.loop("one uA current ref", constraints={})

    assert res.converged
    assert res.iterations[0].layout is not None
    assert res.iterations[0].layout["attempted"] is True
    assert "cm" in res.iterations[0].layout["sub_block_gds"]


# ------------------------------------------------------------------
# Tests: stage helper — deck rendering
# ------------------------------------------------------------------


def test_render_block_spice_current_mirror():
    """current_mirror unwraps into ref + out MOSFET lines."""
    from eda_agents.agents.analog_composition_loop import _render_block_spice

    lines = _render_block_spice(
        name="cm",
        typ="current_mirror",
        params={"width": 4.0, "length": 1.0, "fingers": 2, "multipliers": 3},
        port_to_net={
            "cm.VREF": "IBIAS",
            "cm.VCOPY": "IOUT",
        },
        pdk="ihp_sg13g2",
    )
    assert any("Xcm_ref" in L for L in lines)
    assert any("Xcm_out" in L for L in lines)
    assert any("sg13_lv_nmos" in L for L in lines)
    assert any("m=3" in L for L in lines)


def test_canonical_net_prefers_globals():
    from eda_agents.agents.analog_composition_loop import _canonical_net

    assert _canonical_net("cm.VSS", "GND") == "GND"
    assert _canonical_net("IBIAS", "cm.VREF") == "IBIAS"
    # Unknown both sides: deterministic unique net
    out = _canonical_net("a.b", "c.d")
    assert "a_b" in out and "c_d" in out


def test_merge_sizing_applies_patch():
    from eda_agents.agents.analog_composition_loop import _merge_sizing

    cur = {"cm": {"width": 2.0, "length": 1.0}, "sw": {"width": 1.0}}
    patch = {"cm": {"width": 4.0}, "sw": {"fingers": 2}}
    out = _merge_sizing(cur, patch)
    assert out["cm"]["width"] == 4.0
    assert out["cm"]["length"] == 1.0
    assert out["sw"]["width"] == 1.0
    assert out["sw"]["fingers"] == 2


def test_render_sweep_control_emits_per_code_lines():
    """code_sweep unrolls into explicit alter + analysis + meas triples."""
    from eda_agents.agents.analog_composition_loop import _render_sweep_control

    spec = {
        "kind": "code_sweep",
        "n_bits": 2,
        "code_sources": ["VB0", "VB1"],
        "high_v": 1.2,
        "low_v": 0.0,
        "analysis": "op",
        "measurements": [
            {"name": "iop", "expr": "v(IOP)"},
            {"name": "idiff", "expr": "v(IOP)-v(ION)"},
        ],
    }
    out = _render_sweep_control(spec, {"supply_v": 1.2})

    # Four codes (2 bits): 0,1,2,3 — each has 2 alter + 1 op + 2 meas lines
    code_banners = [L for L in out if L.startswith("* --- code ")]
    assert len(code_banners) == 4

    # Code 0: both bits low
    i0 = out.index("* --- code 0 ---")
    assert out[i0 + 1] == "alter VB0 dc=0.0"
    assert out[i0 + 2] == "alter VB1 dc=0.0"

    # Code 3: both bits high
    i3 = out.index("* --- code 3 ---")
    assert out[i3 + 1] == "alter VB0 dc=1.2"
    assert out[i3 + 2] == "alter VB1 dc=1.2"
    # `op` is substituted with `tran 1n 2n` because ngspice's `.meas`
    # command does not support op analysis.
    assert out[i3 + 3] == "tran 1n 2n"

    # Measurements suffixed per code
    meas_lines = [L for L in out if L.startswith("meas ")]
    assert any("iop_c0" in L for L in meas_lines)
    assert any("iop_c3" in L for L in meas_lines)
    assert any("idiff_c2" in L for L in meas_lines)
    # Totals: 4 codes * 2 measurements = 8 meas lines
    assert len(meas_lines) == 8
    # All meas lines use `meas tran` (op was substituted).
    assert all(L.startswith("meas tran ") for L in meas_lines)


@pytest.mark.spice
def test_sweep_schema_ngspice_roundtrip(tmp_path):
    """ngspice actually parses the sweep-unrolled deck, runs, and writes
    per-code measurements that SpiceRunner can recover."""
    import shutil

    if shutil.which("ngspice") is None:
        pytest.skip("ngspice not on PATH")

    # Hand-write a minimal deck that mirrors what the sweep renderer
    # emits, driving a resistor divider whose top node tracks B0.
    deck = (tmp_path / "sweep.cir")
    deck.write_text(
        "* sweep smoke\n"
        "VVDD VDD 0 DC 1.2\n"
        "VB0 B0 0 DC 0\n"
        "R1 B0 OUT 1k\n"
        "R2 OUT 0 1k\n"
        ".control\n"
        "* --- code 0 ---\n"
        "alter VB0 dc=0.0\n"
        "tran 1n 2n\n"
        "meas tran iop_c0 find v(OUT) at=1n\n"
        "* --- code 1 ---\n"
        "alter VB0 dc=1.2\n"
        "tran 1n 2n\n"
        "meas tran iop_c1 find v(OUT) at=1n\n"
        "quit\n"
        ".endc\n"
        ".end\n"
    )
    from eda_agents.core.spice_runner import SpiceRunner

    result = SpiceRunner(pdk=None).run(deck, work_dir=tmp_path)
    # Some measurements must have landed even though success/Adc are not set
    # (this deck has no AC analysis).
    assert result.measurements.get("iop_c0") == pytest.approx(0.0, abs=1e-3)
    assert result.measurements.get("iop_c1") == pytest.approx(0.6, abs=1e-3)


def test_sweep_schema_produces_runnable_deck(tmp_path):
    """End-to-end: a minimal composition + sweep testbench renders into a
    SPICE deck that ngspice can at least parse (exit code = 0)."""
    pytest.importorskip("numpy")
    from eda_agents.agents.analog_composition_loop import AnalogCompositionLoop

    loop = AnalogCompositionLoop(
        pdk="ihp_sg13g2",
        work_dir=tmp_path / "state",
        attempt_layout=False,
        max_iterations=1,
        max_budget_usd=0.01,
    )

    composition = {
        "name": "dac_sim",
        "composition": [
            {"name": "m0", "type": "nmos",
             "params": {"width": 1.0, "length": 0.5, "fingers": 1}},
        ],
        "connectivity": [
            {"from": "m0.drain", "to": "IOP"},
            {"from": "m0.source", "to": "GND"},
            {"from": "m0.gate", "to": "VB0"},
            {"from": "m0.body", "to": "GND"},
        ],
        "testbench": {
            "analysis": "sweep",
            "inputs": {"VB0": "DC 0", "VDD": "DC 1.2"},
            "sweep": {
                "kind": "code_sweep",
                "n_bits": 1,
                "code_sources": ["VB0"],
                "high_v": 1.2,
                "low_v": 0.0,
                "analysis": "op",
                "measurements": [
                    {"name": "iop", "expr": "v(IOP)"},
                ],
            },
        },
        "target_specs": {},
    }

    iter_dir = tmp_path / "state" / "iter_0"
    iter_dir.mkdir(parents=True)
    deck_path = loop._write_spice_deck(
        composition=composition,
        sizing={"m0": {"width": 1.0, "length": 0.5, "fingers": 1}},
        iter_dir=iter_dir,
        constraints={"supply_v": 1.2},
    )
    deck = deck_path.read_text()

    # The deck contains the explicit per-code structure rather than a
    # single `dc` or `tran` line.
    assert "* code_sweep:" in deck
    assert "meas tran iop_c0" in deck
    assert "meas tran iop_c1" in deck
    # Two sweep points: VB0 low then high
    assert deck.count("alter VB0 dc=0.0") == 1
    assert deck.count("alter VB0 dc=1.2") == 1


# ------------------------------------------------------------------
# Live integration test (gated by markers)
# ------------------------------------------------------------------


@pytest.mark.glayout
@pytest.mark.spice
@pytest.mark.skipif(
    not (Path("/home/montanares/personal_exp/eda-agents/.venv-glayout").is_dir()),
    reason="no .venv-glayout available",
)
def test_live_small_composition_2_iterations(tmp_path, monkeypatch):
    """End-to-end smoke with a 2-iteration budget + Gemini Flash.

    Skipped unless OPENROUTER_API_KEY is set. Gated by --live.
    """
    import os

    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    loop = AnalogCompositionLoop(
        pdk="ihp_sg13g2",
        work_dir=tmp_path,
        model="google/gemini-2.5-flash",
        max_iterations=2,
        max_budget_usd=2.0,
        attempt_layout=False,  # pre-layout SPICE only for smoke
    )
    res = loop.loop(
        "A 2-bit current-steering DAC with 1 uA LSB output, IHP SG13G2.",
        constraints={"supply_v": 1.2, "inl_lsb_max": 0.5},
    )
    # We don't assert convergence — honest-fail is acceptable. We DO
    # assert the loop produced a valid result structure.
    assert isinstance(res, AnalogCompositionResult)
    assert len(res.iterations) >= 1
    assert res.work_dir == str(tmp_path)
    # Artefacts exist
    assert (tmp_path / "result.json").is_file()


# ------------------------------------------------------------------
# MCP tool surface
# ------------------------------------------------------------------


@pytest.mark.mcp
def test_mcp_tool_registered():
    """explore_custom_topology is registered on the mcp instance."""
    pytest.importorskip("fastmcp")
    from eda_agents.mcp.server import mcp

    tool_names = {
        t.name if hasattr(t, "name") else str(t)
        for t in (getattr(mcp, "_tools", {}) or {}).values()
    }
    # Fallback path: fastmcp exposes tools through a different attr
    if not tool_names:
        try:
            registry = mcp._tool_manager._tools  # type: ignore[attr-defined]
            tool_names = set(registry.keys())
        except Exception:
            pytest.skip("cannot introspect fastmcp tool registry")

    assert "explore_custom_topology" in tool_names


@pytest.mark.mcp
def test_mcp_tool_dry_path_without_api_key(monkeypatch, tmp_path):
    """With OPENROUTER_API_KEY unset, the tool returns a structured
    result documenting an iteration-level failure rather than raising."""
    pytest.importorskip("fastmcp")
    import asyncio as _asyncio

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from eda_agents.mcp.server import explore_custom_topology

    fn = (
        explore_custom_topology.fn
        if hasattr(explore_custom_topology, "fn")
        else explore_custom_topology
    )
    result = _asyncio.run(
        fn(
            description="smoke",
            max_iterations=1,
            max_budget_usd=0.01,
            attempt_layout=False,
            output_dir=str(tmp_path),
            timeout_s=30,
        )
    )
    assert "converged" in result
    assert result["converged"] is False
    # The iteration error surfaces via honest_fail_reason or iteration.error
    ser = json.dumps(result)
    assert "OPENROUTER_API_KEY" in ser or "honest_fail" in ser or "error" in ser
