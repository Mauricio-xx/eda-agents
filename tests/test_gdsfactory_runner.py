"""Structural + gated tests for GdsfactoryRunner.

The structural block runs in the default CI gate: it mocks the
subprocess so the runner can be exercised without ``.venv-gdsfactory``
or gdsfactory installed. The gated block runs a real factory inside
``.venv-gdsfactory`` and is selected only when the ``gdsfactory``
marker is enabled (and the venv is provisioned).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from eda_agents.core.gdsfactory_runner import (
    GdsResult,
    GdsfactoryRunner,
    _autodetect_src_root,
)


# ---------------------------------------------------------------------------
# Structural tests (no venv required)
# ---------------------------------------------------------------------------


class TestStructural:
    def test_autodetect_src_root_points_at_worktree_src(self):
        src = _autodetect_src_root()
        assert src is not None
        assert src.name == "src"
        assert (src / "eda_agents" / "core" / "gdsfactory_runner.py").is_file()

    def test_default_driver_script_is_resolvable(self):
        runner = GdsfactoryRunner()
        # importlib.resources resolves the driver relative to the
        # installed package, which works under editable and wheel
        # installs alike. The file must exist on disk.
        assert runner.driver_script.is_file(), (
            f"driver script missing at {runner.driver_script}"
        )

    def test_validate_setup_reports_missing_venv(self, tmp_path):
        runner = GdsfactoryRunner(gdsfactory_venv=tmp_path / "nonexistent-venv")
        problems = runner.validate_setup()
        assert problems
        assert any("not found" in p for p in problems)

    def test_pythonpath_extra_includes_worktree_src(self):
        runner = GdsfactoryRunner()
        assert runner._pythonpath_extra
        assert any(p.endswith("/src") for p in runner._pythonpath_extra)

    def test_env_extra_overrides_into_subprocess_env(self):
        runner = GdsfactoryRunner()
        env = runner._build_env({"PDK_ROOT": "/tmp/some-pdk"})
        assert env["PDK_ROOT"] == "/tmp/some-pdk"
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert "src" in env.get("PYTHONPATH", "")

    def test_generate_component_returns_error_when_venv_python_missing(
        self, tmp_path
    ):
        # No venv: the runner must surface a structured error result
        # rather than raising. This is the contract for callers that
        # want to gracefully fall back when gdsfactory is not set up.
        runner = GdsfactoryRunner(gdsfactory_venv=tmp_path / "nonexistent-venv")
        result = runner.generate_component(
            component_factory="some_module:func",
            params={"x": 1},
            output_dir=tmp_path / "out",
        )
        assert isinstance(result, GdsResult)
        assert not result.success
        assert "venv python not found" in (result.error or "")

    def test_generate_component_parses_driver_success(self, tmp_path):
        # Spoof a subprocess.run that returns a healthy driver JSON
        # line on stdout; the runner must turn that into a populated
        # GdsResult.
        runner = GdsfactoryRunner()

        fake_gds = str(tmp_path / "fake_top.gds")
        Path(fake_gds).write_bytes(b"")
        ok_json = json.dumps({
            "success": True,
            "gds_path": fake_gds,
            "log_path": str(tmp_path / "fake_top.gds.log"),
            "top_cell": "fake_top",
            "lyp_path": None,
            "run_time_s": 0.5,
        })

        with patch.object(
            GdsfactoryRunner, "_build_env", return_value={"PATH": ""}
        ), patch("subprocess.run") as mock_run, patch.object(
            Path, "is_file", return_value=True
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=ok_json + "\n", stderr=""
            )
            result = runner.generate_component(
                component_factory="some_module:func",
                params={"frequency_hz": 60e9},
                output_dir=tmp_path / "out",
            )

        assert result.success, result.error
        assert result.gds_path == fake_gds
        assert result.top_cell == "fake_top"
        assert result.component_factory == "some_module:func"
        assert result.params == {"frequency_hz": 60e9}

    def test_generate_component_handles_driver_error_json(self, tmp_path):
        runner = GdsfactoryRunner()

        err_json = json.dumps({
            "success": False,
            "error": "guard_ring_code import failed",
            "log_path": str(tmp_path / "fake.log"),
        })

        with patch.object(
            GdsfactoryRunner, "_build_env", return_value={"PATH": ""}
        ), patch("subprocess.run") as mock_run, patch.object(
            Path, "is_file", return_value=True
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout=err_json + "\n", stderr=""
            )
            result = runner.generate_component(
                component_factory="some_module:func",
                params={},
                output_dir=tmp_path / "out",
            )

        assert not result.success
        assert "guard_ring_code import failed" in (result.error or "")

    def test_generate_component_falls_back_when_stdout_is_not_json(
        self, tmp_path
    ):
        runner = GdsfactoryRunner()

        with patch.object(
            GdsfactoryRunner, "_build_env", return_value={"PATH": ""}
        ), patch("subprocess.run") as mock_run, patch.object(
            Path, "is_file", return_value=True
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="not even json",
                stderr="ImportError: gdsfactory not available",
            )
            result = runner.generate_component(
                component_factory="some_module:func",
                params={},
                output_dir=tmp_path / "out",
            )

        assert not result.success
        assert "gdsfactory not available" in (result.error or "")

    def test_generate_component_surfaces_timeout(self, tmp_path):
        runner = GdsfactoryRunner(timeout_s=1)

        with patch.object(
            GdsfactoryRunner, "_build_env", return_value={"PATH": ""}
        ), patch("subprocess.run") as mock_run, patch.object(
            Path, "is_file", return_value=True
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=1)
            result = runner.generate_component(
                component_factory="some_module:func",
                params={},
                output_dir=tmp_path / "out",
            )

        assert not result.success
        assert "timed out" in (result.error or "")


# ---------------------------------------------------------------------------
# Gated tests (require .venv-gdsfactory)
# ---------------------------------------------------------------------------


_WORKTREE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_VENV = _WORKTREE_ROOT / ".venv-gdsfactory"


@pytest.mark.gdsfactory
@pytest.mark.skipif(
    not (_DEFAULT_VENV / "bin" / "python").is_file(),
    reason=(
        f"gdsfactory venv not provisioned at {_DEFAULT_VENV}. "
        "See docs/sparx_rf_pdk_variants.md for the native bring-up."
    ),
)
class TestGdsfactoryIntegration:
    def test_generate_empty_component_writes_gds(self, tmp_path):
        """End-to-end smoke against the real .venv-gdsfactory."""
        runner = GdsfactoryRunner(gdsfactory_venv=str(_DEFAULT_VENV))
        problems = runner.validate_setup()
        if problems:
            pytest.skip(f"gdsfactory venv not ready: {problems!r}")

        # Tiny self-contained factory: builds an empty gdsfactory
        # Component. Lives as a helper string we feed to the driver
        # via a small adapter module on the runner's PYTHONPATH.
        adapter = tmp_path / "gf_smoke_factory.py"
        adapter.write_text(
            "import gdsfactory as gf\n"
            "def make_empty(name='smoke_top'):\n"
            "    c = gf.Component()\n"
            "    c.name = name\n"
            "    return c\n"
        )
        runner = GdsfactoryRunner(
            gdsfactory_venv=str(_DEFAULT_VENV),
            pythonpath_extra=[str(tmp_path)],
        )
        result = runner.generate_component(
            component_factory="gf_smoke_factory:make_empty",
            params={"name": "smoke_top"},
            output_dir=tmp_path / "out",
            output_gds_name="smoke_top.gds",
        )
        assert result.success, result.error
        assert Path(result.gds_path).is_file()
        assert (tmp_path / "out" / "smoke_top.gds.log").is_file()
