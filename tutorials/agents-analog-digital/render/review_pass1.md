# Review pass 1 — analog.html + digital.html

Rendered 42+42 slides via `google-chrome --headless --print-to-pdf` on the
`-print.html` variants, exploded to PNG at 90 DPI with `pdftoppm`.

## Critical — blockers for legibility

1. **analog slide 6 (`gm/ID — the one-slide methodology`)**: left column
   (intro paragraph + 4 bullets about high/low gm/ID + 1-D slider) is
   rendered in dark paper-ink on dark stage background — essentially
   invisible. Paper styles leak into a stage layout.
2. **analog slide 13 (`Advisor output — first-order sized, no SPICE yet`)**:
   right "WHAT JUST HAPPENED" callout has dark-on-dark bullets. Same
   palette-leakage bug as slide 6.
3. **digital slide 21 (`OpenCodeHarness — the plumbing`)**: SVG has
   multiple overlapping labels. `JSON EVENT STREAM` label overlaps the
   `_parse_events` box; the `-m <PROVIDER/MODEL>` dropdown overlaps the
   Python caller and opencode-run boxes; the source-file caption text
   cross-hatches with the dropdown rows.

## Moderate — density and redundancy

4. **digital slides 14–17 (four specialist slides:
   verification_engineer / synthesis_engineer / physical_designer /
   signoff_checker)**: four near-identical layouts (TOOLS block left,
   bullets right) each with a large empty bottom half. Wasted real
   estate; nothing visualizes *which part of LibreLane each specialist
   owns*. Opportunity: add a mini 7-phase timeline footer per slide
   highlighting the phase(s) that specialist touches.
5. **analog slide 32 (`analog.* namespace`) + digital slide 33
   (`digital.* namespace`)**: both are pure text lists of skill names.
   Empty lower half. Opportunity: add an agent×skill matrix below the
   list to make runtime-discovery concrete.
6. **analog slides 22 (`AnalogAcademy OTA`) + 23 (`GF180 OTA`)**: both
   sparse — 3 bullets + one callout each. Could merge or add a small
   visual differentiator (NMOS vs PMOS input pair).

## Minor

7. **analog slide 21 (`Miller OTA` schematic)**: `M3` label box overlaps
   the transistor rectangle; `Cc = 2.1pF` label clips `M4` wire.
8. **analog slide 29 (`Resumable runs`)**: `program.md` and `results.tsv`
   labels on the left are slightly cut off by the slide edge.
9. **closing slides (analog 42, digital 42)**: OpenSilicon bottom-right
   logo is tiny and low-contrast; inconsistent sizing vs the rest of
   the decks. Minor, not a blocker.

## What's already good — keep as is

- Title slides (slide 1), section dividers, agenda slides (3).
- Code-heavy slides (JSON output 11, CircuitTopology 17, Miller OTA
  schematic layout 21 aside from label nit).
- Autoresearch 6-node circular diagram (analog 26).
- ADK tree diagram (digital 13).
- cocotb-3-sim verdict flow (digital 10).
- LibreLane orchestrator hub (digital 6).
- Docker-driver pattern (analog 15, digital 11).
- Budget-sizing + corner-validator tables (analog 28, 30).
- Runtime comparison table (digital 31).
- Resume timeline concept (analog 29 — apart from the clipping).

## Priority for pass 1 fixes

1. Stage-palette fix on analog 6 + analog 13 (blocker).
2. OpenCodeHarness SVG rebuild on digital 21 (blocker).
3. Specialist slides 14–17: add LibreLane-phase mini-timeline footer.
4. Namespace slides (analog 32 + digital 33): add agent×skill matrix.
5. Label-cropping fixes on analog 21 + 29 (minor).
