"""Plotting helpers for autoresearch and idea-to-chip loop artefacts.

Reads the canonical persistence emitted by the existing runners and
turns it into PNG figures. The module introduces no new dataclasses;
it consumes the schemas already produced by:

- ``DigitalAutoresearchRunner`` -> ``results.tsv`` with header
  ``eval\\t<param_cols>\\t<measurement_cols>\\tfom\\tvalid\\tstatus``
  (see ``_autoresearch_core.TsvLogger.write_header``). Digital
  measurement columns are
  ``["wns_worst_ns", "cell_count", "die_area_um2", "power_mw",
  "wire_length_um"]``.
- ``run_idea_to_rtl_loop`` -> ``loop_result.json`` matching
  ``IdeaToRTLLoopResult.to_dict()``.

matplotlib is loaded lazily so the rest of the package keeps working
without the ``[plots]`` extra installed. Install via
``pip install -e ".[plots]"``.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    plt = None
    MATPLOTLIB_AVAILABLE = False


_DIGITAL_MEASUREMENT_COLS = [
    "wns_worst_ns",
    "cell_count",
    "die_area_um2",
    "power_mw",
    "wire_length_um",
]


def _lazy_import_matplotlib():
    """Return the pyplot module or raise a clear RuntimeError."""
    if not MATPLOTLIB_AVAILABLE:
        raise RuntimeError(
            "matplotlib not installed; install eda-agents[plots]"
        )
    return plt


def _maybe_float(value: Any) -> float | None:
    """Best-effort float coercion. Empty strings and parse errors return None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_results_tsv(tsv_path: Path) -> dict[str, Any]:
    """Parse ``results.tsv`` into a structured dict.

    Returns
    -------
    dict
        ``{"rows": [...], "param_cols": [...], "measurement_cols": [...]}``.
        ``measurement_cols`` is the subset of
        :data:`_DIGITAL_MEASUREMENT_COLS` actually present in the
        header. ``param_cols`` are the columns between ``eval`` and the
        first known measurement column.
    """
    with open(tsv_path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if reader.fieldnames is None:
            return {"rows": [], "param_cols": [], "measurement_cols": []}
        fieldnames = list(reader.fieldnames)

        if "eval" not in fieldnames:
            return {"rows": [], "param_cols": [], "measurement_cols": []}

        measurement_cols = [
            c for c in _DIGITAL_MEASUREMENT_COLS if c in fieldnames
        ]
        meta_set = {"fom", "valid", "status"}

        if measurement_cols:
            first_meas_idx = min(
                fieldnames.index(c) for c in measurement_cols
            )
        else:
            first_meas_idx = max(
                (fieldnames.index(c) for c in meta_set if c in fieldnames),
                default=len(fieldnames),
            )

        i_eval = fieldnames.index("eval")
        param_cols = [
            c
            for c in fieldnames[i_eval + 1 : first_meas_idx]
            if c not in meta_set and c not in measurement_cols
        ]

        rows: list[dict[str, Any]] = []
        for raw in reader:
            try:
                eval_idx = int(raw["eval"])
            except (KeyError, ValueError, TypeError):
                continue
            row: dict[str, Any] = {
                "eval": eval_idx,
                "fom": _maybe_float(raw.get("fom")),
                "valid": (raw.get("valid", "").strip().lower() == "true"),
                "status": (raw.get("status") or "").strip(),
            }
            for c in param_cols:
                row[c] = _maybe_float(raw.get(c))
            for c in measurement_cols:
                row[c] = _maybe_float(raw.get(c))
            rows.append(row)

    return {
        "rows": rows,
        "param_cols": param_cols,
        "measurement_cols": measurement_cols,
    }


def plot_autoresearch_evolution(
    tsv_path: Path | str,
    output_dir: Path | str,
    *,
    design_label: str | None = None,
    fom_label: str = "FoM",
    show_best_envelope: bool = True,
) -> dict[str, Path]:
    """Emit FoM, metrics, and parameter-evolution plots from a results.tsv.

    Parameters
    ----------
    tsv_path:
        Path to a ``results.tsv`` file produced by
        :class:`DigitalAutoresearchRunner`.
    output_dir:
        Directory to drop PNG files into; created with
        ``parents=True, exist_ok=True``.
    design_label:
        Optional title prefix (e.g. ``"fazyrv_flow"``).
    fom_label:
        Y-axis label for the FoM evolution plot. Default ``"FoM"``.
    show_best_envelope:
        Overlay the best-so-far envelope across valid evaluations.

    Returns
    -------
    dict[str, Path]
        Keys: ``"fom"``, ``"metrics_grid"``, ``"params"``.
    """
    plt_mod = _lazy_import_matplotlib()
    tsv_path = Path(tsv_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed = _read_results_tsv(tsv_path)
    rows = parsed["rows"]
    param_cols = parsed["param_cols"]
    meas_cols = parsed["measurement_cols"]

    if not rows:
        raise ValueError(f"No rows parsed from {tsv_path}")

    title_prefix = f"{design_label}: " if design_label else ""

    fom_path = _plot_fom_evolution(
        plt_mod,
        rows,
        output_dir / "fom_evolution.png",
        title=f"{title_prefix}{fom_label} evolution",
        fom_label=fom_label,
        show_best_envelope=show_best_envelope,
    )
    metrics_path = _plot_metrics_grid(
        plt_mod,
        rows,
        meas_cols,
        output_dir / "metrics_grid.png",
        suptitle=(
            f"{design_label}: metrics evolution"
            if design_label
            else "Metrics evolution"
        ),
    )
    params_path = _plot_params_evolution(
        plt_mod,
        rows,
        param_cols,
        output_dir / "params_evolution.png",
        title=f"{title_prefix}Parameter trajectory",
    )

    return {
        "fom": fom_path,
        "metrics_grid": metrics_path,
        "params": params_path,
    }


def _plot_fom_evolution(
    plt_mod,
    rows: list[dict[str, Any]],
    out_path: Path,
    *,
    title: str,
    fom_label: str,
    show_best_envelope: bool,
) -> Path:
    fig, ax = plt_mod.subplots(figsize=(8, 5))
    evals = [r["eval"] for r in rows]
    foms = [r["fom"] for r in rows]
    valids = [r["valid"] for r in rows]

    valid_x = [e for e, v, f in zip(evals, valids, foms) if v and f is not None]
    valid_y = [f for v, f in zip(valids, foms) if v and f is not None]
    invalid_x = [
        e for e, v, f in zip(evals, valids, foms) if not v and f is not None
    ]
    invalid_y = [f for v, f in zip(valids, foms) if not v and f is not None]

    if valid_x:
        ax.scatter(valid_x, valid_y, c="green", marker="o", label="valid", zorder=3)
    if invalid_x:
        ax.scatter(invalid_x, invalid_y, c="red", marker="x", label="invalid", zorder=3)

    if show_best_envelope and valid_x:
        cur = float("-inf")
        env_x: list[int] = []
        env_y: list[float] = []
        for e, v, f in zip(evals, valids, foms):
            if v and f is not None and f > cur:
                cur = f
            if cur > float("-inf"):
                env_x.append(e)
                env_y.append(cur)
        if env_x:
            ax.plot(env_x, env_y, "g--", alpha=0.6, label="best-so-far")

    ax.set_xlabel("eval")
    ax.set_ylabel(fom_label)
    ax.set_title(title)
    if valid_x or invalid_x:
        ax.legend(loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt_mod.close(fig)
    return out_path


def _plot_metrics_grid(
    plt_mod,
    rows: list[dict[str, Any]],
    meas_cols: list[str],
    out_path: Path,
    *,
    suptitle: str,
) -> Path:
    fig, axes = plt_mod.subplots(2, 2, figsize=(11, 7))
    layout = [
        ("wns_worst_ns", "WNS worst (ns)", axes[0, 0]),
        ("cell_count", "Cell count", axes[0, 1]),
        ("die_area_um2", "Die area (um^2)", axes[1, 0]),
        ("power_mw", "Power total (mW)", axes[1, 1]),
    ]
    evals = [r["eval"] for r in rows]
    valids = [r["valid"] for r in rows]
    for col, label, ax in layout:
        if col not in meas_cols:
            ax.set_visible(False)
            continue
        ys = [r.get(col) for r in rows]
        vx = [e for e, v, y in zip(evals, valids, ys) if v and y is not None]
        vy = [y for v, y in zip(valids, ys) if v and y is not None]
        ix = [e for e, v, y in zip(evals, valids, ys) if not v and y is not None]
        iy = [y for v, y in zip(valids, ys) if not v and y is not None]
        if vx:
            ax.scatter(vx, vy, c="green", marker="o", label="valid", zorder=3)
        if ix:
            ax.scatter(ix, iy, c="red", marker="x", label="invalid", zorder=3)
        ax.set_xlabel("eval")
        ax.set_ylabel(label)
        ax.set_title(label)
        ax.grid(alpha=0.3)
    fig.suptitle(suptitle)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt_mod.close(fig)
    return out_path


def _plot_params_evolution(
    plt_mod,
    rows: list[dict[str, Any]],
    param_cols: list[str],
    out_path: Path,
    *,
    title: str,
) -> Path:
    fig, ax = plt_mod.subplots(figsize=(8, 5))
    evals = [r["eval"] for r in rows]
    if param_cols:
        for col in param_cols:
            ys = [r.get(col) for r in rows]
            ax.plot(evals, ys, marker="o", label=col)
        ax.set_xlabel("eval")
        ax.set_ylabel("parameter value")
        ax.set_title(title)
        ax.legend(loc="best")
        ax.grid(alpha=0.3)
    else:
        ax.text(
            0.5,
            0.5,
            "No design-space parameter columns detected",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt_mod.close(fig)
    return out_path


def plot_idea_loop_evolution(
    loop_result_path: Path | str,
    output_dir: Path | str,
    *,
    design_label: str | None = None,
) -> dict[str, Path]:
    """Read ``loop_result.json`` and emit status + cost plots."""
    loop_result_path = Path(loop_result_path)
    data = json.loads(loop_result_path.read_text())
    return plot_idea_loop_evolution_from_dict(
        data, output_dir, design_label=design_label
    )


def plot_idea_loop_evolution_from_dict(
    loop_result: dict,
    output_dir: Path | str,
    *,
    design_label: str | None = None,
) -> dict[str, Path]:
    """Same as :func:`plot_idea_loop_evolution` taking a dict directly.

    The dict shape is :meth:`IdeaToRTLLoopResult.to_dict`. Only
    ``loop_result["iterations"]`` is consulted; missing keys default
    to safe values so partial logs still plot.
    """
    plt_mod = _lazy_import_matplotlib()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    iterations = loop_result.get("iterations", [])
    if not iterations:
        raise ValueError("loop_result has no iterations")

    title_prefix = f"{design_label}: " if design_label else ""

    status_path = _plot_loop_status(
        plt_mod,
        iterations,
        output_dir / "idea_loop_status.png",
        title=f"{title_prefix}idea-to-chip loop status by turn",
    )
    cost_path = _plot_loop_cost(
        plt_mod,
        iterations,
        output_dir / "idea_loop_cost.png",
        title=f"{title_prefix}idea-to-chip cumulative cost and duration",
    )

    return {"status": status_path, "cost": cost_path}


def _plot_loop_status(
    plt_mod,
    iterations: list[dict],
    out_path: Path,
    *,
    title: str,
) -> Path:
    fig, ax = plt_mod.subplots(figsize=(9, 4.5))
    turns = [it.get("turn", i + 1) for i, it in enumerate(iterations)]
    stages = ["sim_status", "flow_status", "gl_sim_status"]
    stage_labels = {
        "sim_status": "RTL sim",
        "flow_status": "Flow",
        "gl_sim_status": "GL sim",
    }
    status_colors = {
        "pass": "tab:green",
        "fail": "tab:red",
        "skipped": "tab:gray",
        "missing": "tab:orange",
    }
    for i, stage in enumerate(stages):
        for t, it in zip(turns, iterations):
            status = it.get(stage, "missing")
            color = status_colors.get(status, "tab:blue")
            ax.scatter(t, i, c=color, s=180, marker="s", edgecolor="black", zorder=3)

    ax.set_yticks(list(range(len(stages))))
    ax.set_yticklabels([stage_labels[s] for s in stages])
    ax.set_xlabel("turn")
    if turns:
        ax.set_xticks(turns)
    ax.set_title(title)
    legend_handles = [
        plt_mod.Line2D(
            [0],
            [0],
            marker="s",
            color="w",
            markerfacecolor=v,
            markersize=10,
            markeredgecolor="black",
            label=k,
        )
        for k, v in status_colors.items()
    ]
    ax.legend(handles=legend_handles, loc="upper right", ncol=4, fontsize=9)
    ax.grid(axis="x", alpha=0.3)
    ax.set_ylim(-0.5, len(stages) - 0.5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt_mod.close(fig)
    return out_path


def _plot_loop_cost(
    plt_mod,
    iterations: list[dict],
    out_path: Path,
    *,
    title: str,
) -> Path:
    fig, ax_cost = plt_mod.subplots(figsize=(9, 4.5))
    turns = [it.get("turn", i + 1) for i, it in enumerate(iterations)]
    costs = [float(it.get("cost_usd", 0.0) or 0.0) for it in iterations]
    durations = [float(it.get("duration_s", 0.0) or 0.0) for it in iterations]

    cum_cost: list[float] = []
    s = 0.0
    for c in costs:
        s += c
        cum_cost.append(s)

    ax_cost.plot(turns, cum_cost, "b-o", label="cumulative cost (USD)")
    ax_cost.set_xlabel("turn")
    ax_cost.set_ylabel("cumulative cost (USD)", color="b")
    ax_cost.tick_params(axis="y", labelcolor="b")
    if turns:
        ax_cost.set_xticks(turns)
    ax_cost.grid(alpha=0.3)

    ax_dur = ax_cost.twinx()
    ax_dur.bar(turns, durations, alpha=0.3, color="orange", label="duration (s)")
    ax_dur.set_ylabel("duration per turn (s)", color="orange")
    ax_dur.tick_params(axis="y", labelcolor="orange")

    ax_cost.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt_mod.close(fig)
    return out_path


__all__ = [
    "MATPLOTLIB_AVAILABLE",
    "plot_autoresearch_evolution",
    "plot_idea_loop_evolution",
    "plot_idea_loop_evolution_from_dict",
]
