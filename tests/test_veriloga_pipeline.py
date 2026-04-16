"""End-to-end Verilog-A -> OSDI -> ngspice round-trip.

Writes a trivial two-terminal resistor model, compiles it with
``openvaf`` via ``VerilogACompiler``, and simulates a DC sweep with
``SpiceRunner(extra_osdi=[osdi])``. Validates that the measured
current matches Ohm's law within tight tolerance.

Skips cleanly when ``openvaf``, ``ngspice``, or a usable PDK root is
missing so CI without the toolchain stays green.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from eda_agents.core.pdk import netlist_osdi_lines, resolve_pdk
from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.core.stages.veriloga_compile import VerilogACompiler

from tests.conftest import ihp_available, gf180_available

pytestmark = pytest.mark.veriloga


RESISTOR_VA = textwrap.dedent(
    """\
    // Linear two-terminal resistor for the eda-agents veriloga
    // round-trip test. Ohm's law in current-domain form.
    `include "disciplines.vams"

    module eda_res(p, n);
        inout p, n;
        electrical p, n;

        parameter real r = 1000.0 from (0:inf);

        analog begin
            I(p, n) <+ V(p, n) / r;
        end
    endmodule
    """
)


def _pick_pdk():
    if ihp_available:
        return resolve_pdk("ihp_sg13g2")
    if gf180_available:
        return resolve_pdk("gf180mcu")
    pytest.skip("No PDK root available for SpiceRunner construction")


@pytest.fixture
def compiler():
    if shutil.which("openvaf") is None:
        pytest.skip("openvaf not on PATH")
    return VerilogACompiler()


@pytest.fixture
def ngspice_required():
    if shutil.which("ngspice") is None:
        pytest.skip("ngspice not on PATH")


def test_netlist_osdi_lines_appends_extras(tmp_path):
    pdk = resolve_pdk("ihp_sg13g2")
    extra = tmp_path / "user_model.osdi"
    extra.write_bytes(b"")

    lines = netlist_osdi_lines(pdk, extra_osdi=[extra])

    assert any("psp103_nqs.osdi" in ln for ln in lines)
    assert any(str(extra.resolve()) in ln for ln in lines)
    assert lines[-1].strip().startswith("osdi")


def test_netlist_osdi_lines_extras_only_for_bsim4(tmp_path):
    pdk = resolve_pdk("gf180mcu")
    assert netlist_osdi_lines(pdk) == []

    extra = tmp_path / "u.osdi"
    extra.write_bytes(b"")
    lines = netlist_osdi_lines(pdk, extra_osdi=[extra])
    assert len(lines) == 1
    assert str(extra.resolve()) in lines[0]


def test_veriloga_compile_missing_source(compiler, tmp_path):
    result = compiler.run(tmp_path / "does_not_exist.va")
    assert not result.success
    assert "not found" in (result.error or "")


def test_veriloga_compile_resistor(compiler, tmp_path):
    va = tmp_path / "eda_res.va"
    va.write_text(RESISTOR_VA)

    result = compiler.run(va)

    assert result.success, f"openvaf failed: {result.error}\n{result.log_tail}"
    osdi = result.artifacts["osdi"]
    assert osdi.is_file()
    assert osdi.suffix == ".osdi"
    assert osdi.stat().st_size > 0


def test_resistor_roundtrip_ohms_law(compiler, ngspice_required, tmp_path):
    """Compile resistor .va, load via pre_osdi, sweep DC, check I=V/R."""
    pdk = _pick_pdk()

    va = tmp_path / "eda_res.va"
    va.write_text(RESISTOR_VA)
    compile_result = compiler.run(va)
    assert compile_result.success, compile_result.error
    osdi = compile_result.artifacts["osdi"]

    r_value = 2000.0
    v_test = 0.5

    # Only load the Verilog-A model in the cir: this is a pure user-model
    # test that does not need PDK OSDI binding. SpiceRunner writes the
    # cwd .spiceinit that pre-registers eda_res before parsing .model.
    abs_osdi = Path(osdi).resolve()
    cir_lines = [
        "* Verilog-A resistor round-trip",
        "",
        ".control",
        f"  osdi '{abs_osdi}'",
        "  dc V1 0 1.0 0.05",
        f"  meas dc i_out FIND i(V1) AT={v_test}",
        ".endc",
        "",
        "V1 a 0 DC 0",
        f".model rmod eda_res r={r_value}",
        "Nr1 a 0 rmod",
        "",
        ".end",
        "",
    ]

    cir = tmp_path / "res_dc.cir"
    cir.write_text("\n".join(cir_lines))

    runner = SpiceRunner(pdk=pdk, extra_osdi=[osdi], timeout_s=30)
    assert runner.extra_osdi == (osdi.resolve(),)

    spice_result = runner.run(cir)

    assert spice_result.success, (
        f"ngspice failed: {spice_result.error}\n"
        f"stdout:\n{spice_result.stdout_tail}\n"
        f"stderr:\n{spice_result.stderr_tail}"
    )

    i_out = spice_result.measurements.get("i_out")
    assert i_out is not None, (
        f"i_out missing from measurements: {spice_result.measurements}\n"
        f"stdout:\n{spice_result.stdout_tail}"
    )

    # ngspice reports current flowing INTO the positive terminal of V1;
    # with R from node 'a' to 0 and V1 from a to 0 at +v_test, current
    # exits V1's positive terminal so i(V1) is negative.
    expected = -v_test / r_value
    assert i_out == pytest.approx(expected, rel=5e-3), (
        f"Ohm's law violated: got {i_out}, expected {expected}"
    )
