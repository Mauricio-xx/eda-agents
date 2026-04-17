# S12-C handoff — IHP live digital gate

You are picking up the **thinnest S12+ gap**: prove the S11
idea-to-chip digital pipeline PDK-neutral with a **live IHP SG13G2
run**. No code changes are required to attempt this; the gap is
operational (Magic StreamOut is known-slow) and may surface
infrastructure deficiencies that become code changes.

This session is intentionally small. If it finishes in an afternoon,
close it and roll any lessons learned into a short follow-up or into
S12-A / S12-B scope.

## Inherited context

S11 validated the NL → digital GDS path **on GF180MCU-D** only. IHP
SG13G2 is already on the dry-gate matrix:

- `bench/tasks/end-to-end/idea_to_digital_counter_ihp.yaml` — dry,
  always green on CI. Confirms prompt routing + PDK template
  rendering for IHP.
- **No IHP live task** shipped in S11 because of documented upstream
  slowness:
  > "IHP's Magic StreamOut step is known-slow (20-60+ min per
  > counter on current main). Users who want live IHP should copy
  > this file to idea_to_digital_counter_ihp_live.yaml with
  > dry_run=false and be prepared for a >1h wall time."

The `build_from_spec_prompt` already warns the agent explicitly:

> "IMPORTANT: on IHP SG13G2, some steps (notably Magic.StreamOut and
> Magic.SpiceExtraction) can take 20-60+ minutes even for trivial
> designs — this is known PDK+Magic slowness, not a bug in your
> flow. When invoking the Bash tool for LibreLane, set a high
> timeout (e.g. timeout=3600000 ms = 1h, or launch with
> run_in_background and poll) and be patient."

Plus the S11 tail-F fix (LOG INSPECTION DISCIPLINE section) stops
Claude from deadlocking on `tail -f` of a log whose writer has
already exited — that was a real failure mode on the ALU probe and
would be deadlier on IHP where the wait is orders of magnitude
longer.

Merge commit: `2d116c8`.

## What this session must deliver

One of:

**(A) GATE GREEN** — IHP live digital Pass@1 on counter4 (or ALU8
if counter is cheap enough that ALU fits in the quota window).
Evidence persisted under `bench/results/s12c_ihp_live/`.

**(B) Honest-fail + documented PDK blocker** — if Magic StreamOut
times out, deadlocks, or produces non-deterministic output,
document the failure mode with:
- Exact LibreLane step that blocked (`<run>/NN-magic-*/flow.log`
  tail).
- Magic version (`magic -v` from within the LibreLane venv).
- IHP PDK commit hash checked out under `$PDK_ROOT` (is dev branch
  up to date?).
- Upstream issue link if one exists on IHP-Open-PDK or Magic.
- Proposed mitigation (bigger timeout, specific `meta.flow`
  substitution, skipping steps via `RUN_*=false` keys, etc.).

**(C) Workaround shipped** — if the blocker is fixable in
eda-agents / LibreLane config (e.g. setting
`RUN_MAGIC_STREAMOUT: false` in the IHP template similar to how
GF180 handles it), implement the fix and land a PASS run.

## Environment setup

```bash
cd /home/montanares/personal_exp/eda-agents
git worktree add -b feat/s12c-ihp-live \
    /home/montanares/git/eda-agents-worktrees/s12c-ihp-live main

cd /home/montanares/git/eda-agents-worktrees/s12c-ihp-live
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,agents,mcp,adk]"
.venv/bin/pip install -U "python-dotenv>=1.1.0"

source /home/montanares/personal_exp/eda-agents/.env  # OPENROUTER_API_KEY

# IHP-specific env:
export PDK_ROOT=/home/montanares/git/IHP-Open-PDK
export EDA_AGENTS_ALLOW_DANGEROUS=1

# Verify the PDK is on dev branch + up to date (per user's global rules):
cd /home/montanares/git/IHP-Open-PDK && git status
# should be on `dev`. If not:
#   git checkout dev && git pull && git submodule update --recursive
cd -
```

## Files to read before starting

**Already-landed IHP bench assets (read-only):**

- `bench/tasks/end-to-end/idea_to_digital_counter_ihp.yaml` — dry
  variant. Your live variant forks this.
- `bench/tasks/end-to-end/idea_to_digital_counter_live.yaml` — GF180
  live variant. Mirror this shape for IHP.
- `src/eda_agents/agents/templates/ihp_sg13g2.yaml.tmpl` — IHP
  LibreLane template. Check if `RUN_MAGIC_STREAMOUT` / `RUN_MAGIC_
  DRC` / `RUN_KLAYOUT_XOR` / `PRIMARY_GDSII_STREAMOUT_TOOL` can be
  set to minimise Magic-heavy steps.
- `src/eda_agents/core/pdk.py::IHP_SG13G2` — PDK config, find
  `librelane_extra_flags=("--manual-pdk",)` and
  `default_clock_period_ns=10.0`.
- `src/eda_agents/agents/tool_defs.py::build_from_spec_prompt` —
  particularly Phase 4 and the LOG INSPECTION DISCIPLINE warning.

**S11 evidence with IHP context:**

- `bench/results/s11_fase0_live/README.md` — reference shape for
  your IHP evidence README. Match its honest-diagnostic style.

## Implementation outline

### Step 1 — YAML first (live variant)

Create `bench/tasks/end-to-end/idea_to_digital_counter_ihp_live.yaml`:

```yaml
id: e2e_idea_to_digital_counter_ihp_live
family: end-to-end
category: digital
domain: digital
pdk: ihp_sg13g2
difficulty: hard
expected_backend: librelane
harness: callable
topology: counter_4bit
inputs:
  callable: eda_agents.bench.adapters:run_idea_to_digital_chip
  description: |
    4-bit synchronous up-counter with active-low asynchronous reset
    and an enable input.
    <SAME BODY AS GF180 COUNTER DESCRIPTION>
  design_name: counter4_ihp
  pdk: ihp_sg13g2
  complexity: simple
  dry_run: false
  skip_gl_sim: false
  allow_dangerous: true
  max_budget_usd: 15.0            # IHP may need more retries
  timeout_s: 7200                 # 2 h (Magic can take >1 h)
  librelane_python: /home/montanares/git/librelane/.venv/bin/python
expected_metrics:
  gds_exists: {min: 1, unit: bool}
  gl_post_synth_ok: {min: 1, unit: bool}
  gl_post_pnr_ok: {min: 1, unit: bool}
scoring:
  - compile
  - sim_run
  - metrics_in_range
weight: 4.0
timeout_s: 7200
notes: |
  S12-C live IHP digital probe. Magic StreamOut is known-slow on
  SG13G2; budget + timeout increased accordingly. See
  docs/s12_handoffs/s12c_ihp_live_digital.md.
```

Commit. Run the dry variant first to confirm PDK routing is still
clean on your branch.

### Step 2 — Rehearse the shell command

Before kicking off the live bench, manually invoke
`build_from_spec_prompt(...)` with `pdk_config="ihp_sg13g2"` and
`tb_framework="iverilog"` and EYEBALL the prompt. Specifically
verify:

- `PDK=ihp-sg13g2` appears in the flow command.
- `--manual-pdk` extra flag is present.
- LOG INSPECTION DISCIPLINE warning is present.
- Nix PATH prefix points at valid yosys / magic / openroad / netgen
  / klayout binaries.

### Step 3 — Launch (plan for slow)

```bash
export PDK_ROOT=/home/montanares/git/IHP-Open-PDK
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_counter_ihp_live \
    --run-id s12c_ihp_counter_attempt1 \
    --verbose 2>&1 | tee /tmp/s12c_ihp.log &
```

Run in background, set up a **Monitor** on the log with patterns:
`Pass@1|FAIL|ERROR|Traceback|OOM|Killed|exit code [1-9]`. Also
monitor the LibreLane step directory for progression (`ls
<work_dir>/runs/RUN_*/  | tail -5` periodically).

If no progress for **>30 min** on a single step: that step is
suspect. Check the step's `flow.log` / `error.log` and the
pstree of the bench process. Known culprits on IHP:

- `NN-magic-streamout`: can legitimately take 30-60 min.
- `NN-magic-spiceextraction`: also long.
- `NN-netgen-lvs`: usually fast; if slow, Netgen might have
  hit a pathological case.

### Step 4 — If it PASSES

Persist evidence per the S11 pattern:
- `bench/results/s12c_ihp_live/README.md` (honest, with timings
  per step).
- Update `.gitignore` with the allowlist block (mirror
  `s11_fase0_live` entries, just swap the name).
- Commit.
- Update memory entry `project_s11_idea_to_chip.md` with a line
  flagging IHP live as GREEN.

### Step 5 — If it BLOCKS

- Kill the run cleanly (do not `kill -9`; let LibreLane save its
  state).
- Diagnose:
  ```bash
  # Find the last written step
  ls -la <work_dir>/runs/RUN_*/NN-*/ | tail -20
  # Check flow log for stalls
  tail -100 <work_dir>/runs/RUN_*/flow.log
  # Check PDK state
  cd $PDK_ROOT && git log -1
  # Magic version
  $(which magic 2>/dev/null || echo "no magic") -v
  ```
- Document findings in `bench/results/s12c_ihp_live/README.md` as
  an honest-fail (shape: see the counter-probe S11 FFT-inconclusive
  README for the tone — `bench/results/s11_fase2_fft_live/README.md`).
- DO NOT edit `idea_to_chip_s11.md` to claim IHP is supported.
- If a template tweak (e.g. `RUN_MAGIC_STREAMOUT: false` +
  `PRIMARY_GDSII_STREAMOUT_TOOL: klayout` like the GF180 template
  uses) looks promising, try it in a branch-off run before
  declaring honest-fail.

### Step 6 — Regardless of outcome

- Update `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/project_s11_idea_to_chip.md`
  with a new paragraph under "Gates pending / closed" reflecting
  the outcome.
- If you shipped a template tweak, bump a regression test in
  `tests/test_from_spec.py::TestConfigTemplate` checking the IHP
  template contains whatever keys you added.

## Success criteria

- **GATE GREEN path** (best): `bench/results/s12c_ihp_live/` with
  `gds_exists=1 + gl_post_synth_ok=1 + gl_post_pnr_ok=1`. At
  minimum: counter4 PDK-neutrality proven.
- **Honest-fail path** (acceptable): detailed README + PDK/Magic
  version capture + reproduction command. No false green.
- **Workaround path** (best-effort): template key change merged,
  new live run passes with the workaround in place, flagged in
  the YAML notes and the evidence README.

Whichever of the three you end at is fine. **Do not fabricate a
green when the flow actually blocked.**

## Risks / known gotchas

- **IHP PDK dev branch churn**: the user's global rules say the
  PDK "should be install... from dev branch. ihp-sg13g2 is in
  rapid development." Check `git log` before diagnosing — the
  blocker you hit may be fixed in a newer dev commit.
- **nix-shell discipline**: LibreLane and its EDA tools come from
  `/nix/store`; the `detect_nix_eda_tool_dirs()` helper in
  `src/eda_agents/agents/digital_autoresearch.py` finds them and
  the from-spec prompt prepends them to PATH. If you manually run
  a LibreLane subprocess, include the same prefix.
- **Magic version drift**: if `magic` from the Nix store is
  >= a version that fixed StreamOut perf, you may already be
  unblocked; if it's older than the IHP community's known-good
  version, flag that as the root cause.
- **Claude CLI rate limit**: the S11 FFT first-run hit 429 after
  counter + ALU + CPU + FFT on one subscription window. IHP live
  is single-task + long wall time; on a cold window, quota is
  fine. Don't schedule IHP live after a burst of GF180 runs.
- **Bench `gl_sim_skipped` fallback**: if for some reason GL sim
  does get skipped (e.g. due to a cocotb TB flavour the agent
  picks), the adapter will emit `gl_sim_skipped=1`; your
  expected_metrics YAML should be strict (`gl_post_*_ok`), so a
  skip will correctly fail the audit.

## Memory + doc references

- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/project_s11_idea_to_chip.md`
  — S11 summary; IHP paragraph in "Not closed tonight" section.
- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/feedback_full_verification.md`
  — the no-silent-pass rule applies double for slow runs where
  waiting is tempting.
- `docs/idea_to_chip_s11.md` — section "Environment prerequisites
  (for live runs)" lists the three things you need; IHP version
  is one of them.
- `docs/upstream_issues/` — if a relevant IHP issue exists,
  reference it in your evidence README.
- `bench/results/s11_fase2_fft_live/README.md` — template for an
  honest-fail README (the FFT inconclusive run). Match its tone.

## How to start the session

> "Take S12-C. Run `docs/s12_handoffs/s12c_ihp_live_digital.md`
> end-to-end. Realistic target: counter4 live on IHP SG13G2 with
> Pass@1 OR a documented honest-fail. Budget: one afternoon of
> operator time; cap LLM spend at $15. No fabricated green."
