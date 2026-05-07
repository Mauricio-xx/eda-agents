"""Tests for ``eda_agents.utils.plot_autoresearch``.

Gated by the ``plots`` marker so CI runs without matplotlib stay
fast. Each test uses ``pytest.importorskip("matplotlib")`` so the
suite skips cleanly when the optional ``[plots]`` extra is absent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.plots


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_tsv(tmp_path: Path) -> Path:
    pytest.importorskip("matplotlib")
    src = FIXTURES_DIR / "autoresearch_results_sample.tsv"
    dst = tmp_path / "results.tsv"
    shutil.copyfile(src, dst)
    return dst


@pytest.fixture
def sample_loop_json(tmp_path: Path) -> Path:
    pytest.importorskip("matplotlib")
    src = FIXTURES_DIR / "loop_result_sample.json"
    dst = tmp_path / "loop_result.json"
    shutil.copyfile(src, dst)
    return dst


def test_plot_autoresearch_evolution_creates_pngs(
    tmp_path: Path, sample_tsv: Path
) -> None:
    pytest.importorskip("matplotlib")
    from eda_agents.utils import plot_autoresearch as plot_mod

    out_dir = tmp_path / "plots"
    paths = plot_mod.plot_autoresearch_evolution(
        sample_tsv, out_dir, design_label="fazyrv_flow"
    )

    assert set(paths.keys()) == {"fom", "metrics_grid", "params"}
    for key, p in paths.items():
        assert p.is_file(), f"{key} plot missing at {p}"
        assert p.stat().st_size > 0, f"{key} plot is empty"


def test_plot_idea_loop_evolution_creates_pngs(
    tmp_path: Path, sample_loop_json: Path
) -> None:
    pytest.importorskip("matplotlib")
    from eda_agents.utils import plot_autoresearch as plot_mod

    out_dir = tmp_path / "loop_plots"
    paths = plot_mod.plot_idea_loop_evolution(
        sample_loop_json, out_dir, design_label="demo_counter4"
    )

    assert set(paths.keys()) == {"status", "cost"}
    for key, p in paths.items():
        assert p.is_file(), f"{key} plot missing at {p}"
        assert p.stat().st_size > 0, f"{key} plot is empty"


def test_plot_idea_loop_evolution_from_dict(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from eda_agents.utils import plot_autoresearch as plot_mod

    sample = json.loads(
        (FIXTURES_DIR / "loop_result_sample.json").read_text()
    )
    out_dir = tmp_path / "from_dict"
    paths = plot_mod.plot_idea_loop_evolution_from_dict(
        sample, out_dir, design_label="inline_dict"
    )
    assert paths["status"].is_file()
    assert paths["cost"].is_file()


def test_missing_matplotlib_raises(
    tmp_path: Path, monkeypatch, sample_loop_json: Path
) -> None:
    pytest.importorskip("matplotlib")
    from eda_agents.utils import plot_autoresearch as plot_mod

    monkeypatch.setattr(plot_mod, "MATPLOTLIB_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="matplotlib not installed"):
        plot_mod.plot_idea_loop_evolution(sample_loop_json, tmp_path / "nope")


def test_empty_tsv_raises(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from eda_agents.utils import plot_autoresearch as plot_mod

    empty = tmp_path / "empty.tsv"
    empty.write_text(
        "eval\tPL_TARGET_DENSITY_PCT\twns_worst_ns\tfom\tvalid\tstatus\n"
    )
    with pytest.raises(ValueError, match="No rows parsed"):
        plot_mod.plot_autoresearch_evolution(empty, tmp_path / "out")
