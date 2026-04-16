"""Layout operations facade.

The bridge does NOT reimplement DRC / LVS / PEX. It delegates to the
existing runners in ``eda_agents.core.klayout_drc`` / ``klayout_lvs`` /
``magic_pex`` and wraps their dataclass results in ``BridgeResult`` so
the JobRegistry can persist them and the CLI can print them uniformly.

This is the open-source counterpart to the ``leHi*`` SKILL helpers in
``virtuoso-bridge-lite/virtuoso/skill_helpers.py`` — same intent (a
single Python facade for layout-side ops), implemented with KLayout
instead of Cadence Virtuoso. We import the existing core runners; we
do not depend on any virtuoso-bridge-lite code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from eda_agents.bridge.models import BridgeResult, ExecutionStatus
from eda_agents.core.klayout_drc import (
    DEFAULT_VARIANT as DRC_DEFAULT_VARIANT,
    KLayoutDrcRunner,
)
from eda_agents.core.klayout_lvs import (
    DEFAULT_VARIANT as LVS_DEFAULT_VARIANT,
    KLayoutLvsRunner,
)

logger = logging.getLogger(__name__)


class KLayoutOps:
    """Facade for layout-side operations.

    Parameters
    ----------
    pdk_root : str, optional
        PDK root forwarded to the underlying runners.
    drc_variant / lvs_variant : str
        GF180MCU PDK variant. Default "C" matches the underlying runners.
    timeout_s : int
        Per-call hard timeout. Default 600 s.
    drc_runner / lvs_runner : runner instances, optional
        Inject pre-built runners (used in tests). When omitted the
        facade builds one ``KLayoutDrcRunner`` / ``KLayoutLvsRunner``
        per instance.
    """

    def __init__(
        self,
        pdk_root: str | None = None,
        drc_variant: str = DRC_DEFAULT_VARIANT,
        lvs_variant: str = LVS_DEFAULT_VARIANT,
        timeout_s: int = 600,
        drc_runner: KLayoutDrcRunner | None = None,
        lvs_runner: KLayoutLvsRunner | None = None,
    ) -> None:
        self.pdk_root = pdk_root
        self._drc_runner = drc_runner or KLayoutDrcRunner(
            pdk_root=pdk_root, variant=drc_variant, timeout_s=timeout_s
        )
        self._lvs_runner = lvs_runner or KLayoutLvsRunner(
            pdk_root=pdk_root, variant=lvs_variant, timeout_s=timeout_s
        )

    # -- DRC ------------------------------------------------------------------

    def run_drc(
        self,
        gds_path: str | Path,
        run_dir: str | Path,
        top_cell: str | None = None,
        table: str | Sequence[str] | None = None,
        mp: int = 1,
    ) -> BridgeResult:
        """Run KLayout DRC and wrap the result."""
        result = self._drc_runner.run(
            gds_path=gds_path,
            run_dir=run_dir,
            top_cell=top_cell,
            table=table,
            mp=mp,
        )
        if not result.success:
            return BridgeResult(
                status=ExecutionStatus.ERROR,
                tool="klayout-drc",
                output=result.summary,
                errors=[result.error] if result.error else [],
                duration_s=result.run_time_s,
                artifacts=list(result.report_paths),
                metadata={
                    "violated_rules": dict(result.violated_rules),
                    "total_violations": result.total_violations,
                    "clean": result.clean,
                },
            )
        if result.clean:
            status = ExecutionStatus.SUCCESS
        else:
            status = ExecutionStatus.PARTIAL
        return BridgeResult(
            status=status,
            tool="klayout-drc",
            output=result.summary,
            duration_s=result.run_time_s,
            artifacts=list(result.report_paths),
            metadata={
                "violated_rules": dict(result.violated_rules),
                "total_violations": result.total_violations,
                "clean": result.clean,
            },
        )

    # -- LVS ------------------------------------------------------------------

    def run_lvs(
        self,
        gds_path: str | Path,
        netlist_path: str | Path,
        run_dir: str | Path,
        top_cell: str | None = None,
    ) -> BridgeResult:
        """Run KLayout LVS and wrap the result.

        Honours the IHP blocker: callers must skip this call entirely on
        IHP until upstream fixes the deck (``RUN_LVS: false``). The
        facade does NOT auto-skip — that decision belongs to the caller
        because it depends on PDK selection.
        """
        runner = self._lvs_runner
        # The runner signature varies slightly across PDKs; call it via
        # the same arguments the existing core runner exposes.
        result = runner.run(
            gds_path=gds_path,
            netlist_path=netlist_path,
            run_dir=run_dir,
            top_cell=top_cell,
        )
        if not result.success:
            return BridgeResult(
                status=ExecutionStatus.ERROR,
                tool="klayout-lvs",
                output=result.summary,
                errors=[result.error] if result.error else [],
                duration_s=result.run_time_s,
                artifacts=[
                    p for p in (result.extracted_netlist_path, result.report_path) if p
                ],
                metadata={
                    "match": result.match,
                    "stdout_tail": (result.stdout_tail or "")[-1500:],
                },
            )
        status = (
            ExecutionStatus.SUCCESS if result.match else ExecutionStatus.FAILURE
        )
        return BridgeResult(
            status=status,
            tool="klayout-lvs",
            output=result.summary,
            duration_s=result.run_time_s,
            artifacts=[
                p for p in (result.extracted_netlist_path, result.report_path) if p
            ],
            metadata={"match": result.match},
        )

    # -- introspection --------------------------------------------------------

    def validate_setup(self) -> dict[str, list[str]]:
        """Aggregate ``validate_setup`` output from both wrapped runners."""
        return {
            "drc": list(self._drc_runner.validate_setup()),
            "lvs": list(self._lvs_runner.validate_setup()),
        }


__all__ = ["KLayoutOps"]
