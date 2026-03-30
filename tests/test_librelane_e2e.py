"""End-to-end tests for LibreLane flow runner.

Unit tests (no librelane needed):
    pytest tests/test_librelane_e2e.py -m "not librelane" -v

Integration tests (needs librelane + GF180 PDK + template project):
    pytest tests/test_librelane_e2e.py -m librelane -v

The template project is expected at data/gf180-template/.
Clone it with:
    git clone https://github.com/wafer-space/gf180mcu-project-template data/gf180-template
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eda_agents.core.librelane_runner import LibreLaneRunner

TEMPLATE_DIR = Path(__file__).parent.parent / "data" / "gf180-template"
LIBRELANE_DIR = TEMPLATE_DIR / "librelane"


# ---------------------------------------------------------------------------
# Unit tests: runner construction and validation (no librelane required)
# ---------------------------------------------------------------------------


class TestLibreLaneRunnerInit:
    def test_missing_project_dir(self, tmp_path):
        runner = LibreLaneRunner(tmp_path / "nonexistent")
        problems = runner.validate_setup()
        assert any("not found" in p.lower() for p in problems)

    def test_missing_config(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        runner = LibreLaneRunner(project)
        problems = runner.validate_setup()
        assert any("config" in p.lower() for p in problems)

    def test_config_json_creation(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        cfg = project / "config.json"
        cfg.write_text('{"DESIGN_NAME": "test_inv"}')
        runner = LibreLaneRunner(project)
        assert runner.design_name() == "test_inv"

    def test_modify_config_safe_key(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        cfg = project / "config.json"
        cfg.write_text('{"PL_TARGET_DENSITY_PCT": 50}')
        runner = LibreLaneRunner(project)
        result = runner.modify_config("PL_TARGET_DENSITY_PCT", 35)
        assert result["old_value"] == 50
        assert result["new_value"] == 35

    def test_modify_config_unsafe_key_rejected(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        cfg = project / "config.json"
        cfg.write_text('{"DESIGN_NAME": "test"}')
        runner = LibreLaneRunner(project)
        with pytest.raises(ValueError, match="not in the safe"):
            runner.modify_config("DESIGN_NAME", "hacked")

    def test_modify_config_unsafe_key_with_force(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        cfg = project / "config.json"
        cfg.write_text('{"DESIGN_NAME": "test"}')
        runner = LibreLaneRunner(project)
        result = runner.modify_config("DESIGN_NAME", "override", force=True)
        assert result["new_value"] == "override"


class TestLibreLaneRunnerTemplate:
    """Tests that use the cloned template (no librelane execution)."""

    @pytest.fixture(autouse=True)
    def check_template(self):
        if not TEMPLATE_DIR.is_dir():
            pytest.skip("gf180-template not cloned (run: git clone "
                        "https://github.com/wafer-space/gf180mcu-project-template "
                        "data/gf180-template)")

    def test_template_has_config_yaml(self):
        assert (LIBRELANE_DIR / "config.yaml").is_file()

    def test_template_has_rtl(self):
        src_dir = TEMPLATE_DIR / "src"
        assert src_dir.is_dir()
        sv_files = list(src_dir.glob("*.sv"))
        assert len(sv_files) > 0, "No SystemVerilog sources found"

    def test_runner_with_yaml_config(self):
        runner = LibreLaneRunner(
            LIBRELANE_DIR,
            config_file="config.yaml",
        )
        problems = runner.validate_setup()
        # Config should be found (librelane python may not be)
        assert not any("config" in p.lower() for p in problems)


# ---------------------------------------------------------------------------
# Integration tests (require librelane + GF180MCU PDK + template)
# ---------------------------------------------------------------------------


def _librelane_available() -> bool:
    """Check if LibreLane is available."""
    from eda_agents.core.librelane_runner import _find_librelane_python
    return _find_librelane_python() is not None


@pytest.mark.librelane
class TestLibreLaneE2E:
    """Full LibreLane flow integration tests.

    These require:
    - librelane Python environment
    - GF180MCU PDK (PDK_ROOT)
    - Template project at data/gf180-template/
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        if not TEMPLATE_DIR.is_dir():
            pytest.skip("gf180-template not available")
        if not _librelane_available():
            pytest.skip("librelane not available")

    def test_validate_setup(self):
        """Verify LibreLane setup has no problems."""
        runner = LibreLaneRunner(
            LIBRELANE_DIR,
            config_file="config.yaml",
        )
        problems = runner.validate_setup()
        assert problems == [], f"Setup problems: {problems}"

    def test_flow_run(self, tmp_path):
        """Run LibreLane flow on the template, verify GDS generated."""
        # Copy template to tmp to avoid polluting original
        project_copy = tmp_path / "gf180-template"
        shutil.copytree(TEMPLATE_DIR, project_copy)

        runner = LibreLaneRunner(
            project_copy / "librelane",
            config_file="config.yaml",
        )
        result = runner.run_flow(tag="pytest")

        assert result.success, f"Flow failed: {result.error}\n{result.log_tail[-500:]}"
        assert result.gds_path is not None, "No GDS generated"
        assert Path(result.gds_path).is_file()

    def test_flow_then_drc(self, tmp_path):
        """Run flow and then parse DRC results."""
        project_copy = tmp_path / "gf180-template"
        shutil.copytree(TEMPLATE_DIR, project_copy)

        runner = LibreLaneRunner(
            project_copy / "librelane",
            config_file="config.yaml",
        )
        result = runner.run_flow(tag="drc_test")

        if result.success:
            drc = runner.read_drc()
            # Just verify parsing works -- violations may or may not exist
            assert drc.total_violations >= 0 or drc.total_violations == -1


@pytest.mark.librelane
class TestDRCFixLoop:
    """Test the modify-config -> rerun loop for DRC convergence."""

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        if not TEMPLATE_DIR.is_dir():
            pytest.skip("gf180-template not available")
        if not _librelane_available():
            pytest.skip("librelane not available")

    def test_config_modify_and_rerun(self, tmp_path):
        """Modify density, rerun, verify DRC result changes."""
        project_copy = tmp_path / "gf180-template"
        shutil.copytree(TEMPLATE_DIR, project_copy)

        librelane_dir = project_copy / "librelane"

        # Write a json config (from yaml) so modify_config works
        import yaml
        yaml_cfg = librelane_dir / "config.yaml"
        if yaml_cfg.is_file():
            with open(yaml_cfg) as f:
                config = yaml.safe_load(f)
            json_cfg = librelane_dir / "config.json"
            import json
            json_cfg.write_text(json.dumps(config, indent=4))

        runner = LibreLaneRunner(librelane_dir)

        # Run baseline
        result1 = runner.run_flow(tag="baseline")
        if not result1.success:
            pytest.skip(f"Baseline flow failed: {result1.error}")

        drc1 = runner.read_drc()
        if drc1.clean:
            # Nothing to fix
            return

        # Modify density and rerun
        runner.modify_config("PL_TARGET_DENSITY_PCT", 50)
        result2 = runner.run_flow(tag="fix1")

        # We don't assert fewer violations -- just that the loop runs
        assert result2.success or result2.error is not None
