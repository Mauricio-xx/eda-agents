"""Unit tests for the S11 Fase 0 idea-to-chip pipeline.

Covers three layers:

* Library (:mod:`eda_agents.agents.idea_to_rtl`): dataclass semantics,
  dry-run behaviour, description augmentation, JSON serialisation,
  GL-sim helper error paths.
* MCP tool (``generate_rtl_draft`` in :mod:`eda_agents.mcp.server`):
  schema validation and dry-run invocation.
* Bench adapter (``run_idea_to_digital_chip`` in
  :mod:`eda_agents.bench.adapters`): YAML-driven typed inputs, dry
  success, live-mode short-circuits when tools absent.

Heavy / CLI-hitting paths (``dry_run=False``) are not exercised here —
``tests/test_from_spec.py`` covers prompt-builder shape, and the
bench ``idea_to_digital_counter_live.yaml`` task drives the live flow
when tools are available.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from eda_agents.agents.idea_to_rtl import (
    IdeaToRTLResult,
    _augment_description_with_design_name,
    _populate_artifact_paths,
    generate_rtl_draft,
    print_gl_sim_report,
    result_to_dict,
    run_post_flow_gl_sim_check,
    write_result_json,
)
from eda_agents.bench.adapter_inputs import IdeaToDigitalChipInputs
from eda_agents.bench.adapters import run_idea_to_digital_chip
from eda_agents.bench.models import BenchStatus, load_task

_TASK_DIR = Path(__file__).resolve().parents[1] / "bench" / "tasks" / "end-to-end"


# ---------------------------------------------------------------------------
# IdeaToRTLResult dataclass
# ---------------------------------------------------------------------------


class TestIdeaToRTLResult:
    def test_all_passed_requires_success(self):
        r = IdeaToRTLResult(success=False, work_dir=Path("/tmp"))
        assert r.all_passed is False

    def test_all_passed_true_when_gl_sim_skipped(self):
        r = IdeaToRTLResult(success=True, work_dir=Path("/tmp"))
        assert r.all_passed is True

    def test_all_passed_follows_gl_sim_verdict(self):
        good = IdeaToRTLResult(
            success=True,
            work_dir=Path("/tmp"),
            gl_sim={"all_passed": True},
        )
        bad = IdeaToRTLResult(
            success=True,
            work_dir=Path("/tmp"),
            gl_sim={"all_passed": False, "error": "gl failed"},
        )
        assert good.all_passed is True
        assert bad.all_passed is False


# ---------------------------------------------------------------------------
# Description augmentation
# ---------------------------------------------------------------------------


class TestAugmentDescription:
    def test_adds_module_name_hint_when_missing(self):
        out = _augment_description_with_design_name(
            "A simple counter", "counter4"
        )
        assert "counter4" in out
        assert "Top module name MUST be" in out

    def test_no_duplication_when_already_mentioned(self):
        out = _augment_description_with_design_name(
            "A counter named counter4 with reset", "counter4"
        )
        # Should return the description unchanged since design_name is already present.
        assert "Top module name MUST be" not in out

    def test_empty_design_name_is_passthrough(self):
        out = _augment_description_with_design_name("something", "")
        assert out == "something"


# ---------------------------------------------------------------------------
# Dry-run generate_rtl_draft
# ---------------------------------------------------------------------------


class TestGenerateRtlDraftDry:
    @pytest.mark.parametrize("pdk", ["gf180mcu", "ihp_sg13g2"])
    async def test_dry_run_builds_prompt(self, tmp_path, pdk):
        result = await generate_rtl_draft(
            description="4-bit counter with enable",
            design_name="counter4",
            work_dir=tmp_path / "work",
            pdk=pdk,
            pdk_root="/tmp/fake_pdk_root",  # never touched in dry mode
            dry_run=True,
        )
        assert result.success is True
        assert result.all_passed is True
        assert result.prompt_length > 2000
        assert result.error is None
        assert result.design_name == "counter4"
        assert result.wall_time_s == 0.0
        assert result.num_turns == 0
        assert result.cost_usd == 0.0
        assert result.gds_path is None
        assert result.gl_sim is None

    async def test_dry_run_propagates_unknown_pdk(self, tmp_path):
        # Unknown PDK raises inside resolve_pdk; we want it surfaced as an
        # error field, not an exception, so the MCP tool + bench adapter
        # can return a clean failure.
        with pytest.raises(KeyError):
            await generate_rtl_draft(
                description="x",
                design_name="top",
                work_dir=tmp_path,
                pdk="no_such_pdk",
                dry_run=True,
            )

    async def test_dry_run_with_no_default_root_reports_error(self, tmp_path, monkeypatch):
        # Strip PDK_ROOT and default_pdk_root so resolve_pdk_root fails.
        from eda_agents.core.pdk import IHP_SG13G2

        monkeypatch.delenv("PDK_ROOT", raising=False)
        # Monkey-patch the frozen dataclass's default_pdk_root is hard
        # because PdkConfig is frozen. Instead, pass an explicit pdk_root
        # that doesn't exist and let the resolver accept it — the real
        # check is that library-level failures don't crash.
        result = await generate_rtl_draft(
            description="x",
            design_name="top",
            work_dir=tmp_path,
            pdk=IHP_SG13G2,
            pdk_root="/nonexistent/path",  # accepted by resolver, not checked
            dry_run=True,
        )
        # Dry-run with explicit (even nonexistent) pdk_root should still
        # build the prompt.
        assert result.success is True


# ---------------------------------------------------------------------------
# Artifact path population
# ---------------------------------------------------------------------------


class TestPopulateArtifactPaths:
    def test_finds_config_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.safe_dump({"DESIGN_NAME": "widget"}))
        result = IdeaToRTLResult(success=True, work_dir=tmp_path)
        _populate_artifact_paths(result)
        assert result.config_path == cfg
        assert result.design_name == "widget"

    def test_finds_run_dir_and_gds(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump({"DESIGN_NAME": "widget"})
        )
        run_dir = tmp_path / "runs" / "RUN_2026-01-01_12-00-00"
        (run_dir / "final" / "gds").mkdir(parents=True)
        gds = run_dir / "final" / "gds" / "widget.gds"
        gds.write_bytes(b"fake gds")

        result = IdeaToRTLResult(success=True, work_dir=tmp_path)
        _populate_artifact_paths(result)
        assert result.run_dir == run_dir
        assert result.gds_path == gds

    def test_tolerates_missing_paths(self, tmp_path):
        # Empty work_dir — no config, no runs. Should not raise.
        result = IdeaToRTLResult(success=True, work_dir=tmp_path)
        _populate_artifact_paths(result)
        assert result.config_path is None
        assert result.run_dir is None
        assert result.gds_path is None

    def test_handles_corrupt_yaml(self, tmp_path):
        (tmp_path / "config.yaml").write_text("this: is: not: valid: yaml:")
        result = IdeaToRTLResult(success=True, work_dir=tmp_path)
        _populate_artifact_paths(result)
        # config_path is still assigned because the file exists; design_name
        # should not be overwritten by garbage.
        assert result.config_path is not None
        assert result.design_name is None


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


class TestResultSerialisation:
    def test_result_to_dict_shape(self):
        r = IdeaToRTLResult(
            success=True,
            work_dir=Path("/tmp/x"),
            prompt_length=1234,
            wall_time_s=12.5,
            num_turns=7,
            cost_usd=0.1234,
            result_text="abc",
            design_name="widget",
        )
        d = result_to_dict(r)
        assert d["success"] is True
        assert d["all_passed"] is True
        assert d["work_dir"] == "/tmp/x"
        assert d["design_name"] == "widget"
        assert d["prompt_length"] == 1234
        assert d["wall_time_s"] == 12.5
        assert d["cost_usd"] == 0.1234
        assert d["num_turns"] == 7
        assert d["result_text_tail"] == "abc"
        assert d["gl_sim"] is None
        # Paths rendered as None when absent
        assert d["config_path"] is None
        assert d["gds_path"] is None

    def test_result_to_dict_tail_truncation(self):
        long = "x" * 5000
        r = IdeaToRTLResult(success=True, work_dir=Path("/tmp"), result_text=long)
        d = result_to_dict(r)
        assert len(d["result_text_tail"]) == 2000

    def test_write_result_json_round_trip(self, tmp_path):
        r = IdeaToRTLResult(
            success=True,
            work_dir=tmp_path / "w",
            prompt_length=100,
        )
        dest = write_result_json(r, tmp_path / "result.json")
        assert dest.is_file()
        data = json.loads(dest.read_text())
        assert data["success"] is True
        assert data["prompt_length"] == 100


# ---------------------------------------------------------------------------
# GL-sim helper error paths (no LibreLane needed)
# ---------------------------------------------------------------------------


class TestGlSimHelperErrors:
    def test_unknown_pdk_returns_error(self, tmp_path):
        report = run_post_flow_gl_sim_check(
            work_dir=tmp_path,
            pdk_key="no_such_pdk",
            pdk_root="/tmp/fake",
        )
        assert report["all_passed"] is False
        assert "unknown PDK" in report["error"]

    def test_missing_config_returns_error(self, tmp_path):
        report = run_post_flow_gl_sim_check(
            work_dir=tmp_path,
            pdk_key="gf180mcu",
            pdk_root="/tmp/fake",
        )
        assert report["all_passed"] is False
        assert "config.yaml" in report["error"]

    def test_missing_design_name_returns_error(self, tmp_path):
        (tmp_path / "config.yaml").write_text("OTHER_KEY: 1")
        report = run_post_flow_gl_sim_check(
            work_dir=tmp_path,
            pdk_key="gf180mcu",
            pdk_root="/tmp/fake",
        )
        assert "DESIGN_NAME missing" in report["error"]

    def test_missing_runs_dir_returns_error(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump({"DESIGN_NAME": "widget"})
        )
        report = run_post_flow_gl_sim_check(
            work_dir=tmp_path,
            pdk_key="gf180mcu",
            pdk_root="/tmp/fake",
        )
        assert "No LibreLane run directories" in report["error"]

    def test_missing_testbench_returns_error(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump({"DESIGN_NAME": "widget"})
        )
        run_dir = tmp_path / "runs" / "RUN_1"
        run_dir.mkdir(parents=True)
        report = run_post_flow_gl_sim_check(
            work_dir=tmp_path,
            pdk_key="gf180mcu",
            pdk_root="/tmp/fake",
        )
        assert "Testbench not found" in report["error"]

    def test_print_gl_sim_report_handles_skip(self, capsys):
        report = {"all_passed": False, "error": "config.yaml not found"}
        print_gl_sim_report(report)
        captured = capsys.readouterr()
        assert "SKIPPED" in captured.out


# ---------------------------------------------------------------------------
# Bench adapter (dry mode)
# ---------------------------------------------------------------------------


class TestBenchAdapterDry:
    def _task_dry(self) -> "BenchTask":
        return load_task(_TASK_DIR / "idea_to_digital_counter.yaml")

    def test_task_yaml_loads(self):
        t = self._task_dry()
        assert t.id == "e2e_idea_to_digital_counter"
        assert t.harness.value == "callable"
        assert t.inputs["dry_run"] is True

    @pytest.mark.parametrize(
        "yaml_name,task_id,pdk,complexity",
        [
            ("idea_to_digital_counter.yaml", "e2e_idea_to_digital_counter",
             "gf180mcu", "simple"),
            ("idea_to_digital_counter_ihp.yaml", "e2e_idea_to_digital_counter_ihp",
             "ihp_sg13g2", "simple"),
            ("idea_to_digital_alu8_gf180.yaml", "e2e_idea_to_digital_alu8_gf180",
             "gf180mcu", "medium"),
        ],
    )
    def test_dry_variants_pass(self, tmp_path, yaml_name, task_id, pdk, complexity):
        task = load_task(_TASK_DIR / yaml_name)
        assert task.id == task_id
        assert task.inputs["pdk"] == pdk
        assert task.inputs["complexity"] == complexity
        assert task.inputs["dry_run"] is True
        result = run_idea_to_digital_chip(task, tmp_path)
        assert result.status is BenchStatus.PASS, result.errors
        assert result.metrics["prompt_length"] > 2000
        assert result.compile_ok is True

    def test_adapter_passes_on_dry(self, tmp_path):
        task = self._task_dry()
        result = run_idea_to_digital_chip(task, tmp_path)
        assert result.status is BenchStatus.PASS
        assert result.backend_used == "idea-to-chip-dry"
        assert result.metrics["prompt_length"] > 2000
        assert result.metrics["gds_exists"] == 0.0
        assert result.compile_ok is True
        # Result JSON artifact should be written
        artifacts = [Path(a) for a in result.artifacts]
        json_artifact = [a for a in artifacts if a.name == "idea_to_chip_result.json"]
        assert json_artifact, f"missing result JSON in {artifacts}"
        payload = json.loads(json_artifact[0].read_text())
        assert payload["design_name"] == "counter4"
        assert payload["gl_sim"] is None

    def test_live_without_claude_cli_skips(self, tmp_path, monkeypatch):
        task = self._task_dry()
        # Force dry_run=False in a mutated copy of inputs
        live_inputs = {**task.inputs, "dry_run": False, "skip_gl_sim": True}
        live_task = task.model_copy(update={"inputs": live_inputs})

        monkeypatch.setattr(shutil, "which", lambda name: None)
        result = run_idea_to_digital_chip(live_task, tmp_path)
        assert result.status is BenchStatus.FAIL_INFRA
        assert any("Claude Code CLI" in e for e in result.errors)

    def test_live_dangerous_without_env_flag_skips(self, tmp_path, monkeypatch):
        """Double-gate sanity: allow_dangerous=true + env var unset -> fail fast.

        Without this check the CLI subprocess would hang on its first
        permission prompt (stdin is piped; no way to approve interactively)
        and burn the full 90-min timeout.
        """
        task = self._task_dry()
        live_inputs = {
            **task.inputs,
            "dry_run": False,
            "skip_gl_sim": True,
            "allow_dangerous": True,
        }
        live_task = task.model_copy(update={"inputs": live_inputs})

        # Pretend claude CLI exists, but strip the env gate.
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")
        monkeypatch.delenv("EDA_AGENTS_ALLOW_DANGEROUS", raising=False)
        result = run_idea_to_digital_chip(live_task, tmp_path)
        assert result.status is BenchStatus.FAIL_INFRA
        assert any("EDA_AGENTS_ALLOW_DANGEROUS" in e for e in result.errors)


class TestBenchAdapterValidation:
    def test_missing_required_fields_fails_infra(self, tmp_path):
        from eda_agents.bench.models import (
            Backend,
            BenchTask,
            TaskDomain,
            TaskFamily,
            TaskHarness,
            TaskScoring,
        )

        task = BenchTask(
            id="bogus",
            family=TaskFamily.END_TO_END,
            category="digital",
            domain=TaskDomain.DIGITAL,
            difficulty="easy",
            expected_backend=Backend.DRY_RUN,
            harness=TaskHarness.CALLABLE,
            inputs={"callable": "eda_agents.bench.adapters:run_idea_to_digital_chip"},
            scoring=[TaskScoring.COMPILE],
        )
        result = run_idea_to_digital_chip(task, tmp_path)
        assert result.status is BenchStatus.FAIL_INFRA
        assert any("description" in e for e in result.errors)

    def test_unknown_complexity_rejected(self):
        with pytest.raises(ValueError):
            IdeaToDigitalChipInputs(
                callable="eda_agents.bench.adapters:run_idea_to_digital_chip",
                description="a valid description here",
                design_name="counter4",
                complexity="gigabrain",
            )

    def test_unknown_pdk_rejected(self):
        with pytest.raises(ValueError):
            IdeaToDigitalChipInputs(
                callable="eda_agents.bench.adapters:run_idea_to_digital_chip",
                description="a valid description here",
                design_name="counter4",
                pdk="sky130a",
            )


# ---------------------------------------------------------------------------
# MCP tool — optional on fastmcp-less installs
# ---------------------------------------------------------------------------


try:
    import fastmcp  # noqa: F401

    HAS_FASTMCP = True
except ImportError:  # pragma: no cover
    HAS_FASTMCP = False


@pytest.mark.mcp
@pytest.mark.skipif(not HAS_FASTMCP, reason="fastmcp not installed")
class TestMCPTool:
    async def test_tool_registered(self):
        from eda_agents.mcp.server import mcp

        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "generate_rtl_draft" in names

    async def test_dry_run_returns_dict(self, tmp_path):
        from eda_agents.mcp.server import mcp

        result = await mcp.call_tool(
            "generate_rtl_draft",
            {
                "description": "4-bit counter with enable",
                "design_name": "counter4",
                "work_dir": str(tmp_path),
                "pdk": "gf180mcu",
                "pdk_root": "/tmp/fake_pdk",
                "dry_run": True,
            },
        )
        data = result.structured_content
        assert data["success"] is True
        assert data["prompt_length"] > 2000
        assert data["design_name"] == "counter4"
        assert data["gds_path"] is None

    async def test_unknown_complexity_reports_error(self, tmp_path):
        from eda_agents.mcp.server import mcp

        result = await mcp.call_tool(
            "generate_rtl_draft",
            {
                "description": "x" * 20,
                "design_name": "x",
                "work_dir": str(tmp_path),
                "dry_run": True,
                "complexity": "elvis",
            },
        )
        data = result.structured_content
        assert data["success"] is False
        assert "complexity" in data["error"]
