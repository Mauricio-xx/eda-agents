"""4-role analog DAG harness.

The orchestrator walks ``Librarian -> Architect -> Designer <-> Verifier``,
records every handoff in an ``IterationLog``, and gates the
designer / verifier loop on ``BlockSpec.max_iterations``. The harness is
LLM-agnostic: it delegates each role's reasoning to a ``RoleExecutor``
implementation. The bundled ``DryRunExecutor`` lets tests and the demo
run the full DAG without any model calls.

DAG semantics:

    Librarian         (one shot, surveys assets)
       v
    Architect (P1)    (decomposes spec, emits behavioural model + tb)
       v
    Designer          (sizes transistors, emits netlist)
       v
    Verifier          (pre-sim gates -> SpiceRunner -> margin report)
       v
   PASS or FAIL?
     - PASS  -> Architect (P3 integration / sign-off)
     - FAIL  -> Designer (next iteration, capped at max_iterations)
              -> if cap exceeded, escalate to user
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda_agents.agents.analog_roles.roles import (
    Role,
    RoleExecutor,
    RoleResult,
)
from eda_agents.agents.iteration_log import (
    EscalationError,
    IterationEntry,
    IterationLog,
)
from eda_agents.skills.registry import get_skill
from eda_agents.specs import BlockSpec


@dataclass
class HarnessOutput:
    """Aggregated result of a full DAG run."""

    block: str
    session_id: str
    final_status: str  # "PASS" | "FAIL" | "ESCALATED"
    log: IterationLog
    role_results: list[RoleResult] = field(default_factory=list)
    iterations_used: int = 0

    def passed(self) -> bool:
        return self.final_status == "PASS"


class DryRunExecutor:
    """Default executor that does not call any LLM.

    For each role it returns a synthetic ``RoleResult`` describing the
    action that *would* have been taken. The summary string echoes the
    rendered skill prompt so a reviewer can confirm prompts wire up
    correctly without paying for a real model call.
    """

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    def execute(
        self,
        role: Role,
        prompt: str,
        context: dict[str, Any],
    ) -> RoleResult:
        spec: BlockSpec | None = context.get("spec")
        block = spec.block if spec else context.get("block", "<unknown>")
        if self.verbose:
            print(f"[dry-run] {role.value} would act on block '{block}'")
            print(prompt[:240] + ("..." if len(prompt) > 240 else ""))
        if role is Role.LIBRARIAN:
            return RoleResult(
                role=role,
                summary=f"inventory for '{block}' completed (dry-run)",
                artifacts={"inventory_kind": "stub", "block": block},
                next_role=Role.ARCHITECT,
            )
        if role is Role.ARCHITECT:
            return RoleResult(
                role=role,
                summary=f"architecture decomposed for '{block}' (dry-run)",
                artifacts={
                    "behavioral_model": f"{block}_beh.va",
                    "testbench": f"tb_{block}.cir",
                },
                next_role=Role.DESIGNER,
            )
        if role is Role.DESIGNER:
            return RoleResult(
                role=role,
                summary=f"transistor netlist sized for '{block}' (dry-run)",
                artifacts={"netlist": f"{block}.cir", "rationale": "stub"},
                next_role=Role.VERIFIER,
            )
        if role is Role.VERIFIER:
            verdict = bool(context.get("verifier_passes", True))
            return RoleResult(
                role=role,
                summary=(
                    f"verification {'PASS' if verdict else 'FAIL'} for '{block}' "
                    f"(dry-run)"
                ),
                artifacts={"margin_report": "stub", "verdict": verdict},
                success=verdict,
                next_role=Role.ARCHITECT if verdict else Role.DESIGNER,
            )
        raise ValueError(f"unknown role {role}")


class AnalogRolesHarness:
    """Orchestrate the 4-role analog DAG over a single ``BlockSpec``."""

    def __init__(
        self,
        spec: BlockSpec,
        *,
        executor: RoleExecutor | None = None,
        topology: Any | None = None,
        max_iterations: int = 3,
        session_id: str | None = None,
    ) -> None:
        self.spec = spec
        self.executor = executor or DryRunExecutor()
        self.topology = topology
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.log = IterationLog(
            session_id=self.session_id,
            block=spec.block,
            max_iterations=max_iterations,
        )

    # -- Public API -------------------------------------------------

    def run(self) -> HarnessOutput:
        """Execute the DAG and return the aggregate ``HarnessOutput``."""
        results: list[RoleResult] = []

        librarian_res = self._dispatch(Role.LIBRARIAN, prior=None)
        results.append(librarian_res)
        # Setup handoffs use iteration=0 so they do not count against
        # the designer/verifier loop budget.
        self._record_handoff(
            Role.LIBRARIAN, Role.ARCHITECT, librarian_res, iteration=0
        )

        architect_res = self._dispatch(Role.ARCHITECT, prior=librarian_res)
        results.append(architect_res)
        self._record_handoff(
            Role.ARCHITECT, Role.DESIGNER, architect_res, iteration=0
        )

        designer_res, verifier_res, iterations = self._loop_designer_verifier(
            architect_res
        )
        results.extend(designer_res)
        results.extend(verifier_res)

        if not verifier_res:
            # Escalation kicked in before the verifier ran.
            return HarnessOutput(
                block=self.spec.block,
                session_id=self.session_id,
                final_status="ESCALATED",
                log=self.log,
                role_results=results,
                iterations_used=iterations,
            )

        last = verifier_res[-1]
        if last.success:
            self._record_handoff(
                Role.VERIFIER,
                Role.ARCHITECT,
                last,
                status="accepted",
                iteration=iterations,
            )
            final = "PASS"
        else:
            final = "FAIL"
        return HarnessOutput(
            block=self.spec.block,
            session_id=self.session_id,
            final_status=final,
            log=self.log,
            role_results=results,
            iterations_used=iterations,
        )

    def save_log(self, path: str | Path) -> Path:
        return self.log.save(path)

    # -- Internals --------------------------------------------------

    def _dispatch(self, role: Role, prior: RoleResult | None) -> RoleResult:
        prompt = get_skill(role.skill_name).render(self.topology)
        context: dict[str, Any] = {
            "spec": self.spec,
            "block": self.spec.block,
            "session_id": self.session_id,
            "topology": self.topology,
            "prior": prior,
            "iteration": self.log.current_iteration() + 1,
        }
        # Allow callers to plumb extra controls (e.g., test harness
        # forces a verifier failure) through the context.
        ctx_extra = getattr(self, "_context_extra", None)
        if isinstance(ctx_extra, dict):
            context.update(ctx_extra)
        return self.executor.execute(role, prompt, context)

    def _record_handoff(
        self,
        from_role: Role,
        to_role: Role,
        result: RoleResult,
        *,
        status: str = "handoff",
        iteration: int | None = None,
    ) -> IterationEntry:
        return self.log.record(
            from_role=from_role.value,
            to_role=to_role.value,
            status=status,
            summary=result.summary,
            iteration=iteration,
            metadata={"artifacts": list(result.artifacts.keys())},
        )

    def _loop_designer_verifier(
        self,
        architect_res: RoleResult,
    ) -> tuple[list[RoleResult], list[RoleResult], int]:
        designer_runs: list[RoleResult] = []
        verifier_runs: list[RoleResult] = []
        iteration = 0
        prior: RoleResult = architect_res

        while True:
            iteration += 1
            try:
                designer_res = self._dispatch(Role.DESIGNER, prior=prior)
            except EscalationError as exc:  # pragma: no cover - defensive
                self.log.escalate(str(exc))
                return designer_runs, verifier_runs, iteration - 1
            designer_runs.append(designer_res)
            self._record_handoff(
                Role.DESIGNER, Role.VERIFIER, designer_res, iteration=iteration
            )

            verifier_res = self._dispatch(Role.VERIFIER, prior=designer_res)
            verifier_runs.append(verifier_res)

            if verifier_res.success:
                return designer_runs, verifier_runs, iteration

            # Verifier rejected -> back to designer if budget remains.
            if iteration >= self.log.max_iterations:
                self.log.record(
                    from_role="verifier",
                    to_role="designer",
                    status="rejected",
                    summary=verifier_res.summary,
                    iteration=iteration,
                    metadata={"final_iteration": True},
                )
                self.log.escalate(
                    summary=(
                        f"designer/verifier loop exhausted after "
                        f"{iteration} iteration(s); escalating"
                    ),
                )
                return designer_runs, verifier_runs, iteration

            self.log.record(
                from_role="verifier",
                to_role="designer",
                status="rejected",
                summary=verifier_res.summary,
                iteration=iteration,
            )
            prior = verifier_res


def run_analog_roles(
    spec: BlockSpec,
    *,
    executor: RoleExecutor | None = None,
    topology: Any | None = None,
    max_iterations: int = 3,
    session_id: str | None = None,
    log_path: str | Path | None = None,
) -> HarnessOutput:
    """Convenience wrapper for one-shot harness invocations."""
    harness = AnalogRolesHarness(
        spec=spec,
        executor=executor,
        topology=topology,
        max_iterations=max_iterations,
        session_id=session_id,
    )
    output = harness.run()
    if log_path is not None:
        harness.save_log(log_path)
    return output
