"""Tests for the typed ``BenchTask.inputs`` schemas (gap #11).

These tests exercise two axes:

1. Pure Pydantic validation (positive + negative) on each input model.
2. End-to-end: a task with a typo in ``inputs`` routes through the real
   adapter and produces ``FAIL_INFRA`` with the Pydantic message
   surfaced, rather than silently using defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eda_agents.bench.adapter_inputs import (
    AnalogRolesInputs,
    AnalyticalMillerInputs,
    DigitalFlowInputs,
    DryRunInputs,
    GlSimPostSynthInputs,
    LlmSpecToSizingInputs,
    MillerDesignParams,
    PreSimGateInputs,
    Sar11bEnobInputs,
)
from eda_agents.bench.adapters import HARNESS_DISPATCH, run_task
from eda_agents.bench.models import BenchStatus, BenchTask


# ---------------------------------------------------------------------------
# Pure schema validation
# ---------------------------------------------------------------------------


def test_dry_run_inputs_accepts_empty():
    DryRunInputs.model_validate({})


def test_dry_run_inputs_rejects_unknown_field():
    with pytest.raises(ValidationError):
        DryRunInputs.model_validate({"fake_metric": {"Adc_dB": 60.0}})


def test_analog_roles_requires_spec_yaml():
    with pytest.raises(ValidationError):
        AnalogRolesInputs.model_validate({})


def test_analog_roles_rejects_typo_iterations():
    with pytest.raises(ValidationError):
        AnalogRolesInputs.model_validate(
            {"spec_yaml": "block: miller_ota\n", "max_iter": 3}
        )


def test_analog_roles_bounds_iterations():
    with pytest.raises(ValidationError):
        AnalogRolesInputs.model_validate(
            {"spec_yaml": "x: 1\n", "max_iterations": 0}
        )
    with pytest.raises(ValidationError):
        AnalogRolesInputs.model_validate(
            {"spec_yaml": "x: 1\n", "max_iterations": 11}
        )


def test_analytical_miller_inputs_valid():
    inp = AnalyticalMillerInputs.model_validate(
        {
            "callable": "eda_agents.bench.adapters:analytical_miller_design",
            "design_params": {
                "gmid_input": 12.0,
                "gmid_load": 10.0,
                "L_input": 1.0e-6,
                "L_load": 1.0e-6,
                "Cc": 1.0e-12,
            },
        }
    )
    assert inp.design_params.gmid_input == 12.0
    assert inp.design_params.Ibias is None


def test_analytical_miller_inputs_typo_field():
    with pytest.raises(ValidationError):
        AnalyticalMillerInputs.model_validate(
            {
                "callable": "eda_agents.bench.adapters:analytical_miller_design",
                "design_params": {
                    "gmid_inputs": 12.0,  # typo
                    "gmid_load": 10.0,
                    "L_input": 1.0e-6,
                    "L_load": 1.0e-6,
                    "Cc": 1.0e-12,
                },
            }
        )


def test_miller_design_params_bounds():
    # gmid must be > 0 and <= 40
    with pytest.raises(ValidationError):
        MillerDesignParams.model_validate(
            {
                "gmid_input": 0.0,
                "gmid_load": 10.0,
                "L_input": 1.0e-6,
                "L_load": 1.0e-6,
                "Cc": 1.0e-12,
            }
        )
    with pytest.raises(ValidationError):
        MillerDesignParams.model_validate(
            {
                "gmid_input": 41.0,
                "gmid_load": 10.0,
                "L_input": 1.0e-6,
                "L_load": 1.0e-6,
                "Cc": 1.0e-12,
            }
        )


def test_pre_sim_gate_inputs_valid():
    inp = PreSimGateInputs.model_validate(
        {
            "callable": "eda_agents.bench.adapters:run_pre_sim_gate_on_inline_netlist",
            "gate": "floating_nodes",
            "subckt": "x",
            "expect_violation": False,
            "netlist": ".subckt x in out\n.ends\n",
        }
    )
    assert inp.gate == "floating_nodes"


def test_pre_sim_gate_inputs_unknown_gate():
    with pytest.raises(ValidationError):
        PreSimGateInputs.model_validate(
            {
                "callable": "x:y",
                "gate": "bogus_gate",
                "subckt": "x",
                "netlist": ".ends\n",
            }
        )


def test_pre_sim_gate_inputs_empty_netlist():
    with pytest.raises(ValidationError):
        PreSimGateInputs.model_validate(
            {
                "callable": "x:y",
                "gate": "floating_nodes",
                "subckt": "x",
                "netlist": "",
            }
        )


def test_gl_sim_post_synth_inputs_run_dir_optional():
    inp = GlSimPostSynthInputs.model_validate(
        {"callable": "x:y", "run_dir": None}
    )
    assert inp.run_dir is None


def test_gl_sim_post_synth_inputs_rejects_extra():
    with pytest.raises(ValidationError):
        GlSimPostSynthInputs.model_validate(
            {"callable": "x:y", "rundir": "/tmp/foo"}  # typo
        )


def test_sar11_enob_inputs_defaults():
    inp = Sar11bEnobInputs.model_validate({"callable": "x:y"})
    assert inp.N_samples == 128
    assert inp.Fs_Hz == 1.0e6
    assert inp.Fin_Hz is None


def test_llm_inputs_model_default():
    inp = LlmSpecToSizingInputs.model_validate(
        {
            "callable": "x:y",
            "spec_yaml": "block: miller_ota\n",
        }
    )
    assert "gemini" in inp.model.lower()
    assert inp.temperature == 0.0


def test_digital_flow_inputs_defaults():
    inp = DigitalFlowInputs.model_validate(
        {
            "callable": "x:y",
            "design_dir": "bench/designs/counter_bench",
        }
    )
    assert inp.stop_after == "Checker.KLayoutDRC"
    assert inp.cache_run_dir is True


def test_digital_flow_inputs_rejects_typo():
    with pytest.raises(ValidationError):
        DigitalFlowInputs.model_validate(
            {
                "callable": "x:y",
                "design_dir": "bench/designs/counter_bench",
                "stop_after_step": "ROUTE",  # typo: real field is stop_after
            }
        )


# ---------------------------------------------------------------------------
# End-to-end: bogus input goes through the dispatcher and produces
# FAIL_INFRA with a pointed message (not ERROR, not silent default).
# ---------------------------------------------------------------------------


def _base_task_dict():
    return {
        "id": "e2e_typed_input_reject",
        "family": "bugfix",
        "category": "structural",
        "domain": "voltage",
        "pdk": "ihp_sg13g2",
        "difficulty": "easy",
        "expected_backend": "dry-run",
        "harness": "callable",
        "scoring": ["audit_passed"],
    }


def test_typed_inputs_reject_typo_propagates_to_runner(tmp_path):
    bogus = {
        **_base_task_dict(),
        "inputs": {
            "callable": "eda_agents.bench.adapters:run_pre_sim_gate_on_inline_netlist",
            "gate": "floating_nodes",
            "subckt": "x",
            "expect_violate": True,  # typo — real field is expect_violation
            "netlist": ".subckt x in out\n.ends\n",
        },
    }
    task = BenchTask.model_validate(bogus)
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert res.errors, "expected Pydantic error surfaced"
    assert "expect_violate" in " ".join(res.errors)


def test_typed_inputs_accept_valid_via_dispatcher(tmp_path):
    good = {
        **_base_task_dict(),
        "inputs": {
            "callable": "eda_agents.bench.adapters:run_pre_sim_gate_on_inline_netlist",
            "gate": "floating_nodes",
            "subckt": "clean",
            "expect_violation": False,
            "netlist": (
                ".subckt clean in out vdd vss\n"
                "M1 out in vdd vdd pfet W=2u L=180n\n"
                "M2 out in vss vss nfet W=1u L=180n\n"
                ".ends\n"
            ),
        },
    }
    task = BenchTask.model_validate(good)
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.PASS
    # HARNESS_DISPATCH stays populated
    assert "callable" in HARNESS_DISPATCH
