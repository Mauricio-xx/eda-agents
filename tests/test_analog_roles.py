"""Tests for the analog 4-role DAG harness."""

from __future__ import annotations

from typing import Any

import pytest

from eda_agents.agents.analog_roles import (
    AnalogRolesHarness,
    DryRunExecutor,
    Role,
    RoleResult,
    run_analog_roles,
)
from eda_agents.specs import load_spec_from_string


SPEC_YAML = """\
block: miller_ota
process: ihp_sg13g2
supply:
  vdd: 1.2
  vss: 0.0
specs:
  dc_gain: {min: 60, unit: dB}
  power:   {max: 1.0, unit: mW}
corners: [TT_27]
"""


def _spec():
    return load_spec_from_string(SPEC_YAML)


# --------------------------------------------------------------------- #
# DryRunExecutor sanity
# --------------------------------------------------------------------- #


def test_dry_run_visits_each_role():
    out = run_analog_roles(_spec())
    roles_seen = [r.role for r in out.role_results]
    assert Role.LIBRARIAN in roles_seen
    assert Role.ARCHITECT in roles_seen
    assert Role.DESIGNER in roles_seen
    assert Role.VERIFIER in roles_seen
    assert out.final_status == "PASS"
    assert out.iterations_used == 1


def test_iteration_log_records_handoffs():
    spec = _spec()
    harness = AnalogRolesHarness(spec=spec, executor=DryRunExecutor())
    harness.run()
    statuses = [e.status for e in harness.log.entries]
    # librarian->architect, architect->designer, designer->verifier,
    # verifier->architect (PASS handoff)
    assert "accepted" in statuses
    assert harness.log.block == spec.block


def test_save_log_roundtrip(tmp_path):
    spec = _spec()
    out = run_analog_roles(
        spec, log_path=tmp_path / "log.yaml"
    )
    from eda_agents.agents.iteration_log import IterationLog

    loaded = IterationLog.load(tmp_path / "log.yaml")
    assert loaded.block == spec.block
    assert loaded.session_id == out.session_id


# --------------------------------------------------------------------- #
# Designer / verifier loop — escalation
# --------------------------------------------------------------------- #


class _FlakyVerifierExecutor(DryRunExecutor):
    """Verifier always fails so the loop escalates."""

    def execute(self, role, prompt, context):
        result = super().execute(role, prompt, context)
        if role is Role.VERIFIER:
            return RoleResult(
                role=role,
                summary=result.summary,
                artifacts=result.artifacts,
                success=False,
                next_role=Role.DESIGNER,
            )
        return result


def test_loop_escalates_after_cap():
    spec = _spec()
    harness = AnalogRolesHarness(
        spec=spec,
        executor=_FlakyVerifierExecutor(),
        max_iterations=2,
    )
    output = harness.run()
    assert output.final_status == "FAIL"
    assert output.iterations_used == 2
    assert any(e.status == "escalated" for e in harness.log.entries)


# --------------------------------------------------------------------- #
# Custom executor
# --------------------------------------------------------------------- #


class _CountingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Role, str, Any]] = []

    def execute(self, role: Role, prompt: str, context: dict) -> RoleResult:
        self.calls.append((role, prompt, context.get("block")))
        next_role = {
            Role.LIBRARIAN: Role.ARCHITECT,
            Role.ARCHITECT: Role.DESIGNER,
            Role.DESIGNER: Role.VERIFIER,
            Role.VERIFIER: Role.ARCHITECT,
        }[role]
        return RoleResult(
            role=role,
            summary=f"counted {role.value}",
            success=True,
            next_role=next_role,
        )


def test_custom_executor_receives_rendered_prompt():
    spec = _spec()
    exec_ = _CountingExecutor()
    harness = AnalogRolesHarness(spec=spec, executor=exec_)
    harness.run()
    seen_roles = [c[0] for c in exec_.calls]
    assert seen_roles == [
        Role.LIBRARIAN,
        Role.ARCHITECT,
        Role.DESIGNER,
        Role.VERIFIER,
    ]
    # Each prompt must mention the canonical block name (rendered from
    # the role-specific skill prompt).
    for role, prompt, block in exec_.calls:
        assert block == spec.block
        assert isinstance(prompt, str) and len(prompt) > 100
        assert "Librarian" in prompt or "Architect" in prompt or \
               "Designer" in prompt or "Verifier" in prompt


# --------------------------------------------------------------------- #
# Skills are correctly named
# --------------------------------------------------------------------- #


def test_role_skill_names():
    assert Role.LIBRARIAN.skill_name == "analog.roles.librarian"
    assert Role.ARCHITECT.skill_name == "analog.roles.architect"
    assert Role.DESIGNER.skill_name == "analog.roles.designer"
    assert Role.VERIFIER.skill_name == "analog.roles.verifier"


def test_role_prompt_renders_via_skill():
    from eda_agents.skills import get_skill

    for role in Role:
        prompt = get_skill(role.skill_name).render(None)
        assert role.value.capitalize() in prompt or role.value in prompt


def test_invalid_role_raises():
    with pytest.raises(ValueError):
        DryRunExecutor().execute("not-a-role", "", {})  # type: ignore[arg-type]
