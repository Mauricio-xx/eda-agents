#!/usr/bin/env python3
"""Unified gm/ID LUT generator for eda-agents.

Wraps the per-PDK generation pipelines that already exist:

  - IHP SG13G2: delegates to the external
    ``ihp-gmid-kit`` (Mauricio-xx/ihp-gmid-kit, Apache 2.0) — the repo
    stays outside this tree. Set ``--kit-path`` or rely on the
    autodetected ``~/personal_exp/ihp-gmid-kit``.
  - GF180MCU: reuses the sibling ``generate_gf180_luts.py`` script
    which speaks to the wafer-space mosplot NgspiceSimulator directly.

The script is idempotent when ``--skip-existing`` is passed: if the
target ``.npz`` already exists it will not re-run the sweep (handy for
CI regenerations and for layered tests).

Usage
-----

    # Both NMOS + PMOS for the active PDK
    python scripts/generate_gmid_lut.py --pdk ihp_sg13g2

    # Only the NFET, skip if already generated
    python scripts/generate_gmid_lut.py \
        --pdk gf180mcu --device nmos --skip-existing

    # Override the output directory
    python scripts/generate_gmid_lut.py \
        --pdk ihp_sg13g2 --output-dir /tmp/iht_luts

The output filenames match the ``lut_nmos_file`` / ``lut_pmos_file``
fields in :mod:`eda_agents.core.pdk` so ``GmIdLookup`` can pick them
up with no further configuration.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path
from typing import Sequence

# Make ``src/eda_agents`` importable when running from a source checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from eda_agents.core.pdk import get_pdk  # noqa: E402


def _autodetect_kit_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
    else:
        path = Path("~/personal_exp/ihp-gmid-kit").expanduser()
    if not path.is_dir():
        raise FileNotFoundError(
            f"ihp-gmid-kit not found at {path}. Pass --kit-path to "
            "point at your clone of Mauricio-xx/ihp-gmid-kit."
        )
    return path


def _lut_output_paths(
    pdk_name: str, output_dir: Path
) -> dict[str, Path]:
    pdk = get_pdk(pdk_name)
    return {
        "nmos": output_dir / pdk.lut_nmos_file,
        "pmos": output_dir / pdk.lut_pmos_file,
    }


def _generate_ihp(
    output_dir: Path,
    device: str,
    n_process: int,
    kit_path: Path,
    skip_existing: bool,
) -> None:
    """Delegate to ihp-gmid-kit's own generator.

    The kit ships a CLI but its ``generate_lookup_tables`` function is
    easiest to drive programmatically. Importing it also gives us a
    clean error when the vendorized mosplot is missing.
    """
    sys.path.insert(0, str(kit_path / "vendor"))
    sys.path.insert(0, str(kit_path / "src"))
    try:
        from ihp_gmid.lookup_generator import (  # type: ignore[import-not-found]
            create_nmos_simulator,
            create_pmos_simulator,
            get_pdk_root,
        )
        from ihp_gmid.sweep_config import (  # type: ignore[import-not-found]
            nmos_sweep,
            pmos_sweep,
        )
        from mosplot.lookup_table_generator import (  # type: ignore[import-not-found]
            LookupTableGenerator,
        )
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            f"ihp-gmid-kit import failed ({exc}). Verify the kit at "
            f"{kit_path} has its vendorized mosplot in place."
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    pdk_root = get_pdk_root()
    outs = _lut_output_paths("ihp_sg13g2", output_dir)

    def _maybe_run(key: str) -> None:
        out_path = outs[key]
        if skip_existing and out_path.exists():
            print(f"[skip] {out_path} already present")
            return

        stem = str(out_path.with_suffix(""))
        if key == "nmos":
            sim = create_nmos_simulator(pdk_root)
            sweep = nmos_sweep
            model = "sg13_lv_nmos"
            desc = "IHP SG13G2 LV NMOS"
        else:
            sim = create_pmos_simulator(pdk_root)
            sweep = pmos_sweep
            model = "sg13_lv_pmos"
            desc = "IHP SG13G2 LV PMOS"

        gen = LookupTableGenerator(
            description=desc,
            simulator=sim,
            model_sweeps={model: sweep},
            n_process=n_process,
        )
        print(f"[gen] {model} -> {out_path}")
        gen.build(stem)

    if device in ("nmos", "both"):
        _maybe_run("nmos")
    if device in ("pmos", "both"):
        _maybe_run("pmos")


def _generate_gf180(
    output_dir: Path,
    device: str,
    n_process: int,
    pdk_root: str | None,
    skip_existing: bool,
) -> None:
    """Reuse scripts/generate_gf180_luts.py's generate() function."""
    sibling = _REPO_ROOT / "scripts" / "generate_gf180_luts.py"
    if not sibling.exists():
        raise FileNotFoundError(
            f"scripts/generate_gf180_luts.py not found at {sibling}"
        )

    # runpy keeps the sibling script self-contained.
    ns = runpy.run_path(str(sibling))
    get_pdk_root_fn = ns["get_pdk_root"]
    create_nmos_simulator = ns["create_nmos_simulator"]
    create_pmos_simulator = ns["create_pmos_simulator"]
    nmos_sweep = ns["nmos_sweep"]
    pmos_sweep = ns["pmos_sweep"]
    LookupTableGenerator = ns["LookupTableGenerator"]

    resolved_root = get_pdk_root_fn(pdk_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    outs = _lut_output_paths("gf180mcu", output_dir)

    def _maybe_run(key: str) -> None:
        out_path = outs[key]
        if skip_existing and out_path.exists():
            print(f"[skip] {out_path} already present")
            return

        stem = str(out_path.with_suffix(""))
        if key == "nmos":
            sim = create_nmos_simulator(resolved_root)
            sweep = nmos_sweep
            model = "nfet_03v3"
            desc = "GF180MCU nfet_03v3"
        else:
            sim = create_pmos_simulator(resolved_root)
            sweep = pmos_sweep
            model = "pfet_03v3"
            desc = "GF180MCU pfet_03v3"

        gen = LookupTableGenerator(
            description=desc,
            simulator=sim,
            model_sweeps={model: sweep},
            n_process=n_process,
        )
        print(f"[gen] {model} -> {out_path}")
        gen.build(stem)

    if device in ("nmos", "both"):
        _maybe_run("nmos")
    if device in ("pmos", "both"):
        _maybe_run("pmos")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate gm/ID lookup tables (.npz) for an eda-agents PDK."
        ),
    )
    parser.add_argument(
        "--pdk", required=True, choices=("ihp_sg13g2", "gf180mcu"),
        help="PDK registry name.",
    )
    parser.add_argument(
        "--device", default="both", choices=("nmos", "pmos", "both"),
        help="Which device(s) to generate (default: both).",
    )
    parser.add_argument(
        "--output-dir", default=None, type=Path,
        help=(
            "Output directory. Defaults to the PDK's "
            "lut_dir_default."
        ),
    )
    parser.add_argument(
        "--n-process", type=int, default=4,
        help="Parallel ngspice processes (default: 4).",
    )
    parser.add_argument(
        "--kit-path", default=None,
        help=(
            "Path to Mauricio-xx/ihp-gmid-kit (IHP only). Defaults to "
            "~/personal_exp/ihp-gmid-kit."
        ),
    )
    parser.add_argument(
        "--pdk-root", default=None,
        help=(
            "PDK root override. Defaults to PDK_ROOT env or the "
            "PdkConfig.default_pdk_root."
        ),
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip device(s) whose target .npz already exists.",
    )
    args = parser.parse_args(argv)

    pdk = get_pdk(args.pdk)
    output_dir = (
        Path(args.output_dir) if args.output_dir
        else Path(pdk.lut_dir_default)
    ).expanduser()

    # PDK_ROOT propagation: both kits read from env, so honour the CLI.
    if args.pdk_root:
        os.environ["PDK_ROOT"] = args.pdk_root

    if args.pdk == "ihp_sg13g2":
        kit_path = _autodetect_kit_path(args.kit_path)
        _generate_ihp(
            output_dir=output_dir,
            device=args.device,
            n_process=args.n_process,
            kit_path=kit_path,
            skip_existing=args.skip_existing,
        )
    else:
        _generate_gf180(
            output_dir=output_dir,
            device=args.device,
            n_process=args.n_process,
            pdk_root=args.pdk_root,
            skip_existing=args.skip_existing,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
