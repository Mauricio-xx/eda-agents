# Designer review package

Everything you need to action the feedback on the redesigned
"RTL-to-GDS on GF180MCU — LibreLane + Docker" deck.

## Files (numbered in reading order)

| # | File | What it is | Why you need it |
|---|------|------------|-----------------|
| 01 | `01_feedback.md` | Prioritised review (TL;DR + A/B/C priorities + per-page table). | **Start here.** All other files are referenced from this one. |
| 02 | `02_reference_deck.pdf` | Current author's Beamer deck (audience view, 39 pages). | Canonical source for the section-6 content that was replaced, the colour legend, and the per-stage callouts. |
| 03 | `03_reference_deck_with_notes.pdf` | Same deck, notes on the right half of every page. | The speaker-narrative that belongs on each slide — use as context when replicating content. |
| 04 | `04_speaker_notes.md` | Speaker notes extracted to plain Markdown, one block per slide with a link to its PNG. | Easy to search/copy without opening the PDF. |
| 05 | `05_chip_top_full_chip_render.png` | KLayout render of the full-chip reference run: `chip_top.gds`, 3.93x5.12 mm die, padring + 2 SRAMs + logic core, 1000x1302 px. Metrics: 251 615 instances, 34.3 mW, 0 DRC/LVS/setup/hold vios — the numbers the deck quotes in the Headline section. | **Use for the audience p.8 placeholder (`final_layout_render.png`).** This matches the original caption intent exactly. |
| 06 | `06_slots_frame_source.tex` | LaTeX source of the new "Slots: a wafer-space convention, not a LibreLane one" frame. | The one pedagogical addition not in your snapshot. Drop it between the "Two YAML files" divider and the `slot_1x1.yaml` frame. |
| 07 | `07_counter_bare_block_render.png` | Optional secondary render: the counter from the companion notebook (GF180 Classic flow, 300x300 um, bare block, no padring). | Include only if you want a visual contrast between full-chip vs bare-macro — otherwise skip. The full-chip render in file 05 is the canonical p.8 image. |

## Reading path

1. **`01_feedback.md`** — TL;DR + priorities.
2. **`02_reference_deck.pdf`** — pages 33 onwards for the section-6
   content that must be restored.
3. **`06_slots_frame_source.tex`** — the content of the new frame
   (priority A2).
4. **`05_chip_top_full_chip_render.png`** — drop into p.8 as the
   `final_layout_render.png` image. Caption stays as originally
   intended ("Final GDS rendered by KLayout").
5. **`07_counter_bare_block_render.png`** — optional; use only if you
   want to contrast full-chip vs bare-macro in the deck.

## What is NOT in this package

- The individual slide PNGs of the reference deck (~2 MB, 39 files).
  If you want side-by-side comparison, ask; we can generate a zip.
- The companion notebook `demo/rtl2gds_counter.ipynb` (self-contained,
  runs on any machine with Docker). Not required for visual-design
  work; ask if you want it for reference.
- The project's LaTeX source tree (sections/\*.tex + preamble.tex).
  Ask if you want it to lift any stylistic detail verbatim.

## Questions to confirm with the author before you ship

1. The **"OpenSilicon Labs / /dev/silicon" branding** on the cover and
   the closing-slide footer: keep, replace, or drop?
2. We shipped you two renders: file 05 (full chip, canonical for
   p.8) and file 07 (bare counter, optional contrast image). Any
   reason to use anything other than file 05 on p.8?
3. The **"seven-slide Section 6"** (headline numbers + signoff table
   + metrics grep + four pitfalls + what-to-try-next + companion
   notebook + further reading) must replace your current Part 06
   "From one chip to a shuttle" sequence. The Shuttle CI slide (your
   p.34) is good enough to keep as an appendix — confirm whether the
   author wants that kept or removed.
