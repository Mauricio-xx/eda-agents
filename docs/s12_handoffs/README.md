# S12+ handoff index

Three parallel Claude sessions pick up where the S11 idea-to-chip
arc ended (merge commit `2d116c8` on main). Each handoff is
**self-contained**: enough context, file references, environment
setup, and success criteria that a fresh Claude session can work
without parent-session memory beyond what's in the repo.

Grouped by code-area + similarity so parallel sessions don't fight
over the same files:

| handoff | scope | primary code areas |
|---------|-------|--------------------|
| [S12-A](./s12a_digital_verification.md) | Digital iterative loop for >10k-cell designs + GlSimRunner cocotb backend | `src/eda_agents/agents/idea_to_rtl*`, `src/eda_agents/core/stages/gl_sim_runner.py`, `src/eda_agents/skills/digital.py` |
| [S12-B](./s12b_analog_layout.md) | SG13G2 `opamp_twostage` upstream + custom-composition analog loop | `/home/montanares/personal_exp/gLayout` (fork), `scripts/glayout_driver.py`, `src/eda_agents/mcp/server.py`, `src/eda_agents/skills/analog.py` |
| [S12-C](./s12c_ihp_live_digital.md) | Live IHP SG13G2 digital counter/ALU probe (operator-driven, Magic slowness tolerance) | `bench/tasks/end-to-end/idea_to_digital_counter_ihp_live.yaml` (new), `src/eda_agents/agents/templates/ihp_sg13g2.yaml.tmpl` |

## Running them in parallel

Each session should work off its own git worktree to avoid checkout
collisions:

```
/home/montanares/git/eda-agents-worktrees/
  ├── s11-idea-to-chip-spike/        # S11 original (now merged to main)
  ├── s12a-digital-verification/     # spawn for S12-A
  ├── s12b-analog-layout/            # spawn for S12-B
  └── s12c-ihp-live/                 # spawn for S12-C
```

File conflicts between S12-A and S12-B / S12-C are minimal
(different primary code paths). S12-B touches `glayout_driver.py` +
MCP server; S12-A touches `idea_to_rtl*.py` + sim runners; S12-C is
mostly a new YAML + evidence dir. Coordinate via the memory entry
at `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/project_s11_idea_to_chip.md`
(update it when each session closes).

## Priority order (if only running serially)

1. **S12-A** first — closes the next digital scaling step and
   exercises the loop pattern that S12-B's custom-composition work
   will reuse conceptually. Also polishes the cocotb path by
   adding real GL sim parity with the iverilog path.
2. **S12-B** second — gLayout SG13G2 opamp (gap 4) unblocks full
   analog idea→GDS on IHP, which is the single biggest remaining
   user-facing gap for the arc. Custom-composition (gap 5) is
   exploratory; acceptable to close with honest-fail.
3. **S12-C** last — smallest scope, primarily infra validation.
   Don't block on it; can run in a background worktree during
   S12-A/B work if the Claude subscription has quota headroom.

## What "done" means for each handoff

Every handoff file has its own "Success criteria" section. The
common discipline across all three:

- **Evidence dir under `bench/results/`** with README + summary +
  report + result JSON, allowlisted in `.gitignore` — match the
  S11 pattern (see `bench/results/s11_*_live/README.md`).
- **No silent skips of verification.** `feedback_full_verification`
  memory entry is load-bearing.
- **Honest-fail is a valid outcome**, especially for exploratory
  work (S12-B gap 5, S12-C if Magic blocks). NEVER fabricate
  green.
- **Suite stays >= 988 green** on `pytest -m "not spice and not
  klayout and not magic and not librelane and not veriloga and
  not xspice and not bridge and not bench"`.
