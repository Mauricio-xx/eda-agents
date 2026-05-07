# Prompt for the slides agent (copy-paste verbatim)

---

You are creating a short beamer slide deck (~6-10 frames) on the
eda-agents stack, focused on the digital autoresearch loop running
on GF180MCU with two interchangeable proposal backends: Claude Code
CLI (subscription) and opencode CLI (any provider, currently
`openai/gpt-5.3-codex`).

The repo is at `/home/montanares/personal_exp/eda-agents/`.

## Read this brief first (single source of truth)

`/home/montanares/personal_exp/eda-agents/docs/brief/autoresearch_gf180_cc_opencode_slides.md`

That file is your work order. It contains:

- Deck objective and suggested section list.
- Existing companion decks to mirror in style:
  `tutorials/agents-analog-digital/main.tex` and
  `tutorials/rtl2gds-gf180-docker/main.tex`. Reuse the beamer +
  tikz pattern, `aspectratio=169`, `\note{...}` blocks for speaker
  notes, `make` / `make notes` targets.
- ~30 commit SHAs grouped by theme (autoresearch core arc, harness
  family, idea-to-chip, toolchain hardening, today's uncommitted
  patches). Run `git show <sha>` on any of them — the SHA is
  authoritative; the one-line hints in the brief are just an index.
- Key files to read with line numbers (`_propose_params:504`,
  `_propose_params_via_cc_cli:608`, `_propose_params_via_opencode:651`,
  `_build_harness:326`, `_find_post_synth_netlist`, etc.).
- Three diagrams to draw (verbal sketches): idea_to_optimize chain,
  backend dispatch in `_propose_params`, the greedy loop.
- A one-slide list of five real engineering gotchas hit live today
  with the framework patches that unblocked them. Use these in a
  "what we learned" slide — don't sugarcoat, don't dramatize.
- An explicit "what NOT to put in the deck" list.

## Suggested deck location

`tutorials/autoresearch-gf180-backends/` with `main.tex`,
`preamble.tex`, `Makefile`, and `sections/*.tex`. Mirror the
sibling decks. Don't scaffold a giant infra; one clean `main.tex`
with sections inline is fine if you are short on time.

## Working agreement

- Read the brief, the existing companion decks, and the SHAs the
  brief points at *before* drafting the first slide.
- Do not duplicate `agents-analog-digital/sections/04-autoresearch.tex`
  whole. Reuse its tikz greedy-loop figure as a recap, then extend
  the discussion to the digital flow + GF180 specifics + backend
  dispatch — that is the *new* material this deck is for.
- Build with the same Makefile pattern as the sibling decks:
  `make` produces audience PDF, `make notes` produces speaker view.
- Author all prose in **English**, even if user-facing comms in this
  workspace are bilingual. The other tutorials are English; the new
  deck must match.
- Heavy material goes in `\note{...}` blocks. Slides themselves stay
  schematic.
- No emojis. No em-dashes (use comma / period / parentheses).
- Direct, scientific tone. No marketing voice. Engineering honest.

## Live data and what to do if it isn't ready

There are two live runs under
`/home/montanares/i2o_claude_demo/` and
`/home/montanares/i2o_opencode_demo/` that may produce comparative
metrics in `phase_optimize/results.tsv` and PNG plots under
`phase_optimize/`. These runs are still being debugged at the time
you start; do *not* attempt to launch them yourself, do *not*
read or write anything inside those workdirs to avoid colliding
with the parent session that is actively iterating on the
toolchain.

If `phase_optimize/results.tsv` exists with `valid=True` rows by the
time you build the deck, cite the numbers (best FoM, kept count,
chosen params) verbatim. If not, present the experimental design
as the proof point and label the comparison "smoke-budget A/B,
real comparison reserved for a follow-up". The brief explains both
paths.

## Hands-off rules (so you don't collide with the parent debugging)

- Do NOT run any of the live `examples/11_idea_to_chip_demo_gf180.py`
  invocations. The parent session owns those. You will not be
  notified about their state changes.
- Do NOT modify any file under
  `src/eda_agents/`, `tests/`, `examples/`, or under the live
  workdirs (`/home/montanares/i2o_claude_demo/`,
  `/home/montanares/i2o_opencode_demo/`). The parent session has
  uncommitted patches in flight; you would race with it.
- Do NOT commit or push anything in this repo. The parent session
  will batch commits at the end.
- Stay inside `tutorials/autoresearch-gf180-backends/` and the
  existing tutorial directories you reference. Read other parts of
  the repo freely for context (architecture, code, prior decks),
  but write only inside your deck directory.
- Respect `~/.claude/CLAUDE.md` and the project `CLAUDE.md` rules
  loaded at the top of your session.

## Deliverable

A working `main.pdf` (audience) and `main-with-notes.pdf` (speaker)
under `tutorials/autoresearch-gf180-backends/`, plus the source
`.tex` files and a `Makefile`. Brief README in the deck directory
covering the build command. That's it — no PR, no commit, no push.

When you finish, post a one-paragraph summary to the user that
includes (a) the path to `main.pdf`, (b) the section titles and
slide count, (c) any blockers you hit, and (d) any place where you
deliberately deviated from the brief and why.

If you are uncertain whether to include a specific commit, file,
or talking point, default to the brief's structure. The brief was
written to be the canonical scope; deviations should be small.

---

Start now.
