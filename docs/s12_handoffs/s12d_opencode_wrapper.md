# S12-D — OpenCode Harness Wrapper (multi-provider digital flow driver)

## Why

`ClaudeCodeHarness` (`src/eda_agents/agents/claude_code_harness.py`)
is the only harness that drives the idea-to-chip digital flow today.
It wraps the Anthropic Claude CLI (`claude --print --output-format
json`) and is bound to Anthropic-provided models. The S12-A Haiku
probes proved the value of testing the same digital flow against a
weaker model:

* `s12a_haiku_fft8_live` exposed the cocotb-no-asserts class of TB
  failure that Opus had spontaneously sidestepped — a finding
  worth ~half the framework discipline added in S12-A.
* `s12a_haiku_fft8_v3_loop_iterates` provided the FIRST live
  validation of the IdeaToRTLLoop critique-feedback path, because
  Haiku is slow enough to force per-turn timeouts.

We currently change models only via `--model` on the Claude CLI,
which still routes through Anthropic. To compare against:

* **Gemini Flash 8B** (~50× cheaper than Opus per token, available
  via OpenRouter)
* **GPT-4o / GPT-4o-mini** (different family, different failure
  modes — likely different TB-writing patterns)
* **Local Ollama models** (zero per-call cost, useful for
  high-iteration framework experiments)

we need a multi-provider harness with the same tool semantics as
CC (Read, Write, Edit, Bash, Glob, Grep). Building those tools
from scratch on top of LiteLLM or ADK is an engineering project of
its own (200-500 LOC plus safety review). The cheaper path is to
wrap the open-source [opencode](https://github.com/sst/opencode)
CLI, which already implements those tools with semantics close
enough to CC that our existing prompts should work.

## Scope

Build `OpenCodeHarness` mirroring the shape of `ClaudeCodeHarness`,
plumb it through the digital flow, and prove parity on at least one
S11 task and one S12-A task.

### Out of scope

* Building filesystem tools on top of ADK/LiteLLM directly. That
  is a separate handoff (S12-E or later) and is the right answer
  if we ever decide to dogfood our own tool dispatch instead of
  depending on opencode.
* Authoring opencode itself or contributing patches upstream.
* Switching the default driver away from CC. CC stays the
  reference; opencode is an alternative.

## Approach

### opencode CLI surface

* Shell command: `opencode --print --output-format json -p "<prompt>"`.
* Model selection: `opencode --model <provider>/<model>` (e.g.
  `openrouter/google/gemini-flash-1.5-8b`,
  `anthropic/claude-haiku-4-5`, `ollama/qwen2.5-coder:32b`).
* JSON output shape (verify against current opencode docs before
  implementing):
  - `result_text`: final agent reply text.
  - `cost_usd`: total cost (may be 0 for local models).
  - `num_turns`: tool-call rounds.
  - `error`: present on failure.
* Tool surface today (verify): Read, Write, Edit, Bash, Glob,
  Grep, WebFetch — same names as CC, similar JSON schemas.

### `OpenCodeHarness` design

Mirror `ClaudeCodeHarness` field-for-field:

```python
@dataclass
class OpenCodeHarness:
    prompt: str
    work_dir: Path
    cli_path: str = "opencode"
    model: str | None = None         # provider/model form
    timeout_s: int = 3600
    max_budget_usd: float | None = None
    allow_dangerous: bool = False    # opencode equivalent flag TBD
    extra_env: dict[str, str] = field(default_factory=dict)

    async def run(self) -> HarnessResult: ...
```

`HarnessResult` is already shared (lives in `claude_code_harness.py`).
Reuse it verbatim so the loop / adapter / bench pieces don't change.

### Plumbing through the digital flow

`generate_rtl_draft` and `run_idea_to_rtl_loop` currently hard-wire
`ClaudeCodeHarness`. Add a thin abstraction:

```python
class HarnessFactory(Protocol):
    def __call__(self, *, prompt, work_dir, ...) -> AbstractHarness: ...

DEFAULT_HARNESS_FACTORY = ClaudeCodeHarness  # current behaviour
```

`generate_rtl_draft` gains an optional `harness_factory=None` kwarg.
When None, defaults to CC. When set, the loop / adapter just uses
whatever the factory returns. `IdeaToDigitalChipInputs` gains a
`harness: Literal["cc","opencode"] = "cc"` field; the bench adapter
selects the factory.

The MCP tool surface also gains the same kwarg; `--harness opencode`
on the CLI becomes a one-flag switch.

### Bench YAMLs

Add `bench/tasks/end-to-end/idea_to_digital_counter_opencode_*.yaml`
plus a Haiku-via-opencode and a Gemini-Flash-via-opencode pair.
Same designs as the S11 baseline so we can compare convergence and
cost head-to-head.

## Acceptance

1. `OpenCodeHarness` unit tests: 5+ tests covering subprocess invocation,
   JSON parsing, error propagation, timeout, model flag pass-through.
   Use the same mocking pattern as `tests/test_claude_code_harness.py`.
2. Integration: `e2e_idea_to_digital_counter_opencode_live` reaches
   GDS on counter (the cheapest live target) using
   `opencode --model anthropic/claude-haiku-4-5`. Expected cost:
   $0.05-0.20.
3. Multi-provider parity: same task with
   `opencode --model openrouter/google/gemini-flash-1.5-8b` reaches
   GDS or honest-fails with documented root cause. Expected cost:
   $0.005-0.05.
4. Loop integration: the Haiku FFT8 v3 reproduce target also runs
   under opencode (`s12d_opencode_haiku_fft8_v1`) and either
   converges with `converged_turn >= 1` or honest-fails. The
   acceptance is "loop machinery still works", not a particular
   converge depth.
5. Documentation: a `docs/opencode_harness.md` explaining when to
   pick CC vs opencode, model selection, environment requirements.

## Cost ceiling

$30 across all S12-D live spend. Gemini-Flash and Ollama probes
are essentially free; Anthropic via opencode behaves like the
Claude CLI directly. The headline budget item is implementation
time, not API spend.

## Risks

* **opencode tool semantics drift.** opencode is younger and
  evolving faster than the Claude CLI. Pin a specific version and
  add a smoke test that confirms `Read`/`Write`/`Edit`/`Bash` still
  behave identically before invoking the loop.
* **JSON output format differences.** Anything in `HarnessResult`
  that opencode doesn't emit (e.g. `model_usage` per-model token
  breakdown) will be empty. Tests must tolerate `None` for those
  fields.
* **opencode binary distribution.** It's a Bun-built single binary
  hosted via npm. Document the install path and the
  EDA_AGENTS_OPENCODE_PATH env var (mirror the existing
  `cli_path="claude"` pattern).
* **Subprocess permissions / sandboxing.** opencode's "dangerous"
  mode equivalent must be similarly double-gated. The S12-A
  agent-auto-commit incident (Haiku committed to the worktree git
  tree because `--dangerously-skip-permissions` left git
  unrestricted) means the opencode wrapper should ALSO ship a
  recommendation to run agents inside a non-git work_dir, or to
  block git from the agent's tool list.

## Critical files (reuse, do NOT duplicate)

* `src/eda_agents/agents/claude_code_harness.py` — `HarnessResult`,
  the env-var build pattern, the timeout / kill protocol, the
  cost-cap monitor. Borrow all of these.
* `src/eda_agents/agents/idea_to_rtl.py` — single-shot dispatch.
  Add factory hook here.
* `src/eda_agents/agents/idea_to_rtl_loop.py` — loop dispatch.
  Threaded factory through.
* `src/eda_agents/bench/adapter_inputs.py` — `harness` field.
* `src/eda_agents/bench/adapters.py` — adapter selects factory.
* `src/eda_agents/mcp/server.py` — `harness` MCP kwarg.

## Estimated effort

* Harness + tests: 0.5-1 day.
* Plumbing + bench YAMLs: 0.5 day.
* Live runs + evidence READMEs: 0.5 day.

Total: 1-2 sessions.

## Branch

Create `feat/s12d-opencode-wrapper` off main once S12-A merges.
Do NOT bundle with S12-A; S12-A is already a large PR.
