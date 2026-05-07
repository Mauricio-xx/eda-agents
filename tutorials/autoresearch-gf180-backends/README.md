# autoresearch-gf180-backends

Short companion deck (~9 frames) on the eda-agents autoresearch loop applied to the digital flow on GF180MCU, with two interchangeable proposal backends: Claude Code CLI (subscription, Opus 4.7) and opencode CLI (any provider, e.g. `openai/gpt-5.3-codex`). Sibling of `tutorials/agents-analog-digital/` and `tutorials/rtl2gds-gf180-docker/`; this deck assumes the architecture refresher and analog loop coverage from the analog-digital deck and only extends the new digital + backend-dispatch material. The brief at `docs/brief/autoresearch_gf180_cc_opencode_slides.md` is the canonical scope.

## Build

```
make            # main.pdf            (audience view, 9 frames)
make notes      # main-with-notes.pdf (speaker view, dual-screen)
make chipathon  # main-chipathon.pdf + chipathon-slide-1.png .. chipathon-slide-3.png
make clean      # remove latex aux files
make distclean  # also delete generated PDFs and PNGs
```

The Makefile runs `pdflatex` twice per target to resolve table-of-contents references. `latexmk` is not required. PNG export uses `pdftoppm -r 300` so the rasterised slides remain crisp inside PowerPoint at 16:9. The Chipathon export is gated by `\ifchipathon` in `preamble.tex`; the audience deck (`main.pdf`) excludes those frames.

## Sections

`sections/01-context.tex` places autoresearch inside the wider eda-agents stack. `sections/02-loop-applied.tex` covers the greedy loop on the digital flow plus the GF180 FoM formula. `sections/03-idea-to-optimize.tex` shows the `idea_loop` to `autoresearch` two-phase chain. `sections/04-backends.tex` is the dispatcher diagram and the `f1b6d13` bug story. `sections/05-live-ab.tex` lays out the GF180 A/B (design-only by default; numbers slot in later). `sections/06-gotchas.tex` is the live-debug index. `sections/99-chipathon.tex` carries the three Chipathon-supplement frames.

## Live data

Two parallel runs stage outputs at `~/i2o_claude_demo/idea_to_optimize/` (cc_cli, Opus 4.7) and `~/i2o_opencode_demo/idea_to_optimize/` (opencode, `openai/gpt-5.3-codex`). Both produce `phase_optimize/results.tsv` and `plots/phase_optimize/{fom_evolution,metrics_grid,params_evolution}.png`. The deck does not enter those workdirs (the parent debug session owns them). When the runs close, the A/B frame can be patched with a two-line numeric table without re-laying-out the slide.

## Demo video

A 90-120 s replay walk-through is planned. The shot list is in the project plan at `~/.claude/plans/prompt-for-the-temporal-glacier.md`. Inputs: the two `i2o_*_demo` workdirs, plus the deck PDF for intro/outro cuts. Recording is done after the parent runs land; nothing is captured live during deck authoring.
