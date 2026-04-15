"""Tests for the bench schema mirror (``BenchTask`` / ``BenchResult``).

The Pydantic models duplicate the constraints declared in
``bench/schemas/{task,result}.json``. Tests here both exercise the
Pydantic surface and assert that the JSON schemas stay in sync with the
enums (so we don't drift after future enum extensions).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from eda_agents.bench.models import (
    Backend,
    BenchResult,
    BenchScores,
    BenchStatus,
    BenchTask,
    MetricBound,
    TaskDomain,
    TaskFamily,
    TaskHarness,
    TaskScoring,
    load_task,
    load_tasks_from_dir,
)


# Repo-relative schema paths — derived from this test file location so
# pytest can find them regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_SCHEMA = REPO_ROOT / "bench" / "schemas" / "task.json"
RESULT_SCHEMA = REPO_ROOT / "bench" / "schemas" / "result.json"


def _good_task_doc() -> dict:
    return {
        "id": "miller_ota_ihp_easy",
        "family": "spec-to-topology",
        "category": "ota",
        "domain": "voltage",
        "pdk": "ihp_sg13g2",
        "difficulty": "easy",
        "expected_backend": "ngspice-osdi",
        "harness": "analog_roles",
        "topology": "miller_ota",
        "scoring": ["compile", "metrics_in_range"],
        "expected_metrics": {
            "Adc_dB": {"min": 50.0, "unit": "dB"},
        },
    }


# ---------------------------------------------------------------------------
# Schema files
# ---------------------------------------------------------------------------


def test_schema_files_exist_and_parse():
    assert TASK_SCHEMA.is_file()
    assert RESULT_SCHEMA.is_file()
    json.loads(TASK_SCHEMA.read_text())
    json.loads(RESULT_SCHEMA.read_text())


def test_schema_enums_match_pydantic_enums():
    """Schema enum lists and Pydantic enum members must match exactly."""
    task_schema = json.loads(TASK_SCHEMA.read_text())
    props = task_schema["properties"]
    # family
    assert set(props["family"]["enum"]) == {f.value for f in TaskFamily}
    # domain
    assert set(props["domain"]["enum"]) == {d.value for d in TaskDomain}
    # harness
    assert set(props["harness"]["enum"]) == {h.value for h in TaskHarness}
    # backend
    assert set(props["expected_backend"]["enum"]) == {b.value for b in Backend}
    # scoring
    schema_scoring = set(props["scoring"]["items"]["enum"])
    assert schema_scoring == {s.value for s in TaskScoring}
    # pdk: schema allows null + the registered ones
    pdk_enum = props["pdk"]["enum"]
    assert "ihp_sg13g2" in pdk_enum and "gf180mcu" in pdk_enum and None in pdk_enum

    result_schema = json.loads(RESULT_SCHEMA.read_text())
    status_enum = result_schema["properties"]["status"]["enum"]
    assert set(status_enum) == {s.value for s in BenchStatus}


# ---------------------------------------------------------------------------
# BenchTask Pydantic surface
# ---------------------------------------------------------------------------


def test_task_loads_minimal_valid_doc():
    task = BenchTask.model_validate(_good_task_doc())
    assert task.id == "miller_ota_ihp_easy"
    assert task.family is TaskFamily.SPEC_TO_TOPOLOGY
    assert task.domain is TaskDomain.VOLTAGE
    assert task.harness is TaskHarness.ANALOG_ROLES
    assert task.expected_backend is Backend.NGSPICE_OSDI
    assert task.expected_metrics["Adc_dB"].min == 50.0


def test_task_extra_fields_rejected():
    doc = _good_task_doc()
    doc["spurious"] = "boom"
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)


def test_task_unknown_pdk_rejected():
    doc = _good_task_doc()
    doc["pdk"] = "sky130"
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)


def test_task_non_digital_requires_pdk():
    doc = _good_task_doc()
    doc["pdk"] = None
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)


def test_digital_task_allows_null_pdk():
    doc = _good_task_doc()
    doc.update({
        "id": "digital_smoke",
        "domain": "digital",
        "pdk": None,
        "expected_backend": "verilator",
        "harness": "digital_autoresearch",
        "topology": None,
    })
    doc.pop("topology", None)
    task = BenchTask.model_validate(doc)
    assert task.pdk is None
    assert task.domain is TaskDomain.DIGITAL


def test_task_scoring_must_be_nonempty():
    doc = _good_task_doc()
    doc["scoring"] = []
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)


def test_task_weight_and_timeout_validation():
    doc = _good_task_doc()
    doc["weight"] = -0.1
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)
    doc["weight"] = 1.0
    doc["timeout_s"] = 0
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)


def test_task_difficulty_pattern():
    doc = _good_task_doc()
    doc["difficulty"] = "trivial"
    with pytest.raises(Exception):
        BenchTask.model_validate(doc)


def test_task_is_frozen():
    task = BenchTask.model_validate(_good_task_doc())
    with pytest.raises(Exception):
        task.id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Metric bounds
# ---------------------------------------------------------------------------


def test_metric_bound_requires_at_least_one_bound():
    with pytest.raises(Exception):
        MetricBound()


def test_metric_bound_check_min_only():
    mb = MetricBound(min=10.0)
    ok, margin = mb.check(15.0)
    assert ok and margin == 5.0
    ok, margin = mb.check(5.0)
    assert not ok and margin == -5.0


def test_metric_bound_check_max_only():
    mb = MetricBound(max=100.0)
    ok, margin = mb.check(80.0)
    assert ok and margin == 20.0


def test_metric_bound_inconsistent_minmax_rejected():
    with pytest.raises(Exception):
        MetricBound(min=10.0, max=5.0)


# ---------------------------------------------------------------------------
# Result wire format
# ---------------------------------------------------------------------------


def test_result_round_trip(tmp_path):
    res = BenchResult(
        task_id="x",
        status=BenchStatus.PASS,
        scores=BenchScores(
            compile=1.0,
            sim_run=1.0,
            audit_passed=1.0,
            weighted_total=1.0,
        ),
        harness_used="dry_run",
        backend_used="dry-run",
        pdk_used=None,
        duration_s=0.01,
        artifacts=["/tmp/sim.cir"],
        metrics={"Adc_dB": 32.5, "valid": True, "note": "ok"},
        errors=[],
        notes=["hello"],
    )
    path = tmp_path / "r.json"
    res.save_json(path)
    other = BenchResult.load_json(path)
    assert other == res
    assert other.passed


def test_scores_weighted_total_must_be_in_range():
    with pytest.raises(Exception):
        BenchScores(weighted_total=1.5)
    with pytest.raises(Exception):
        BenchScores(weighted_total=-0.1, compile=1.0)


def test_result_extra_field_rejected(tmp_path):
    with pytest.raises(Exception):
        BenchResult(
            task_id="x",
            status=BenchStatus.PASS,
            scores=BenchScores(weighted_total=1.0),
            harness_used="dry",
            duration_s=0.0,
            spurious="boom",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Disk loaders
# ---------------------------------------------------------------------------


def test_load_task_yaml(tmp_path):
    p = tmp_path / "t.yaml"
    yaml.safe_dump(_good_task_doc(), p.open("w"))
    task = load_task(p)
    assert task.id == "miller_ota_ihp_easy"


def test_load_task_json(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(_good_task_doc()))
    task = load_task(p)
    assert task.harness is TaskHarness.ANALOG_ROLES


def test_load_task_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_task(tmp_path / "nope.yaml")


def test_load_task_unknown_extension(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("ignored")
    with pytest.raises(ValueError):
        load_task(p)


def test_load_tasks_from_dir_filters_and_orders(tmp_path):
    a = tmp_path / "a.yaml"
    yaml.safe_dump(_good_task_doc(), a.open("w"))
    b_doc = _good_task_doc()
    b_doc["id"] = "bugfix_task"
    b_doc["family"] = "bugfix"
    b = tmp_path / "b.yaml"
    yaml.safe_dump(b_doc, b.open("w"))
    # Non-task junk file should be ignored without crashing.
    (tmp_path / "README.md").write_text("# notes")

    all_tasks = load_tasks_from_dir(tmp_path)
    assert {t.id for t in all_tasks} == {"miller_ota_ihp_easy", "bugfix_task"}

    bugfix_only = load_tasks_from_dir(tmp_path, family="bugfix")
    assert [t.id for t in bugfix_only] == ["bugfix_task"]


def test_repo_seed_tasks_validate():
    """Every shipped seed task under ``bench/tasks/`` must validate."""
    seed_root = REPO_ROOT / "bench" / "tasks"
    if not seed_root.exists():
        pytest.skip("no seed tasks shipped yet")
    tasks = load_tasks_from_dir(seed_root)
    # Sanity: at least one task per family should ship in the seed.
    fams = {t.family.value for t in tasks}
    assert TaskFamily.SPEC_TO_TOPOLOGY.value in fams
