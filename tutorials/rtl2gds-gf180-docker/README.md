# RTL-to-GDS on GF180MCU: LibreLane + Docker (short deck)

A compact Beamer presentation showing how to take a Verilog design to a
manufacturable GDSII layout on the GF180MCU open PDK, using the
IIC-OSIC-TOOLS Docker image and LibreLane.

Audience: first-time users of open-source IC design flows. No prior
experience with RTL-to-GDS, Docker, or EDA tools is assumed.

The deck is deliberately lean (~25 content frames across 6 sections).
Every content frame carries a `\note{...}` with deeper speaker context --
an expert designer can import `slides + notes` into a polished deck with
animations later. See the `notes` Make target for the speaker build.

A companion Jupyter notebook ships a hands-on, tiny counter through the
same flow in 8 cells: `demo/rtl2gds_counter.ipynb`.

A sibling deck introduces the eda-agents AI-driven workflow for analog
and digital blocks: `../agents-analog-digital/`.

## Building the PDF

### Option A -- local texlive install

Requires a reasonably full texlive:

- `texlive-latex-base`, `texlive-latex-recommended`, `texlive-latex-extra`
- `texlive-pictures` (TikZ)
- `texlive-fonts-recommended`
- The `beamertheme-metropolis` package (ships with most modern texlive
  distributions; `texlive-latex-extra` on Debian/Ubuntu)
- `latexmk`

Then:

```bash
make          # main.pdf            (audience view, notes hidden)
make notes    # main-with-notes.pdf (slide + notes right-hand)
make clean    # remove auxiliary files
make distclean
```

If `beamertheme-metropolis` is not available, edit `preamble.tex` and
swap the `\usetheme{metropolis}` line for:

```latex
\usetheme{default}\usecolortheme{beaver}
```

### Option B -- build with a throw-away texlive container

No install needed on the host. You just need Docker:

```bash
make docker          # audience PDF
make docker-notes    # speaker PDF
```

This pulls `texlive/texlive:latest` (the upstream image), mounts the
tutorial directory, and runs `latexmk`.

## Manual screenshots

The slide source contains `%TODO-SCREENSHOT:` comments (in `.tex`
sources -- grep for them) marking every spot where a screenshot should
be dropped. Capture each PNG and save it to `slides/figures/` with the
filename referenced in the slide. If the file is missing, the PDF shows
a labelled placeholder box, so the deck still builds.

Checklist (matches file names used in `sections/`):

| File                                   | What to capture                                                          |
|----------------------------------------|--------------------------------------------------------------------------|
| `final_layout_render.png`              | PNG from `final/render/chip_top.png` (KLayout auto-render).              |
| `librelane_run_output.png`             | Tail of a successful `make librelane` run showing final banner.          |
| `floorplan.png`                        | OpenROAD GUI after floorplan: die + padring + SRAMs (empty boxes).       |
| `pdn.png`                              | OpenROAD GUI with Metal2/3 visible, showing PDN straps and core ring.    |

## File layout

```
main.tex                 -- audience deck
main-with-notes.tex      -- speaker deck (loads main.tex w/ notes=right)
preamble.tex             -- packages, colors, TikZ styles, listings
sections/
  01-pipeline.tex                 -- what RTL->GDS means, final artifacts
  02-stack-pdk-container.tex      -- LibreLane as orchestrator + Docker + PDK
  03-librelane-config.tex         -- config.yaml / slot_*.yaml anatomy
  04-run-the-flow.tex             -- golden-path command + phase overview
  05-flow-stages.tex              -- per-phase deep dive (synth ... signoff)
  06-results-pitfalls-next.tex    -- metrics, signoff table, 4 pitfalls, next
slides/figures/          -- PNG screenshots (manual, drop them in here)
demo/                    -- Jupyter notebook + Python mirror (minimal counter)
Makefile
README.md
```

## Source of authority

All commands, metrics, and error messages in these slides are quoted from:

- `docs/iic_osic_tools_audit.md` (in the `eda-agents` repo) -- audit report
  of `hpretl/iic-osic-tools:next` that produced the reference run on
  2026-04-13.
- `final/metrics.csv` from that reference run.

Do not invent new numbers without re-running the flow.

## Related decks

- `../agents-analog-digital/` -- short Beamer deck introducing the
  eda-agents stack: analog sizing, digital RTL-to-GDS agent hierarchy,
  autoresearch loop, MCP tooling.
