"""End-to-end XSPICE code-model build + simulation tests.

Exercises the ``XSpiceCompiler`` stage runner on the four in-house
primitives under ``src/eda_agents/veriloga/voltage_domain/``, then
runs ngspice through ``SpiceRunner(extra_codemodel=...)`` with a
cwd-local ``.spiceinit`` shim to confirm the ``codemodel`` plumbing
loads before the netlist parse.

Entire file is skipped when the toolchain is unavailable (no ngspice
source tree with a built ``cmpp``, or no C compiler) — that is the
Session 5 contract: XSPICE is optional, but when the prereqs exist
the primitives must build and simulate correctly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eda_agents.core.stages.xspice_compile import (
    CodeModelSource,
    XSpiceCompiler,
    load_codemodel_line,
)
from eda_agents.topologies.sar_adc_8bit_behavioral import (
    behavioral_comparator_cards,
    build_behavioral_comparator_kit,
    generate_behavioral_comparator_deck,
)
from eda_agents.veriloga.voltage_domain import primitive_paths

pytestmark = pytest.mark.xspice


@pytest.fixture(scope="module")
def _compiler() -> XSpiceCompiler:
    comp = XSpiceCompiler()
    if not comp.available():
        pytest.skip(
            "XSPICE toolchain not available (need ngspice source tree "
            "with cmpp + C compiler)."
        )
    return comp


@pytest.fixture(scope="module")
def _bundle_cm(_compiler: XSpiceCompiler, tmp_path_factory) -> Path:
    """Build a single ``.cm`` bundling all four primitives."""
    out_dir = tmp_path_factory.mktemp("xspice_bundle")
    sources = []
    for name in ("comparator_ideal", "clock_gen", "opamp_ideal", "edge_sampler"):
        mod, ifs = primitive_paths(name)
        sources.append(
            CodeModelSource(name=f"ea_{name}", cfunc_mod=mod, ifspec_ifs=ifs),
        )
    out_path = out_dir / "ea_primitives.cm"
    result = _compiler.compile(sources, out_path, work_dir=out_dir / "build")
    assert result.success, f"compile failed: {result.error}\n{result.log_tail}"
    assert result.artifacts["cm"].is_file()
    return result.artifacts["cm"]


def _run_ngspice(work_dir: Path, cir: Path) -> str:
    """Run ngspice -b in ``work_dir`` and return its stdout."""
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
    """Pull 'name = <value>' out of ngspice stdout."""
    for line in stdout.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(name.lower()) and "=" in stripped:
            val = stripped.split("=", 1)[1].strip()
            return float(val.split()[0])
    raise AssertionError(f"measurement {name!r} not in output:\n{stdout}")


class TestToolchainDetection:
    def test_toolchain_resolved(self, _compiler: XSpiceCompiler) -> None:
        tc = _compiler.toolchain()
        assert tc is not None
        assert tc.cmpp.is_file()
        assert tc.dlmain_c.is_file()
        assert all(d.is_dir() for d in tc.include_dirs)


class TestCompile:
    def test_bundle_builds(self, _bundle_cm: Path) -> None:
        assert _bundle_cm.is_file()
        # ELF/shared object sanity
        data = _bundle_cm.read_bytes()[:4]
        assert data == b"\x7fELF"

    def test_missing_source_errors_cleanly(
        self, _compiler: XSpiceCompiler, tmp_path: Path
    ) -> None:
        bad = CodeModelSource(
            name="ea_missing",
            cfunc_mod=tmp_path / "nope.mod",
            ifspec_ifs=tmp_path / "nope.ifs",
        )
        res = _compiler.compile([bad], tmp_path / "out.cm", work_dir=tmp_path / "b")
        assert not res.success
        assert "missing" in (res.error or "")


class TestComparatorIdeal:
    def test_comparator_decides_threshold(
        self, _bundle_cm: Path, tmp_path: Path
    ) -> None:
        (tmp_path / ".spiceinit").write_text(load_codemodel_line(_bundle_cm) + "\n")
        cir = tmp_path / "comp.cir"
        cir.write_text(
            "* comparator threshold test\n"
            "vref vref 0 dc 0.5\n"
            "vin  vin 0  pwl(0 0.1  1u 0.1  2u 0.9  3u 0.1  4u 0.1)\n"
            "acomp vin vref cout ea_comp\n"
            ".model ea_comp ea_comparator_ideal("
            "vout_high=1.0 vout_low=0.0 hysteresis_v=0.01)\n"
            "rl cout 0 1meg\n"
            ".tran 5n 4u\n"
            ".control\n"
            "run\n"
            "meas tran v_below find v(cout) at=0.5u\n"
            "meas tran v_above find v(cout) at=2.0u\n"
            "meas tran v_back find v(cout) at=3.5u\n"
            "quit\n"
            ".endc\n"
            ".end\n"
        )
        out = _run_ngspice(tmp_path, cir)
        assert _parse_meas(out, "v_below") == pytest.approx(0.0, abs=0.05)
        assert _parse_meas(out, "v_above") == pytest.approx(1.0, abs=0.05)
        assert _parse_meas(out, "v_back") == pytest.approx(0.0, abs=0.05)


class TestClockGen:
    def test_clock_produces_duty_cycle(
        self, _bundle_cm: Path, tmp_path: Path
    ) -> None:
        (tmp_path / ".spiceinit").write_text(load_codemodel_line(_bundle_cm) + "\n")
        cir = tmp_path / "clk.cir"
        # 1 MHz, 50% duty: first half high, second half low.
        cir.write_text(
            "* clock_gen sanity\n"
            "aclk out ea_clk\n"
            ".model ea_clk ea_clock_gen("
            "period_s=1u duty=0.5 v_high=1.0 v_low=0.0 delay_s=0)\n"
            "rl out 0 1meg\n"
            ".tran 5n 2u\n"
            ".control\n"
            "run\n"
            "meas tran hi1 find v(out) at=100n\n"
            "meas tran lo1 find v(out) at=700n\n"
            "meas tran hi2 find v(out) at=1.1u\n"
            "quit\n"
            ".endc\n"
            ".end\n"
        )
        out = _run_ngspice(tmp_path, cir)
        assert _parse_meas(out, "hi1") == pytest.approx(1.0, abs=0.05)
        assert _parse_meas(out, "lo1") == pytest.approx(0.0, abs=0.05)
        assert _parse_meas(out, "hi2") == pytest.approx(1.0, abs=0.05)


class TestBehavioralComparatorKit:
    def test_cards_render_correctly(self) -> None:
        inst, model = behavioral_comparator_cards(
            model_ref="mycmp",
            vout_high=1.8,
            vout_low=0.0,
            hysteresis_v=0.02,
        )
        assert "ACMP cmp_p cmp_n cmp_out mycmp" == inst
        assert "mycmp ea_comparator_ideal" in model
        assert "vout_high=1.8" in model
        assert "hysteresis_v=0.02" in model

    def test_build_kit_produces_cm(
        self, _compiler: XSpiceCompiler, tmp_path: Path
    ) -> None:
        kit = build_behavioral_comparator_kit(tmp_path, compiler=_compiler)
        assert kit is not None
        assert kit.cm_path.is_file()
        assert "codemodel" in kit.spiceinit_line()
        snippet = kit.netlist_snippet()
        assert snippet.count("\n") == 1
        assert ".model" in snippet

    def test_deck_runs_end_to_end(
        self, _compiler: XSpiceCompiler, tmp_path: Path
    ) -> None:
        cir, kit = generate_behavioral_comparator_deck(tmp_path)
        assert kit is not None
        (tmp_path / ".spiceinit").write_text(kit.spiceinit_line() + "\n")
        out = _run_ngspice(tmp_path, cir)
        vhi = _parse_meas(out, "vcmp_hi")
        vlo = _parse_meas(out, "vcmp_lo")
        assert vhi == pytest.approx(1.2, abs=0.05)
        assert vlo == pytest.approx(0.0, abs=0.05)
