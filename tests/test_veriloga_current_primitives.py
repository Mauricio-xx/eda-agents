"""Verilog-A current-domain primitive tests.

Compiles each ``.va`` under ``src/eda_agents/veriloga/current_domain/``
with openvaf and exercises a minimal measurement to confirm the
primitive behaves as advertised through ngspice + OSDI.

Marked ``veriloga``; skips cleanly when openvaf is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agents.core.stages.veriloga_compile import VerilogACompiler
from eda_agents.veriloga.current_domain import primitive_path

pytestmark = pytest.mark.veriloga


@pytest.fixture(scope="module")
def _compiler() -> VerilogACompiler:
    comp = VerilogACompiler()
    if not comp.available():
        pytest.skip("openvaf not available")
    if not shutil.which("ngspice"):
        pytest.skip("ngspice not available")
    return comp


def _run_ngspice(work_dir: Path, cir: Path) -> str:
    proc = subprocess.run(
        ["ngspice", "-b", str(cir)],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode in (0, 1), (
        f"ngspice exit {proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    return proc.stdout


def _parse_meas(stdout: str, name: str) -> float:
    for line in stdout.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(name.lower()) and "=" in stripped:
            val = stripped.split("=", 1)[1].strip()
            return float(val.split()[0])
    raise AssertionError(f"measurement {name!r} not in output:\n{stdout}")


@pytest.mark.parametrize(
    "primitive",
    ["filter_1st", "opamp_1p", "ldo_beh"],
)
def test_primitive_compiles(
    _compiler: VerilogACompiler, primitive: str, tmp_path: Path
) -> None:
    src = primitive_path(primitive)
    res = _compiler.run(src, out_dir=tmp_path)
    assert res.success, f"{primitive}: {res.error}\n{res.log_tail}"
    assert res.artifacts["osdi"].is_file()


class TestFilter1st:
    def test_first_order_pole_is_minus_3dB(
        self, _compiler: VerilogACompiler, tmp_path: Path
    ) -> None:
        # R=10k, C=159.155pF -> fp ~= 100 kHz
        src = primitive_path("filter_1st")
        res = _compiler.run(src, out_dir=tmp_path)
        assert res.success, res.error
        osdi = res.artifacts["osdi"].resolve()
        (tmp_path / ".spiceinit").write_text(f"osdi '{osdi}'\n")
        cir = tmp_path / "ac.cir"
        cir.write_text(
            "* filter_1st -3dB point\n"
            "vin in 0 dc 0 ac 1\n"
            "n1 in out 0 filt1\n"
            ".model filt1 filter_1st r_ohm=10e3 c_f=159.1549e-12\n"
            ".ac dec 40 10 10meg\n"
            ".control\n"
            "run\n"
            "meas ac mag_lowf find vdb(out) at=10\n"
            "meas ac mag_pole  find vdb(out) at=100e3\n"
            "meas ac mag_hif   find vdb(out) at=1meg\n"
            "quit\n"
            ".endc\n"
            ".end\n"
        )
        out = _run_ngspice(tmp_path, cir)
        assert _parse_meas(out, "mag_lowf") == pytest.approx(0.0, abs=0.1)
        assert _parse_meas(out, "mag_pole") == pytest.approx(-3.0, abs=0.3)
        # At 10x above pole, roll-off ~ -20 dB
        assert _parse_meas(out, "mag_hif") == pytest.approx(-20.0, abs=1.0)


class TestLdoBeh:
    def test_dc_output_tracks_vref(
        self, _compiler: VerilogACompiler, tmp_path: Path
    ) -> None:
        src = primitive_path("ldo_beh")
        res = _compiler.run(src, out_dir=tmp_path)
        assert res.success, res.error
        osdi = res.artifacts["osdi"].resolve()
        (tmp_path / ".spiceinit").write_text(f"osdi '{osdi}'\n")
        cir = tmp_path / "dc.cir"
        # Use transient long-settle to sidestep the measure-at=0 DC
        # pitfall: the LDO's output capacitor filters the OP step and
        # eventually settles to vref + (small) coupling term.
        cir.write_text(
            "* ldo_beh settle\n"
            "vin vin 0 dc 1.8\n"
            "n1 vin vout 0 ldo1\n"
            ".model ldo1 ldo_beh vref=1.2 psrr_db=60 rout_ohm=0.05 bw_hz=1e6\n"
            "rl vout 0 1meg\n"
            ".tran 10u 5m uic\n"
            ".control\n"
            "run\n"
            "meas tran vout_final find v(vout) at=4.9m\n"
            "quit\n"
            ".endc\n"
            ".end\n"
        )
        out = _run_ngspice(tmp_path, cir)
        # PSRR=60 dB -> coupling 1e-3 of 0.6 V ripple above vref = 6e-4.
        # The LDO settles within a few %% of vref at steady state.
        assert _parse_meas(out, "vout_final") == pytest.approx(1.2, abs=0.05)
