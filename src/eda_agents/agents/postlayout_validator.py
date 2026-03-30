"""Post-layout validation pipeline for analog circuits.

Orchestrates the full layout -> DRC -> LVS -> PEX -> post-layout SPICE
pipeline. Compares post-layout performance against pre-layout results
to quantify parasitic impact.

Pipeline steps:
    1. Generate OTA layout (GDS + schematic netlist) via gLayout
    2. Run DRC (KLayout)
    3. Run LVS (KLayout) comparing layout vs schematic
    4. Run PEX (Magic) to extract parasitics
    5. Generate post-layout testbench
    6. Run post-layout SPICE simulation
    7. Compare pre-layout vs post-layout metrics
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from eda_agents.agents.phase_results import PostLayoutResult

logger = logging.getLogger(__name__)


class PostLayoutValidator:
    """Full post-layout validation pipeline for GF180 OTA.

    Parameters
    ----------
    topology : GF180OTATopology
        Circuit topology (for sizing, testbench generation, FoM).
    glayout_runner : GLayoutRunner
        Layout generator.
    magic_pex_runner : MagicPexRunner
        Parasitic extraction runner.
    spice_runner : SpiceRunner
        SPICE simulator.
    drc_runner : KLayoutDrcRunner or None
        DRC checker. If None, DRC is skipped.
    lvs_runner : KLayoutLvsRunner or None
        LVS checker. If None, LVS is skipped.
    """

    def __init__(
        self,
        topology,
        glayout_runner,
        magic_pex_runner,
        spice_runner,
        drc_runner=None,
        lvs_runner=None,
    ):
        self.topology = topology
        self.glayout = glayout_runner
        self.pex = magic_pex_runner
        self.spice = spice_runner
        self.drc = drc_runner
        self.lvs = lvs_runner

    def validate(
        self,
        params: dict[str, float],
        pre_layout_fom: float,
        pre_layout_spice=None,
        work_dir: str | Path = "/tmp/postlayout",
    ) -> PostLayoutResult:
        """Run the full post-layout validation pipeline.

        Parameters
        ----------
        params : dict
            Design space parameters.
        pre_layout_fom : float
            FoM from pre-layout simulation (for delta computation).
        pre_layout_spice : SpiceResult or None
            Pre-layout SPICE results for detailed delta computation.
        work_dir : path
            Working directory for all artifacts.

        Returns
        -------
        PostLayoutResult
        """
        t0 = time.monotonic()
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        result = PostLayoutResult(params=params, pre_layout_fom=pre_layout_fom)

        # Step 1: Generate layout
        sizing = self.topology.params_to_sizing(params)
        layout_dir = work_dir / "layout"

        logger.info("Step 1/6: Generating OTA layout...")
        layout_result = self.glayout.generate_ota(sizing, layout_dir)

        if not layout_result.success:
            result.error = f"Layout generation failed: {layout_result.error}"
            result.total_time_s = time.monotonic() - t0
            return result

        result.gds_path = layout_result.gds_path
        result.netlist_path = layout_result.netlist_path
        logger.info("Layout generated: %s (%.1fs)", result.gds_path, layout_result.run_time_s)

        # Step 2: DRC
        if self.drc:
            logger.info("Step 2/6: Running DRC...")
            drc_dir = work_dir / "drc"
            drc_result = self.drc.run(
                gds_path=result.gds_path,
                run_dir=drc_dir,
            )
            result.drc_clean = drc_result.clean
            result.drc_violations = drc_result.total_violations
            logger.info("DRC: %s", drc_result.summary)
        else:
            logger.info("Step 2/6: DRC skipped (no runner)")

        # Step 3: LVS
        if self.lvs and result.netlist_path:
            logger.info("Step 3/6: Running LVS...")
            lvs_dir = work_dir / "lvs"
            lvs_result = self.lvs.run(
                gds_path=result.gds_path,
                netlist_path=result.netlist_path,
                run_dir=lvs_dir,
            )
            result.lvs_match = lvs_result.match
            logger.info("LVS: %s", lvs_result.summary)
        else:
            logger.info("Step 3/6: LVS skipped (no runner or netlist)")

        # Step 4: PEX
        logger.info("Step 4/6: Running parasitic extraction...")
        pex_dir = work_dir / "pex"
        # Use the actual top cell name from gLayout (may differ from filename)
        design_name = layout_result.top_cell or Path(result.gds_path).stem

        # Port names for the extracted subcircuit (gLayout opamp_twostage port order)
        pex_ports = list(self.topology._GLAYOUT_PORTS)

        pex_result = self.pex.run(
            gds_path=result.gds_path,
            design_name=design_name,
            work_dir=pex_dir,
            port_names=pex_ports,
        )

        if not pex_result.success:
            result.error = f"PEX failed: {pex_result.error}"
            result.total_time_s = time.monotonic() - t0
            return result

        result.extracted_netlist_path = pex_result.extracted_netlist_path
        result.pex_corner = pex_result.corner
        logger.info("PEX: extracted -> %s (%.1fs)", result.extracted_netlist_path, pex_result.run_time_s)

        # Step 5: Generate post-layout testbench
        logger.info("Step 5/6: Generating post-layout testbench...")
        sim_dir = work_dir / "sim"
        cir_path = self.topology.generate_postlayout_testbench(
            extracted_netlist_path=Path(result.extracted_netlist_path),
            sizing=sizing,
            work_dir=sim_dir,
        )

        # Step 6: Post-layout SPICE simulation
        logger.info("Step 6/6: Running post-layout SPICE simulation...")
        spice_result = self.spice.run(cir_path, work_dir=sim_dir)

        if spice_result.success:
            result.post_Adc_dB = spice_result.Adc_dB
            result.post_GBW_Hz = spice_result.GBW_Hz
            result.post_PM_deg = spice_result.PM_deg
            result.post_fom = self.topology.compute_fom(spice_result, sizing)
            result.post_valid, _ = self.topology.check_validity(spice_result)

            # Compute deltas
            if pre_layout_spice and pre_layout_spice.Adc_dB is not None:
                if spice_result.Adc_dB is not None:
                    result.gain_delta_dB = spice_result.Adc_dB - pre_layout_spice.Adc_dB
                if spice_result.GBW_Hz is not None and pre_layout_spice.GBW_Hz:
                    result.gbw_delta_pct = (
                        (spice_result.GBW_Hz - pre_layout_spice.GBW_Hz)
                        / pre_layout_spice.GBW_Hz
                        * 100
                    )
                if spice_result.PM_deg is not None and pre_layout_spice.PM_deg is not None:
                    result.pm_delta_deg = spice_result.PM_deg - pre_layout_spice.PM_deg

            if pre_layout_fom > 0:
                result.fom_delta_pct = (result.post_fom - pre_layout_fom) / pre_layout_fom * 100

            logger.info(
                "Post-layout SPICE: Adc=%.1fdB, GBW=%.2fMHz, PM=%.1fdeg",
                result.post_Adc_dB or 0,
                (result.post_GBW_Hz or 0) / 1e6,
                result.post_PM_deg or 0,
            )
        else:
            result.error = f"Post-layout SPICE failed: {spice_result.error}"

        result.total_time_s = time.monotonic() - t0
        return result

    def validate_top_n(
        self,
        top_designs: list[dict],
        work_dir: str | Path = "/tmp/postlayout_batch",
    ) -> list[PostLayoutResult]:
        """Validate multiple top designs from an exploration run.

        Parameters
        ----------
        top_designs : list[dict]
            Each dict must have "params", "fom", and optionally "spice_result".
        work_dir : path
            Base working directory (subdirs created per design).

        Returns
        -------
        list[PostLayoutResult]
        """
        work_dir = Path(work_dir)
        results = []

        for i, design in enumerate(top_designs):
            logger.info("Validating design %d/%d...", i + 1, len(top_designs))
            design_dir = work_dir / f"design_{i:03d}"

            result = self.validate(
                params=design["params"],
                pre_layout_fom=design.get("fom", 0.0),
                pre_layout_spice=design.get("spice_result"),
                work_dir=design_dir,
            )
            results.append(result)

        return results
