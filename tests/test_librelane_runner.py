"""Tests for LibreLaneRunner.

Unit tests that don't require librelane or KLayout installed.
Integration tests (marked @pytest.mark.librelane) require a real project.
"""

import json
import pytest
import yaml
from unittest.mock import patch, MagicMock

from eda_agents.core.librelane_runner import (
    LibreLaneRunner,
    SAFE_CONFIG_KEYS,
)


_SAMPLE_CONFIG = {
    "meta": {
        "version": 2,
        "flow": ["Yosys.Synthesis", "OpenROAD.Floorplan"],
    },
    "DESIGN_NAME": "test_design",
    "VERILOG_FILES": "dir::src/*.v",
    "CLOCK_PORT": None,
    "PL_TARGET_DENSITY_PCT": 75,
    "FP_PDN_VPITCH": 25,
    "FP_PDN_HPITCH": 25,
    "DIE_AREA": [0, 0, 50, 50],
}


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory with config.json."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_SAMPLE_CONFIG, indent=4))
    return tmp_path


@pytest.fixture
def yaml_project_dir(tmp_path):
    """Create a minimal project directory with config.yaml."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(_SAMPLE_CONFIG, default_flow_style=False))
    return tmp_path


class TestSetup:
    def test_validate_good_project(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        problems = runner.validate_setup()
        # May have "no python with librelane" but not "dir not found"
        assert not any("not found" in p and "directory" in p.lower() for p in problems)

    def test_validate_missing_dir(self, tmp_path):
        runner = LibreLaneRunner(
            tmp_path / "nonexistent", python_cmd="python3"
        )
        problems = runner.validate_setup()
        assert any("not found" in p for p in problems)

    def test_validate_missing_config(self, tmp_path):
        runner = LibreLaneRunner(tmp_path, python_cmd="python3")
        problems = runner.validate_setup()
        assert any("Config not found" in p for p in problems)


class TestConfigModification:
    def test_modify_safe_key(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.modify_config("PL_TARGET_DENSITY_PCT", 60)
        assert result["old_value"] == 75
        assert result["new_value"] == 60

        # Verify written
        config = json.loads((project_dir / "config.json").read_text())
        assert config["PL_TARGET_DENSITY_PCT"] == 60

    def test_modify_unsafe_key_rejected(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        with pytest.raises(ValueError, match="not in the safe"):
            runner.modify_config("DESIGN_NAME", "hacked")

    def test_modify_unsafe_key_forced(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.modify_config("CUSTOM_KEY", "value", force=True)
        assert result["new_value"] == "value"
        assert result["old_value"] is None

    def test_modify_new_key(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.modify_config("GRT_ALLOW_CONGESTION", True)
        assert result["old_value"] is None
        assert result["new_value"] is True

    def test_safe_keys_exist(self):
        assert "PL_TARGET_DENSITY_PCT" in SAFE_CONFIG_KEYS
        assert "CLOCK_PERIOD" in SAFE_CONFIG_KEYS
        assert "PDN_VPITCH" in SAFE_CONFIG_KEYS
        assert "GRT_ANTENNA_REPAIR_ITERS" in SAFE_CONFIG_KEYS
        assert "DESIGN_NAME" not in SAFE_CONFIG_KEYS
        # v3 removals: these should NOT be in the set
        assert "FP_PDN_VPITCH" not in SAFE_CONFIG_KEYS
        assert "QUIT_ON_TIMING_VIOLATIONS" not in SAFE_CONFIG_KEYS
        assert "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT" not in SAFE_CONFIG_KEYS
        assert "GRT_ANT_ITERS" not in SAFE_CONFIG_KEYS
        assert "RCX_RULES" not in SAFE_CONFIG_KEYS


class TestYamlConfig:
    """Verify config read/write works with YAML files."""

    def test_read_yaml_config(self, yaml_project_dir):
        runner = LibreLaneRunner(
            yaml_project_dir, config_file="config.yaml", python_cmd="python3"
        )
        config = runner._read_config()
        assert config["DESIGN_NAME"] == "test_design"
        assert config["PL_TARGET_DENSITY_PCT"] == 75

    def test_modify_yaml_config(self, yaml_project_dir):
        runner = LibreLaneRunner(
            yaml_project_dir, config_file="config.yaml", python_cmd="python3"
        )
        result = runner.modify_config("PL_TARGET_DENSITY_PCT", 60)
        assert result["old_value"] == 75
        assert result["new_value"] == 60

        # Verify YAML was written (not JSON)
        config_path = yaml_project_dir / "config.yaml"
        text = config_path.read_text()
        assert "PL_TARGET_DENSITY_PCT: 60" in text
        # Round-trip: still readable
        reloaded = yaml.safe_load(text)
        assert reloaded["PL_TARGET_DENSITY_PCT"] == 60

    def test_yaml_detected_by_extension(self, yaml_project_dir):
        runner = LibreLaneRunner(
            yaml_project_dir, config_file="config.yaml", python_cmd="python3"
        )
        assert runner._is_yaml

    def test_json_not_yaml(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        assert not runner._is_yaml

    def test_yml_extension(self, tmp_path):
        config_path = tmp_path / "config.yml"
        config_path.write_text(yaml.dump(_SAMPLE_CONFIG))
        runner = LibreLaneRunner(
            tmp_path, config_file="config.yml", python_cmd="python3"
        )
        assert runner._is_yaml
        config = runner._read_config()
        assert config["DESIGN_NAME"] == "test_design"


class TestDesignName:
    def test_read_design_name(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        assert runner.design_name() == "test_design"

    def test_missing_design_name(self, tmp_path):
        runner = LibreLaneRunner(tmp_path, python_cmd="python3")
        assert runner.design_name() is None


class TestShellWrapper:
    """Verify nix-shell wrapper is applied to commands."""

    @patch("subprocess.run")
    def test_shell_wrapper_wraps_command(self, mock_run, project_dir):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Flow completed\n", stderr="",
        )
        run_dir = project_dir / "runs" / "test_run"
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True)
        (final_dir / "test_design.gds").write_bytes(b"GDS")

        runner = LibreLaneRunner(
            project_dir,
            python_cmd="python3",
            shell_wrapper="nix-shell /path/to/project --run",
        )
        runner.run_flow(tag="test_run")

        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("cmd", [])
        # First 4 args are the wrapper
        assert cmd[0] == "nix-shell"
        assert cmd[1] == "/path/to/project"
        assert cmd[2] == "--run"
        # Last arg is the wrapped command string
        assert "python3 -m librelane" in cmd[3]
        assert "--run-tag test_run" in cmd[3]

    def test_no_wrapper_by_default(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        assert runner.shell_wrapper is None

    def test_wrapper_defaults_python(self, project_dir):
        runner = LibreLaneRunner(
            project_dir, shell_wrapper="nix-shell . --run",
        )
        assert runner.python_cmd == "python3"


class TestRunFlow:
    def test_missing_python(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd=None)
        runner.python_cmd = None
        result = runner.run_flow()
        assert not result.success
        assert "No Python" in result.error

    def test_missing_config(self, tmp_path):
        runner = LibreLaneRunner(tmp_path, python_cmd="python3")
        result = runner.run_flow()
        assert not result.success
        assert "Config not found" in result.error

    @patch("subprocess.run")
    def test_successful_flow(self, mock_run, project_dir):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Flow completed successfully\nNo timing violations\n",
            stderr="",
        )

        # Create a fake run directory with GDS
        run_dir = project_dir / "runs" / "test_run"
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True)
        (final_dir / "test_design.gds").write_bytes(b"GDS")

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.run_flow(tag="test_run")

        assert result.success
        assert result.gds_path is not None
        assert "test_design.gds" in result.gds_path

    @patch("subprocess.run")
    def test_failed_flow(self, mock_run, project_dir):
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout="",
            stderr="Error: synthesis failed for test_design\n",
        )

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.run_flow()

        assert not result.success
        assert result.error is not None


class TestRunDir:
    def test_find_latest_run(self, project_dir):
        import time

        runs_dir = project_dir / "runs"
        (runs_dir / "old_run").mkdir(parents=True)
        time.sleep(0.01)
        (runs_dir / "new_run").mkdir(parents=True)

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        latest = runner.latest_run_dir()
        assert latest is not None
        assert latest.name == "new_run"

    def test_find_tagged_run(self, project_dir):
        runs_dir = project_dir / "runs"
        (runs_dir / "my_tag").mkdir(parents=True)

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        found = runner._find_run_dir("my_tag")
        assert found is not None
        assert found.name == "my_tag"

    def test_no_runs(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        assert runner.latest_run_dir() is None
        assert runner.latest_gds() is None


class TestArtifacts:
    def test_find_gds_in_final(self, project_dir):
        run_dir = project_dir / "runs" / "run1" / "final"
        run_dir.mkdir(parents=True)
        (run_dir / "chip.gds").write_bytes(b"GDS")

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        gds = runner.latest_gds()
        assert gds is not None
        assert gds.name == "chip.gds"

    def test_find_gds_recursive(self, project_dir):
        run_dir = project_dir / "runs" / "run1" / "step5" / "output"
        run_dir.mkdir(parents=True)
        (run_dir / "chip.gds").write_bytes(b"GDS")

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        gds = runner.latest_gds()
        assert gds is not None


class TestDRCParsing:
    def test_no_run_dir(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.read_drc()
        assert result.total_violations == -1

    def test_lyrdb_parsing(self, project_dir):
        run_dir = project_dir / "runs" / "run1"
        run_dir.mkdir(parents=True)

        lyrdb_content = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
  <description/>
  <original-file/>
  <generator/>
  <top-cell/>
  <tags/>
  <categories/>
  <cells/>
  <items>
    <item>
      <category>'M1.S.1'</category>
      <cell/>
      <visited>false</visited>
      <multiplicity>1</multiplicity>
    </item>
    <item>
      <category>'M1.S.1'</category>
      <cell/>
      <visited>false</visited>
      <multiplicity>1</multiplicity>
    </item>
    <item>
      <category>'M2.W.1'</category>
      <cell/>
      <visited>false</visited>
      <multiplicity>1</multiplicity>
    </item>
  </items>
</report-database>"""
        (run_dir / "drc_report.lyrdb").write_text(lyrdb_content)

        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.read_drc(run_dir)

        assert result.total_violations == 3
        assert result.violated_rules["M1.S.1"] == 2
        assert result.violated_rules["M2.W.1"] == 1
        assert not result.clean


class TestTimingParsing:
    def test_no_run_dir(self, project_dir):
        runner = LibreLaneRunner(project_dir, python_cmd="python3")
        result = runner.read_timing()
        assert "error" in result
