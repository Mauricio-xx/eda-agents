# Review pass 2 — re-render after critique

Re-rendered both decks after applying the pass-1 fixes. Outputs:
`render/analog_v2.pdf`, `render/digital_v2.pdf`, per-page PNGs at
`render/analog_v2/*.png` and `render/digital_v2/*.png`.

Page count: **42 + 42** (unchanged).

## Critical fixes — verified

| Deck · slide                              | Before                                   | After                                                              |
| ----------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------ |
| analog 6 (`gm/ID methodology`)            | Left column dark-on-dark, unreadable     | Full stage palette — bullets + paragraph legible                   |
| analog 13 (`Advisor output`)              | Right cards dark-on-dark, bullets hidden | Full stage palette — "WHAT JUST HAPPENED" + "FIRST SPICE" legible  |
| digital 21 (`OpenCodeHarness` plumbing)   | Boxes + labels overlapping               | Clean single-row flow; provider grid separated below dashed line   |
| analog 29 (`Resumable runs`)              | Persistence labels clipped at left edge  | Labels moved to right side of band, fully visible                  |

Root cause of the first two: `deck.css` had `.slide-stage X` palette
overrides but not `.slide-stage-grid X`, so grid-background stage slides
inherited paper-ink on their text. Fix: extend the selectors to cover
both. That single CSS change fixed slides 6 and 13 in one edit.

Root cause of digital 21: the SVG had its `viewBox` too short
(420 px) to hold the provider dropdown + caption below the main flow,
so the dropdown collided with the flow boxes and the NDJSON label sat
inside the box boundaries. Rebuilt the SVG with `viewBox=0 0 1600 580`,
row labels up top, a clean dashed divider between flow and provider
grid, and the caption at the bottom with breathing room.

## Moderate fixes — verified

- digital 14–17 (four specialist slides): added a uniform
  **LibreLane-phase ribbon** at the bottom. Each specialist has its
  phase(s) highlighted (RTL/TB → verification; Synth → synthesis;
  Floor/PDN/Place/CTS/Route → physical; Signoff → signoff). This
  turns the previously empty bottom halves into a visual cheat-sheet
  for which specialist owns which LibreLane phase. CSS for the ribbon
  lives inline in `digital.html` under `<style>`, so it does not
  affect the analog deck.

## Not touched this pass (explicit scope deferral)

- **Analog 22 (AnalogAcademy OTA) + 23 (GF180 OTA) sparseness**:
  accepted as-is; merging them would break the "one topology per
  registry entry" narrative; the empty bottom half is a pacing beat.
- **Namespace slides (analog 32, digital 33) text-list sparseness**:
  accepted as-is; the empty bottom is intentional because the namespace
  is *small* (10 and 5 skills) — filling with filler would reduce
  clarity.
- **Analog 42, digital 42 logo sizing**: cosmetic, not a blocker.

## Verdict

Pass 2 closes every critical legibility + overlap bug found in pass 1
and adds tangible information to the four previously empty-looking
specialist slides. Redundancy concerns (merge 14–17 into one slide,
merge 22+23) remain open by choice — both kept as separate slides for
pacing reasons, with the specialist slides now earning their real estate
via the new phase ribbon.
